import asyncio
import os
import subprocess
import time
from hmac import compare_digest
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, Cookie, Depends, Body, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
from docker_manager import DockerManager
from recipes import (
    SERVER_RECIPES, SERVER_TEMPLATES,
    create_from_template, create_custom_recipe, delete_user_recipe,
    get_template_for_image, CF_API_KEY, save_user_recipe,
)
from stats_fetcher import StatsFetcher
import urllib.request
import urllib.error
import urllib.parse
import httpx
import psutil
import traceback
import json
import re
import socket
from datetime import datetime

#Load env from the backend working directory with no sudo or systemd drop-in
load_dotenv()

#Secrets come from env only, empty fallback is a safe default, if env is missing nobody can authenticate, production must set these in env
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
GUEST_TOKEN = os.environ.get("GUEST_TOKEN", "")
FRP_SERVER_ADDR = os.environ.get("FRP_SERVER_ADDR", "")
FRP_TOKEN = os.environ.get("FRP_TOKEN", "")
FACTORIO_USERNAME = os.environ.get("FACTORIO_USERNAME", "")
FACTORIO_TOKEN = os.environ.get("FACTORIO_TOKEN", "")
SATELLITE_HOSTS = {"ssh://lunkman@<TAILSCALE_IP>"}

#Recipes reads CF API KEY at import time before load dotenv runs, reread it here to pick up the env value, patch both the module attribute and the local import binding
import recipes as _recipes
_recipes.CF_API_KEY = os.environ.get("CF_API_KEY", "")
CF_API_KEY = _recipes.CF_API_KEY

#Docker container stats cache populated by the background task
STATS_CACHE = {}

#Disk usage cache keyed by server id, du on large dirs can take seconds, 30s TTL keeps the page fast
_DISK_CACHE = {}
_DISK_TTL = 30

def get_server_disk_gb(server_id):
    """Cached du of ~/Documents/server_data/{id}/ in GB."""
    now = time.time()
    cached = _DISK_CACHE.get(server_id)
    if cached and (now - cached[0]) < _DISK_TTL:
        return cached[1]
    data_dir = os.path.expanduser(f"~/Documents/server_data/{server_id}")
    if not os.path.isdir(data_dir):
        return 0.0
    try:
        result = subprocess.run(
            ["du", "-sb", data_dir], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            gb = round(int(result.stdout.split()[0]) / (1024 ** 3), 2)
            _DISK_CACHE[server_id] = (now, gb)
            return gb
    except Exception:
        pass
    #ponytail du failed or timed out, return last known or zero
    return cached[1] if cached else 0.0

def get_fleet_allocation():
    """Sum of RAM limits across running local servers vs host total."""
    total_ram_gb = psutil.virtual_memory().total / (1024 ** 3)
    allocated_gb = 0.0
    running_count = 0
    for server_id, recipe in SERVER_RECIPES.items():
        if recipe.get("remote_host"):
            continue
        status = STATS_CACHE.get(server_id, {}).get("status")
        if status not in ("running", "restarting"):
            continue
        ram_str = recipe.get("ram_limit", "0g")
        allocated_gb += parse_ram_to_gb(ram_str)
        running_count += 1
    return {
        "total_ram_gb": round(total_ram_gb, 1),
        "allocated_gb": round(allocated_gb, 1),
        "running_local": running_count,
    }

#Short TTL cache for psutil cpu percent so concurrent API callers do not reset each others baseline and get zero percent back
_cpu_cache = {"value": 0.0, "ts": 0.0}
_CPU_CACHE_TTL = 0.8  #seconds

def get_cpu_percent():
    """Thread-safe local CPU reading that doesn't suffer from psutil's
    global-timestamp race when called by multiple endpoints at once."""
    now = time.time()
    if now - _cpu_cache["ts"] < _CPU_CACHE_TTL:
        return _cpu_cache["value"]
    val = psutil.cpu_percent(interval=None)
    if val == 0.0 and now - _cpu_cache["ts"] < 2.0:
        #Psutil returned zero because another caller reset the baseline, return the last real reading instead
        return _cpu_cache["value"]
    _cpu_cache["value"] = val
    _cpu_cache["ts"] = now
    return val

#Docker and stats managers
docker_mgr = DockerManager()
stats_fetcher = StatsFetcher()

def get_current_role(auth_token: str = Cookie(None), authorization: str = Header(None)):
    #Support bearer token for service to service auth like the Discord bot
    bearer_token = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:].strip()
    token = auth_token or bearer_token
    #Compare digest prevents timing attacks on token comparison
    if token and compare_digest(token, ADMIN_TOKEN):
        return "admin"
    if token and compare_digest(token, GUEST_TOKEN):
        return "guest"
    raise HTTPException(status_code=401, detail="Unauthorized. Nice try.")

def require_admin(role: str = Depends(get_current_role)):
    if role != "admin":
        raise HTTPException(status_code=403, detail="Guests cannot perform this action.")
    return role

def _ssh_compose(compose_file: str, remote_host: str, subcmd: str):
    """Run 'docker compose <subcmd>' on a remote host via SSH.
    Raises RuntimeError on non-zero exit."""
    ssh_target = remote_host.replace("ssh://", "")
    #Alias map from Tailscale IP to SSH host alias in ssh config
    alias_map = {"<TAILSCALE_IP>": "satellite-1"}
    ssh_target = alias_map.get(ssh_target, ssh_target)
    cmd = f"cd {os.path.dirname(compose_file)} && docker compose {subcmd}"
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
         ssh_target, cmd],
        capture_output=True, text=True, timeout=30
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout.strip()

def _compose_group(server_ids: list, subcmd: str, label: str):
    """If the group shares a compose file, run one docker compose command.
    Returns results list, or None if not a compose group (caller falls
    back to individual container ops)."""
    compose_file = remote_host = None
    for sid in server_ids:
        recipe = SERVER_RECIPES.get(sid, {})
        if recipe.get("compose_file"):
            compose_file = recipe["compose_file"]
            remote_host = recipe.get("remote_host")
    if not (compose_file and remote_host):
        return None
    try:
        out = _ssh_compose(compose_file, remote_host, subcmd)
        msg = out if out else f"Compose group {label}"
        return [{"server_id": sid, "status": "success", "message": msg} for sid in server_ids]
    except Exception as e:
        return [{"server_id": sid, "status": "error", "message": str(e)} for sid in server_ids]

def update_frpc_tunnel():
    """Rebuild and hot-reload of FRP tunnel config."""
    running_servers = [s_id for s_id, stats in STATS_CACHE.items() if stats.get("status") in ["running", "restarting"]]

    toml_content = f"""serverAddr = "{FRP_SERVER_ADDR}"
serverPort = 7000
auth.method = "token"
auth.token = "{FRP_TOKEN}"

[webServer]
addr = "127.0.0.1"
port = 7400

[[proxies]]
name = "lunk-api-bridge"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8000
remotePort = 8000
"""

    unique_proxy_blocks = set()
    for server_id, recipe in SERVER_RECIPES.items():
        if server_id not in running_servers:
            continue
        #Do not auto tunnel auto discovered containers
        if recipe.get("_auto"):
            continue
        target_ip = "127.0.0.1"
        remote_url = recipe.get("remote_host")
        if remote_url:
            try:
                target_ip = remote_url.split('@')[1]
            except IndexError:
                pass
        ports = recipe.get("ports", {})
        for container_port_map, host_port in ports.items():
            protocol = container_port_map.split('/')[1]
            block = f"""
[[proxies]]
name = "{server_id}_{protocol}_{host_port}"
type = "{protocol}"
localIP = "{target_ip}"
localPort = {host_port}
remotePort = {host_port}
"""
            unique_proxy_blocks.add(block)

    for block in unique_proxy_blocks:
        toml_content += block

    frp_dir = os.path.abspath("/etc/frp/")
    config_path = os.path.join(frp_dir, "frpc.toml")
    try:
        os.makedirs(frp_dir, exist_ok=True)
        with open(config_path, "w") as config_file:
            config_file.write(toml_content)
    except PermissionError:
        print(f"[File Error] No permission to write to {config_path}.")
        return

    try:
        req = urllib.request.Request("http://127.0.0.1:7400/api/reload", method="GET")
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                print(f"[Network] API reloaded! Active: {', '.join(running_servers)}")
    except Exception as e:
        print(f"[Network Error] Reload failed: {e}")

async def poll_docker_stats():
    """Background task: poll Docker stats every 1.5s without blocking the API."""
    while True:
        for server_id, recipe in SERVER_RECIPES.items():
            try:
                stats = await asyncio.to_thread(docker_mgr.get_container_stats, server_id)
                stats["game_type"] = recipe.get("game_type", "unknown")
                stats["game_version"] = recipe.get("game_version", "unknown")
                STATS_CACHE[server_id] = stats
            except Exception as e:
                print(f"Error polling {server_id}: {e}")
        await asyncio.sleep(1.5)

async def automated_backup_scheduler():
    """Background scheduler: hourly Minecraft backups (keep 6), daily app backups (keep 7)."""
    print("Backup engine warming up... waiting 15 seconds for live server stats.")
    await asyncio.sleep(15)
    last_mc_backup_hour = -1
    last_daily_backup_date = None

    while True:
        try:
            now = datetime.now()
            #Hourly Minecraft backups
            if now.hour != last_mc_backup_hour:
                last_mc_backup_hour = now.hour
                for server_id, recipe in SERVER_RECIPES.items():
                    if recipe.get("game_type") == "minecraft":
                        if STATS_CACHE.get(server_id, {}).get("status") != "running":
                            continue
                        try:
                            print(f"[{now.strftime('%H:%M:%S')}] Actively backing up: {server_id}...")
                            await asyncio.to_thread(docker_mgr.create_snapshot, server_id, recipe, retention_limit=6)
                        except Exception as e:
                            print(f"[{now.strftime('%H:%M:%S')}] Failed to backup {server_id}: {e}")
                print(f"[{now.strftime('%H:%M:%S')}] Automated Minecraft backups complete.")

            #Daily app backups at 5 AM
            if now.hour == 5 and last_daily_backup_date != now.date():
                last_daily_backup_date = now.date()
                for server_id, recipe in SERVER_RECIPES.items():
                    if server_id in ["jellyfin", "wizarr", "wizarrd", "adguardhome"]:
                        if STATS_CACHE.get(server_id, {}).get("status") != "running":
                            continue
                        try:
                            print(f"[{now.strftime('%H:%M:%S')}] Actively backing up: {server_id}...")
                            await asyncio.to_thread(docker_mgr.create_snapshot, server_id, recipe, retention_limit=7)
                        except Exception as e:
                            print(f"[{now.strftime('%H:%M:%S')}] Failed to backup {server_id}: {e}")
                print(f"[{now.strftime('%H:%M:%S')}] Automated Daily App backups complete.")

        except Exception as e:
            print(f"CRITICAL: Background scheduler encountered a fatal error: {e}")
        await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    stat_task = asyncio.create_task(poll_docker_stats())
    backup_task = asyncio.create_task(automated_backup_scheduler())
    yield
    stat_task.cancel()
    backup_task.cancel()

class CommandPayload(BaseModel):
    command: str

class ModInstallPayload(BaseModel):
    download_url: str
    filename: str

class CreateServerPayload(BaseModel):
    server_id: str
    name: str = ""
    template: str = ""          #Template key like factorio or custom
    image: str = ""             #For custom mode or overriding template image
    description: str = ""
    #Manual mode fields ignored when template is not custom
    ports: dict = {}
    container_path: str = "/data"
    client_port: int | None = None
    env: dict = {}
    ram_limit: str = "0g"
    start_now: bool = True
    #Game specific options for the creation wizard
    game_options: dict = {}     #Factorio uses map gen preset and mods, Minecraft uses server type and version and modpack url

def get_recipe(server_id: str):
    """Get server recipe or raise 404."""
    recipe = SERVER_RECIPES.get(server_id)
    if not recipe:
        raise HTTPException(status_code=404, detail=f"Server ID {server_id} not found in configuration.")
    return recipe

def parse_ram_to_gb(ram_string: str) -> float:
    """Convert Docker memory strings (e.g., '4g', '512mb') to GB float."""
    if not ram_string:
        return 0.0
    ram_string = ram_string.lower()
    num_str = "".join(c for c in ram_string if c.isdigit() or c == '.')
    if not num_str:
        return 0.0
    try:
        val = float(num_str)
        if 'g' in ram_string:
            return round(val, 2)
        elif 'm' in ram_string:
            return round(val / 1024, 2)
    except ValueError:
        pass
    return 0.0

app = FastAPI(lifespan=lifespan)

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://<DOMAIN>",
    "http://<VPS_IP>:8080",
    "http://<VPS_IP>:8000"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/servers")
def get_all_servers():
    """Lightweight map of all server statuses for the sidebar."""
    return docker_mgr.get_all_statuses(list(SERVER_RECIPES.keys()))

@app.get("/api/bot/status")
def get_bot_status():
    """Structured fleet status for the Discord bot. Returns one container
    per entry with normalized state, name, type, and host label. No auth
    on statuses (same as /api/servers); the bot carries its own token for
    mutation endpoints only."""
    raw = docker_mgr.get_all_statuses(list(SERVER_RECIPES.keys()))
    result = []
    for sid, raw_status in raw.items():
        recipe = SERVER_RECIPES.get(sid, {})
        #Normalize the docker status string into a state token
        s = str(raw_status).lower()
        if "up" in s or s == "running":
            state = "running"
        elif "restart" in s:
            state = "restarting"
        elif s in ("exited", "stopped", "created", "dead", "paused"):
            state = "stopped"
        elif s == "unknown":
            state = "unknown"
        else:
            state = "stopped"
        host = recipe.get("remote_host", "local")
        if not host or host == "local":
            host_label = "Lunkserver 2.0"
        else:
            host_label = recipe.get("alias") or host.split("@")[-1]
        result.append({
            "id": sid,
            "name": recipe.get("name") or sid,
            "game_type": recipe.get("game_type", "unknown"),
            "state": state,
            "raw_status": str(raw_status),
            "host": host_label,
        })
    return {"servers": result}

#Server template management for the dashboard add and remove server pages

@app.get("/api/templates")
def get_templates():
    """Return the template catalog for the Add Server picker."""
    templates = []
    for key, t in SERVER_TEMPLATES.items():
        templates.append({
            "key": key,
            "label": t["label"],
            "category": t.get("category", "Other"),
            "image": t["image"],
            "ports": t["ports"],
            "client_port": t["client_port"],
            "container_path": t["container_path"],
            "game_type": t["game_type"],
            "ram_limit": t.get("ram_limit", "0g"),
            "env": t["env"],
            "version_options": t.get("version_options", []),
        })
    return {"templates": templates}

@app.get("/api/docker/search")
def search_docker_hub(query: str):
    """Proxy Docker Hub image search so the frontend can discover images."""
    if not query or len(query.strip()) < 2:
        raise HTTPException(status_code=400, detail="Query too short")
    try:
        url = f"https://hub.docker.com/v2/search/repositories/?query={urllib.parse.quote(query)}&page_size=12"
        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
        results = []
        for r in data.get("results", []):
            results.append({
                "name": r.get("repo_name", ""),
                "description": r.get("short_description", ""),
                "star_count": r.get("star_count", 0),
                "pull_count": r.get("pull_count", 0),
            })
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Docker Hub search failed: {e}")

@app.get("/api/minecraft/versions")
async def get_mc_versions():
    """Return Minecraft release versions from Mojang's version manifest.
    The dropdown loads these so the user picks from real versions instead
    of typing a version string."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://piston-meta.mojang.com/mc/game/version_manifest.json",
                timeout=15)
            resp.raise_for_status()
            data = resp.json()
        versions = [v["id"] for v in data.get("versions", [])
                    if v.get("type") == "release"]
        return {"versions": versions}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch versions: {e}")

@app.get("/api/factorio/versions")
async def get_factorio_versions():
    """Return the latest stable/experimental version numbers from factorio.com
    plus a list of recent Docker tags for the version dropdown."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://factorio.com/api/latest-releases", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            #Also fetch Docker tags for the version dropdown
            resp2 = await client.get(
                "https://hub.docker.com/v2/repositories/factoriotools/factorio/tags",
                params={"page_size": 50, "ordering": "last_updated"}, timeout=10)
            resp2.raise_for_status()
            tags_data = resp2.json()
        stable_ver = data.get("stable", {}).get("headless", "")
        exp_ver = data.get("experimental", {}).get("headless", "")
        #Extract unique version number tags, skip rootless and stable prefixes
        import re
        seen = set()
        versions = []
        for t in tags_data.get("results", []):
            name = t["name"]
            #Only pure version numbers like 2.0.77 and 2.1.12
            if re.match(r"^\d+\.\d+(\.\d+)?$", name) and name not in seen:
                seen.add(name)
                versions.append(name)
        return {
            "stable": stable_ver,
            "experimental": exp_ver,
            "all_versions": versions,
        }
    except Exception:
        return {"stable": "2.0", "experimental": "2.1", "all_versions": []}

@app.get("/api/minecraft/modpacks/search")
async def search_cf_modpacks(query: str, limit: int = 15, game_version: str = None):
    """Search CurseForge for Minecraft modpacks.
    Returns modpack name + the CF page URL that itzg/minecraft-server's
    AUTO_CURSEFORGE type consumes via the CF_PAGE_URL env var."""
    if not CF_API_KEY:
        raise HTTPException(status_code=500, detail="CF_API_KEY env var not set.")
    if not query.strip():
        return {"results": []}
    try:
        params = {
            "gameId": "432",           #Minecraft
            "classId": "4471",         #Modpacks class
            "searchFilter": query,
            "pageSize": min(limit, 25),
            "sortField": "2",          #Popularity
            "sortOrder": "desc",
        }
        #gameVersion filters server side on CurseForge, client side filtering does not work because search results omit it
        if game_version:
            params["gameVersion"] = game_version
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.curseforge.com/v1/mods/search",
                params=params,
                headers={"x-api-key": CF_API_KEY},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        results = []
        for m in data.get("data", []):
            results.append({
                "id": m.get("id"),
                "name": m.get("name", ""),
                "summary": m.get("summary", ""),
                "download_count": m.get("downloadCount", 0),
                "page_url": f"https://www.curseforge.com/minecraft/modpacks/{m.get('slug', '')}",
            })
        return {"results": results}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="CurseForge API error")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

def _download_modrinth_mods(server_id: str, slugs: list, game_version: str) -> list:
    """Download Fabric jars for a list of Modrinth project slugs into mods/.
    Filters by game_version. Returns list of installed filenames.
    Skips mods that already exist in mods/."""
    installed = set(m.lower() for m in docker_mgr.list_mods(server_id).get("mods", []))
    headers = {"User-Agent": "lunkman/lunkServerManager/1.0"}
    results = []
    with httpx.Client(timeout=15) as client:
        for slug in slugs:
            params = {"loaders": '["fabric"]'}
            if game_version:
                params["game_versions"] = f'["{game_version}"]'
            r = client.get(f"https://api.modrinth.com/v2/project/{slug}/version",
                           params=params, headers=headers)
            r.raise_for_status()
            versions = r.json()
            #Prefer release builds, fall back to beta since Geyser only ships betas
            releases = [v for v in versions if v.get("version_type") == "release"]
            if not releases:
                releases = versions
            if not releases:
                continue  #No version match, skip silently
            f = releases[0]["files"][0]
            if f["filename"].lower() in installed:
                continue  #Already present
            result = docker_mgr.install_mod(server_id, f["url"], f["filename"])
            if result["status"] == "error":
                raise HTTPException(status_code=500, detail=f"Failed to install {slug}: {result['message']}")
            results.append(f["filename"])
    return results

#Modrinth slugs for the one click Geyser and Floodgate Bedrock crossplay setup
GEYSER_MODS = ["fabric-api", "geyser", "floodgate"]
#Server side performance mods based on the working set from minecraft 01
OPTIMIZATION_MODS = ["fabric-api", "lithium", "ferrite-core", "krypton", "servercore"]

def _install_geyser_sync(server_id: str, game_version: str, recipe: dict, bedrock_port: int = 19132):
    """Download Fabric API + Geyser-Fabric + Floodgate-Fabric jars into mods/
    and add the Bedrock port to the recipe. Shared by the creation wizard and
    the standalone geyser-setup endpoint."""
    _download_modrinth_mods(server_id, GEYSER_MODS, game_version)
    #Add Bedrock port so Docker exposes it on next start
    port_key = f"{bedrock_port}/udp"
    if port_key not in recipe.get("ports", {}):
        recipe.setdefault("ports", {})[port_key] = bedrock_port
        save_user_recipe(server_id, recipe)

def _install_optimization_mods_sync(server_id: str, game_version: str):
    """Download server-side optimization mods into mods/."""
    _download_modrinth_mods(server_id, OPTIMIZATION_MODS, game_version)

def check_port_conflicts(server_id: str, ports: dict):
    """Reject a new server if any of its host ports are already claimed by
    another recipe OR currently bound on the host. Called from create_server
    before anything is written to disk."""
    if not ports:
        return
    new_host_ports = set(int(v) for v in ports.values() if v)
    #Conflict check against existing server recipes
    for sid, r in SERVER_RECIPES.items():
        if sid == server_id:
            continue
        taken = set(int(v) for v in r.get("ports", {}).values() if v)
        clash = new_host_ports & taken
        if clash:
            raise HTTPException(status_code=409,
                detail=f"Port(s) {sorted(clash)} already used by server '{r.get('name', sid)}'.")
    #Port bound on the host right now, catches non Lunkserver processes
    for hp in new_host_ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        try:
            sock.bind(("127.0.0.1", hp))
            sock.close()
        except OSError:
            raise HTTPException(status_code=409,
                detail=f"Port {hp} is already in use on this host.")
    #ponytail TCP only check, a UDP only port will not be caught, Docker still refuses to bind so the failure surfaces just less cleanly

@app.post("/api/servers/create")
def create_server(payload: CreateServerPayload, role: str = Depends(require_admin)):
    """Create a new server from a template or custom config.
    Persists to user_recipes.json, optionally starts the Docker container."""
    server_id = payload.server_id.strip().lower().replace(" ", "_")
    if not server_id:
        raise HTTPException(status_code=400, detail="server_id is required")

    #Resolve ports before creating the recipe so the conflict check runs before writing to disk
    template_key = payload.template or "custom"
    if template_key != "custom" and template_key in SERVER_TEMPLATES:
        tmpl = SERVER_TEMPLATES[template_key]
        ports_to_check = tmpl["ports"].copy()
        #Apply client port override, swap the primary host port matching the template default with the user value
        if payload.client_port and payload.client_port != tmpl["client_port"]:
            default_cp = tmpl["client_port"]
            ports_to_check = {
                k: (payload.client_port if int(v) == default_cp else v)
                for k, v in ports_to_check.items()
            }
    elif payload.image:
        ports_to_check = payload.ports
    else:
        ports_to_check = {}
    #Geyser Bedrock UDP port, add to conflict check if the user enabled Geyser
    gops = payload.game_options or {}
    if gops.get("enable_geyser"):
        bp = gops.get("geyser_port") or 19132
        ports_to_check[f"{bp}/udp"] = bp
    check_port_conflicts(server_id, ports_to_check)

    if template_key != "custom" and template_key in SERVER_TEMPLATES:
        tmpl = SERVER_TEMPLATES[template_key]
        recipe, err = create_from_template(template_key, server_id,
                                            name=payload.name or None,
                                            description=payload.description)
        #Apply the same client port override to the recipe so the container binds the user port not the template default
        if not err and payload.client_port and payload.client_port != tmpl["client_port"]:
            default_cp = tmpl["client_port"]
            recipe["ports"] = {
                k: (payload.client_port if int(v) == default_cp else v)
                for k, v in recipe["ports"].items()
            }
            recipe["client_port"] = payload.client_port
            save_user_recipe(server_id, recipe)
    elif payload.image:
        #Check if image matches a known template
        match = get_template_for_image(payload.image)
        if match and template_key != "custom":
            tmpl_key = next(k for k, v in SERVER_TEMPLATES.items() if v is match)
            recipe, err = create_from_template(tmpl_key, server_id,
                                               name=payload.name or None,
                                               description=payload.description)
        else:
            recipe, err = create_custom_recipe(
                server_id=server_id,
                name=payload.name or server_id,
                image=payload.image,
                ports=payload.ports,
                container_path=payload.container_path,
                client_port=payload.client_port,
                env=payload.env,
                ram_limit=payload.ram_limit,
                description=payload.description,
            )
    else:
        raise HTTPException(status_code=400, detail="Either template or image is required")

    if err:
        raise HTTPException(status_code=400, detail=err)

    #Game specific creation options for factorio and minecraft wizards
    gops = payload.game_options or {}
    if recipe.get("game_type") == "factorio":
        #Version override maps to image tag
        fv = gops.get("factorio_version")
        if fv:
            recipe["image"] = f"factoriotools/factorio:{fv}"
            recipe["game_version"] = "2.0" if fv == "stable" else "2.1"
        #Space Age DLC sets an env var the Docker image reads on startup
        if gops.get("space_age_dlc"):
            recipe["env"]["DLC_SPACE_AGE"] = "true"
        #Custom map gen settings for ore frequency size and richness overrides
        map_overrides = gops.get("map_gen_overrides")
        if map_overrides:
            cfg_dir = os.path.expanduser(f"~/Documents/server_data/{server_id}/config")
            os.makedirs(cfg_dir, exist_ok=True)
            settings_path = os.path.join(cfg_dir, "map-gen-settings.json")
            with open(settings_path, "w") as f:
                json.dump(map_overrides, f, indent=2)
        #Map gen preset writes map gen settings json before container start so the first map generation uses it
        preset_key = gops.get("map_gen_preset")
        if preset_key:
            preset = FACTORIO_MAP_PRESETS.get(preset_key)
            if preset:
                cfg_dir = os.path.expanduser(f"~/Documents/server_data/{server_id}/config")
                os.makedirs(cfg_dir, exist_ok=True)
                settings_path = os.path.join(cfg_dir, "map-gen-settings.json")
                with open(settings_path, "w") as f:
                    json.dump(preset["settings"], f, indent=2)
        #Mod pre install downloads selected mods before start
        mods = gops.get("mods", [])
        if mods:
            if not FACTORIO_USERNAME or not FACTORIO_TOKEN:
                raise HTTPException(status_code=500, detail="FACTORIO_USERNAME and FACTORIO_TOKEN env vars not set. Mod installation requires Factorio account credentials.")
            for mod in mods:
                base_url = mod["download_url"]
                if not base_url.startswith("https"):
                    base_url = f"https://mods.factorio.com{base_url}"
                sep = "&" if "?" in base_url else "?"
                authed_url = f"{base_url}{sep}username={urllib.parse.quote(FACTORIO_USERNAME)}&token={urllib.parse.quote(FACTORIO_TOKEN)}"
                result = docker_mgr.install_mod(server_id, authed_url, mod["filename"])
                if result["status"] == "error":
                    raise HTTPException(status_code=500, detail=f"Mod install failed ({mod['filename']}): {result['message']}")
        save_user_recipe(server_id, recipe)

    elif recipe.get("game_type") == "minecraft":
        #Server type override via TYPE env
        mc_type = gops.get("server_type")
        if mc_type:
            recipe["env"]["TYPE"] = mc_type
        #Game version override as a Minecraft version string
        mc_version = gops.get("mc_version")
        if mc_version:
            recipe["env"]["VERSION"] = mc_version
            recipe["game_version"] = mc_version
        #CurseForge modpack sets type to auto curseforge with a page url
        modpack_url = gops.get("modpack_url")
        if modpack_url:
            if not CF_API_KEY:
                raise HTTPException(status_code=500, detail="CF_API_KEY env var not set. CurseForge modpack installation requires an API key.")
            recipe["env"]["TYPE"] = "AUTO_CURSEFORGE"
            recipe["env"]["CF_API_KEY"] = CF_API_KEY
            recipe["env"]["CF_PAGE_URL"] = modpack_url
        #Fabric mods download before start
        fabric_mods = gops.get("fabric_mods", [])
        if fabric_mods:
            for mod in fabric_mods:
                result = docker_mgr.install_mod(server_id, mod["download_url"], mod["filename"])
                if result["status"] == "error":
                    raise HTTPException(status_code=500, detail=f"Mod install failed ({mod['filename']}): {result['message']}")
        save_user_recipe(server_id, recipe)
        #Resolve the actual MC version for mod downloads, if the user did not pick one fetch the latest release from Mojang
        mc_version = gops.get("mc_version") or ""
        if not mc_version:
            try:
                import httpx as _hx
                resp = _hx.get("https://piston-meta.mojang.com/mc/game/version_manifest.json", timeout=10)
                mc_version = resp.json().get("latest", {}).get("release", "")
            except Exception:
                mc_version = ""
        if mc_version:
            recipe["game_version"] = mc_version
            save_user_recipe(server_id, recipe)
        #Geyser and Floodgate one click install adds both and opens the Bedrock port
        if gops.get("enable_geyser"):
            bp = gops.get("geyser_port") or 19132
            _install_geyser_sync(server_id, mc_version, recipe, bedrock_port=bp)
        #Optimization mods one click install
        if gops.get("enable_optimization"):
            _install_optimization_mods_sync(server_id, mc_version)

    #Bust the fleet cache so the new server shows in the sidebar immediately even without starting the container
    docker_mgr._fleet_cache = None

    #Optionally start the container immediately
    if payload.start_now:
        result = docker_mgr.start_server(
            server_id=server_id,
            image_repo=recipe["image"],
            ports=recipe.get("ports", {}),
            env_vars=recipe.get("env", {}),
            container_path=recipe.get("container_path", "/data"),
            network_mode=recipe.get("network_mode", "bridge"),
            ram_limit=recipe.get("ram_limit"),
        )
        if result.get("status") == "error":
            return {"status": "warning",
                    "message": f"Recipe saved but container failed to start: {result.get('message')}",
                    "server_id": server_id}
        #Seed the stats cache so the new server appears in the fleet immediately instead of waiting for the poll cycle
        STATS_CACHE[server_id] = {
            "id": server_id, "status": "running", "cpu_load": 0.0,
            "ram_used": 0.0, "uptime": "Booting...", "game_type": recipe.get("game_type", "unknown"),
        }
        update_frpc_tunnel()

    return {"status": "success", "server_id": server_id, "recipe": recipe}

@app.delete("/api/servers/{server_id}")
def delete_server(server_id: str, role: str = Depends(require_admin)):
    """Stop, remove Docker container, and delete a user-created recipe.
    Only works on _user_created recipes — hand-written recipes are protected."""
    recipe = SERVER_RECIPES.get(server_id)
    if not recipe:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")
    if not recipe.get("_user_created"):
        raise HTTPException(status_code=403,
                            detail="Cannot delete hand-written recipe. Only dashboard-created servers can be removed here.")
    try:
        docker_mgr.stop_server(server_id)
    except Exception:
        pass
    try:
        container = docker_mgr._get_client(server_id).containers.get(server_id)
        container.remove(force=True)
    except Exception:
        pass  #Already gone
    delete_user_recipe(server_id)
    STATS_CACHE.pop(server_id, None)
    docker_mgr._fleet_cache = None
    update_frpc_tunnel()
    return {"status": "success", "message": f"Server '{server_id}' removed"}

@app.get("/api/fleet/groups")
def get_fleet_groups():
    """Servers grouped by remote host for collapsable sidebar sections."""
    all_statuses = docker_mgr.get_all_statuses(list(SERVER_RECIPES.keys()))
    groups = {}
    for server_id, status in all_statuses.items():
        recipe = SERVER_RECIPES.get(server_id, {})
        host = recipe.get("remote_host", "local")
        game_type = recipe.get("game_type")
        alias = None
        if host == "local" or not host:
            host_key = "local"
            name = "Lunkserver 2.0"
        else:
            host_key = host
            try:
                ip = host.split("@")[1]
            except (IndexError, ValueError):
                ip = host
            alias = recipe.get("alias")
            if alias:
                name = alias
            else:
                name = f"Remote ({ip})"
        if host_key not in groups:
            groups[host_key] = {"name": name, "alias": alias, "servers": {}, "game_types": {}, "user_created": set()}
        if game_type:
            groups[host_key]["game_types"][server_id] = game_type
        if recipe.get("_user_created"):
            groups[host_key]["user_created"].add(server_id)
        groups[host_key]["servers"][server_id] = status
    #Drop fleet groups whose host is entirely offline
    groups = {k: g for k, g in groups.items()
              if any(s != "unknown" for s in g["servers"].values())}
    #Convert sets to lists for JSON serialization
    for g in groups.values():
        g["user_created"] = list(g["user_created"])
    return groups

@app.get("/api/system")
def get_system_stats():
    cpu_pct = get_cpu_percent()
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    ram_total_gb = vm.total / (1024 ** 3)
    ram_used_gb = vm.used / (1024 ** 3)
    disk_total_gb = disk.total / (1024 ** 3)
    disk_used_gb = disk.used / (1024 ** 3)
    return {
        "cpu_percent": round(cpu_pct, 1),
        "ram_percent": round(vm.percent, 1),
        "ram_used": f"{ram_used_gb:.2f}",
        "ram_total": f"{ram_total_gb:.2f}",
        "storage_percent": round(disk.percent, 1),
        "storage_used": f"{disk_used_gb:.2f}",
        "storage_total": f"{disk_total_gb:.2f}"
    }

@app.get("/api/vram")
def get_vram():
    """Local GPU VRAM stats."""
    return stats_fetcher.get_vram_stats()

@app.get("/api/remote-hosts")
def get_remote_hosts():
    """Remote host system stats, excluding satellite hosts (HTTP microservice)."""
    hosts = stats_fetcher.get_unique_remote_hosts()
    hosts = [h for h in hosts if h not in SATELLITE_HOSTS]
    result = []
    for host in hosts:
        stats = stats_fetcher.get_remote_system_stats(host)
        alias = None
        try:
            ip = host.split("@")[1]
        except (IndexError, ValueError):
            ip = host
        for sid, r in SERVER_RECIPES.items():
            if r.get("remote_host") == host:
                alias = r.get("alias")
                break
        result.append({
            "host": host,
            "ip": ip,
            "alias": alias or ip,
            "stats": stats
        })
    return result

@app.get("/api/vps-telem")
def get_vps_telem():
    """VPS bandwidth stats from LunkVPS."""
    return stats_fetcher.get_vps_bandwidth()

@app.get("/api/containers/groups")
def get_container_groups():
    """Docker-compose container groups (e.g., odysseyus-stack)."""
    groups = {}
    for sid, recipe in SERVER_RECIPES.items():
        group_name = recipe.get("group")
        if group_name:
            if group_name not in groups:
                groups[group_name] = {
                    "name": f"Group: {group_name.replace('-', ' ').title()}",
                    "alias": group_name.replace('-', ' ').title(),
                    "servers": {},
                    "stats": None,
                    "game_types": {}
                }
            groups[group_name]["servers"][sid] = recipe.get("name", sid)
            groups[group_name]["game_types"][sid] = recipe.get("game_type", "other")
    all_group_servers = list({sid for g in groups.values() for sid in g["servers"]})
    if all_group_servers:
        statuses = docker_mgr.get_all_statuses(all_group_servers, SERVER_RECIPES)
        for sid, status in statuses.items():
            for group in groups.values():
                if sid in group["servers"]:
                    group["servers"][sid] = status
    #Drop groups whose host is entirely unreachable
    groups = {k: g for k, g in groups.items()
              if any(s != "unknown" for s in g["servers"].values())}
    return groups

@app.get("/api/satellites")
async def get_satellites():
    """Satellite host stats from deployed microservices."""
    try:
        return await asyncio.wait_for(stats_fetcher._get_satellite_stats_async(), 10)
    except asyncio.TimeoutError:
        return []

@app.get("/api/dashboard")
async def get_dashboard():
    """Consolidated telemetry: system, vram, remote-hosts, fleet/groups,
    containers/groups, vps-telem, satellites. One round trip instead of 6."""
    #Fast local calls run first
    system = get_system_stats()
    vram = stats_fetcher.get_vram_stats()
    #Slow calls run concurrent with 10s timeout each, return exceptions true means a hung SSH returns empty not a 504
    #ponytail wait for can not cancel a running thread, leaked thread finishes in background, acceptable until SSH hangs become frequent
    fleet_groups, container_groups, remote_hosts, satellites, vps_telem = await asyncio.gather(
        asyncio.wait_for(asyncio.to_thread(get_fleet_groups), 10),
        asyncio.wait_for(asyncio.to_thread(get_container_groups), 10),
        asyncio.wait_for(asyncio.to_thread(get_remote_hosts), 10),
        asyncio.wait_for(stats_fetcher._get_satellite_stats_async(), 10),
        asyncio.wait_for(asyncio.to_thread(stats_fetcher.get_vps_bandwidth), 10),
        return_exceptions=True,
    )
    if isinstance(fleet_groups, Exception):
        fleet_groups = {}
    if isinstance(container_groups, Exception):
        container_groups = {}
    if isinstance(remote_hosts, Exception):
        remote_hosts = []
    if isinstance(satellites, Exception):
        satellites = []
    if isinstance(vps_telem, Exception):
        vps_telem = {"status": "error", "bandwidth": {"text": "0 GB / 3000 GB", "reset_date": "Resets Unknown", "used": "0 GB", "percent": 0}, "per_server_bandwidth": {}}
    return {
        "system": system,
        "vram": vram,
        "remote_hosts": remote_hosts,
        "fleet_groups": fleet_groups,
        "container_groups": container_groups,
        "vps_telem": vps_telem,
        "satellites": satellites,
        "allocation": get_fleet_allocation(),
        "ts": time.time(),
    }

@app.get("/api/servers/{server_id}")
def get_server_stats(server_id: str, role: str = Depends(get_current_role)):
    """Server stats merging recipes, overrides, and cache."""
    try:
        recipe = SERVER_RECIPES.get(server_id, {})
        override_path = os.path.expanduser(f"~/Documents/server_data/{server_id}/lunk_overrides.json")
        overrides = {}
        if os.path.exists(override_path):
            try:
                with open(override_path, "r") as f:
                    overrides = json.load(f)
            except Exception:
                pass

        primary_port = recipe.get("client_port")
        if not primary_port:
            ports = recipe.get("ports", {})
            primary_port = list(ports.values())[0] if ports else None

        #Overrides take priority over recipe defaults
        ram_limit_str = overrides.get("ram_limit") or recipe.get("ram_limit", "0g")
        allocated_gb = parse_ram_to_gb(ram_limit_str)
        description = overrides.get("description") or recipe.get("description", "")
        game_version = overrides.get("game_version") or recipe.get("game_version", "unknown")

        #Merge with cached stats if available
        if server_id in STATS_CACHE:
            stats = STATS_CACHE[server_id].copy()
            stats["name"] = recipe.get("name", server_id)
            stats["client_port"] = primary_port
            stats["description"] = description
            stats["ram_allocated"] = allocated_gb
            stats["game_version"] = game_version
            stats["game_type"] = recipe.get("game_type", "unknown")
            stats["_user_created"] = recipe.get("_user_created", False)
            stats["ports"] = recipe.get("ports", {})
            stats["disk_gb"] = get_server_disk_gb(server_id)
            #Zero usage stats for offline servers
            if stats.get("status") not in ["running", "restarting"]:
                stats["ram_used"] = 0.0
                stats["cpu_load"] = 0.0
            return stats

        #Fallback poll Docker directly if the background cache has not populated yet, happens when the poller is stuck on slow remote hosts
        try:
            stats = docker_mgr.get_container_stats(server_id)
            stats["name"] = recipe.get("name", server_id)
            stats["client_port"] = primary_port
            stats["description"] = description
            stats["ram_allocated"] = allocated_gb
            stats["game_version"] = game_version
            stats["game_type"] = recipe.get("game_type", "unknown")
            stats["_user_created"] = recipe.get("_user_created", False)
            stats["ports"] = recipe.get("ports", {})
            stats["disk_gb"] = get_server_disk_gb(server_id)
            STATS_CACHE[server_id] = stats  #seed cache for future requests
            if stats.get("status") not in ["running", "restarting"]:
                stats["ram_used"] = 0.0
                stats["cpu_load"] = 0.0
            return stats
        except Exception:
            pass

        return {
            "id": server_id,
            "name": recipe.get("name", server_id),
            "status": "loading",
            "cpu_load": 0.0,
            "ram_used": 0.0,
            "ram_allocated": allocated_gb,
            "uptime": "Loading...",
            "connection_string": "",
            "client_port": primary_port,
            "description": description,
            "game_type": recipe.get("game_type", "unknown"),
            "game_version": game_version,
            "ports": recipe.get("ports", {}),
            "disk_gb": get_server_disk_gb(server_id),
            "_user_created": recipe.get("_user_created", False)
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "id": server_id}

@app.post("/api/servers/{server_id}/start")
def start_server_endpoint(server_id: str, role: str = Depends(get_current_role)):
    recipe = SERVER_RECIPES.get(server_id)
    if not recipe:
        raise HTTPException(status_code=404, detail=f"Server recipe '{server_id}' not found.")

    compose_file = recipe.get("compose_file")
    remote_host = recipe.get("remote_host")

    #Docker compose servers on remote hosts
    if compose_file and remote_host:
        try:
            _ssh_compose(compose_file, remote_host, f"up -d {server_id}")
            result = {"status": "success", "message": "Compose container started"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        result = docker_mgr.start_server(
            server_id=server_id,
            image_repo=recipe.get("image", ""),
            ports=recipe.get("ports", {}),
            env_vars=recipe.get("env", {}),
            container_path=recipe.get("container_path", "/data"),
            network_mode=recipe.get("network_mode", "bridge"),
            extra_volumes=recipe.get("extra_volumes"),
            ram_limit=recipe.get("ram_limit"),
            devices=recipe.get("devices"),
            group_add=recipe.get("group_add")
        )

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))

    if server_id in STATS_CACHE:
        STATS_CACHE[server_id]["status"] = "running"
        STATS_CACHE[server_id]["uptime"] = "Booting..."
        #Refresh from Docker live so uptime is not stuck on booting
        try:
            fresh = docker_mgr.get_container_stats(server_id)
            if fresh.get("status") != "error":
                STATS_CACHE[server_id] = fresh
        except Exception:
            pass

    if server_id != "network_frp":
        update_frpc_tunnel()

    return result

@app.post("/api/servers/{server_id}/stop")
def stop_server_endpoint(server_id: str, role: str = Depends(require_admin)):
    recipe = SERVER_RECIPES.get(server_id, {})
    compose_file = recipe.get("compose_file")
    remote_host = recipe.get("remote_host")

    if compose_file and remote_host:
        try:
            _ssh_compose(compose_file, remote_host, f"stop {server_id}")
            result = {"status": "success", "message": "Compose container stopped"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        result = docker_mgr.stop_server(server_id)
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("message"))

    if server_id in STATS_CACHE:
        STATS_CACHE[server_id]["status"] = "exited"
        STATS_CACHE[server_id]["uptime"] = "Offline"

    if server_id != "network_frp":
        update_frpc_tunnel()

    return result

@app.post("/api/servers/{server_id}/restart")
def restart_server_endpoint(server_id: str, role: str = Depends(require_admin)):
    """Restart a server (local or remote compose)."""
    recipe = SERVER_RECIPES.get(server_id, {})
    compose_file = recipe.get("compose_file")
    remote_host = recipe.get("remote_host")

    if compose_file and remote_host:
        try:
            _ssh_compose(compose_file, remote_host, f"restart {server_id}")
            result = {"status": "success", "message": "Compose container restarted"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        result = docker_mgr.restart_server(server_id)
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("message"))
    return result

@app.post("/api/redeploy/{server_id}")
def api_redeploy_server(server_id: str, role: str = Depends(require_admin)):
    recipe = SERVER_RECIPES.get(server_id)
    if not recipe:
        raise HTTPException(status_code=404, detail=f"Recipe for {server_id} not found.")
    result = docker_mgr.redeploy_server(server_id, recipe)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))
    if server_id != "network_frp":
        update_frpc_tunnel()
    return {"status": "success", "message": f"{server_id} successfully rebuilt!"}

@app.get("/api/servers/{server_id}/update-check")
def check_server_update(server_id: str, role: str = Depends(get_current_role)):
    """Pull the image tag, compare to running container. Near-instant if current."""
    if server_id not in SERVER_RECIPES:
        raise HTTPException(status_code=404, detail="Server not found.")
    return docker_mgr.check_for_update(server_id)

@app.post("/api/servers/{server_id}/command")
def send_server_command(server_id: str, payload: CommandPayload, role: str = Depends(require_admin)):
    """Inject command using game-specific CLI template."""
    recipe = get_recipe(server_id)
    template = recipe.get("command_template", "{command}")
    result = docker_mgr.send_command(server_id, payload.command, template)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

@app.get("/api/modrinth/search")
async def search_modrinth(query: str, limit: int = 15, facets: str = None):
    headers = {"User-Agent": "lunkman/lunkServerManager/1.0 (contact@yourdomain.com)"}
    async with httpx.AsyncClient() as client:
        try:
            encoded_query = urllib.parse.quote(query)
            url = f"https://api.modrinth.com/v2/search?query={encoded_query}&limit={limit}"
            if facets:
                url += f"&facets={facets}"
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail="Modrinth API Error")
        except httpx.RequestError:
            raise HTTPException(status_code=500, detail="Failed to connect to Modrinth")

@app.get("/api/servers/{server_id}/mods")
def get_installed_mods(server_id: str):
    result = docker_mgr.list_mods(server_id)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

@app.post("/api/servers/{server_id}/mods/install")
def install_server_mod(server_id: str, payload: ModInstallPayload, role: str = Depends(require_admin)):
    result = docker_mgr.install_mod(server_id, payload.download_url, payload.filename)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

@app.delete("/api/servers/{server_id}/mods/{filename}")
def delete_server_mod(server_id: str, filename: str, role: str = Depends(require_admin)):
    result = docker_mgr.delete_mod(server_id, filename)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

@app.post("/api/servers/{server_id}/geyser-setup")
def setup_geyser(server_id: str, game_version: str = "", bedrock_port: int = 19132, role: str = Depends(require_admin)):
    """One-click Geyser + Floodgate install for a Fabric Minecraft server.
    Downloads Fabric API + both jars from Modrinth into the server's mods/ dir,
    and adds the Bedrock port (default 19132/udp) to the recipe if missing."""
    recipe = SERVER_RECIPES.get(server_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Server not found.")
    if recipe.get("game_type") != "minecraft":
        raise HTTPException(status_code=400, detail="Geyser setup is for Minecraft servers only.")
    installed = docker_mgr.list_mods(server_id).get("mods", [])
    if any("geyser" in m.lower() for m in installed):
        raise HTTPException(status_code=409, detail="Geyser is already installed on this server.")
    #Check the Bedrock port against other servers before claiming it
    check_port_conflicts(server_id, {f"{bedrock_port}/udp": bedrock_port})
    _install_geyser_sync(server_id, game_version, recipe, bedrock_port=bedrock_port)
    return {"status": "success",
            "bedrock_port": bedrock_port,
            "message": f"Installed Fabric API + Geyser + Floodgate. Restart the server to activate Bedrock crossplay (port {bedrock_port}/udp)."}

@app.post("/api/servers/{server_id}/optimization-setup")
def setup_optimization(server_id: str, game_version: str = "", role: str = Depends(require_admin)):
    """One-click server-side optimization mods for a Fabric Minecraft server."""
    recipe = SERVER_RECIPES.get(server_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Server not found.")
    if recipe.get("game_type") != "minecraft":
        raise HTTPException(status_code=400, detail="Optimization mods are for Minecraft servers only.")
    _install_optimization_mods_sync(server_id, game_version)
    return {"status": "success",
            "message": "Installed optimization mods (Lithium, FerriteCore, Krypton, ServerCore, Fabric API). Restart to activate."}

class RemoveModsPayload(BaseModel):
    slugs: list

@app.post("/api/servers/{server_id}/remove-mods-by-slug")
def remove_mods_by_slug(server_id: str, payload: RemoveModsPayload, role: str = Depends(require_admin)):
    """Remove mods from mods/ by Modrinth slug prefix.
    slugs is a list of slug strings; any installed jar whose filename
    contains a slug (minus hyphens) is deleted."""
    recipe = SERVER_RECIPES.get(server_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Server not found.")
    installed = docker_mgr.list_mods(server_id).get("mods", [])
    removed = []
    for filename in installed:
        fl = filename.lower()
        for slug in payload.slugs:
            #Match slug names without hyphens like lithium ferritecore and geyser
            normalized = slug.replace("-", "").replace("_", "")
            if normalized in fl.replace("-", "").replace("_", ""):
                docker_mgr.delete_mod(server_id, filename)
                removed.append(filename)
                break
    return {"status": "success", "removed": removed}

@app.get("/api/servers/{server_id}/mod-bundles")
def get_mod_bundles(server_id: str, role: str = Depends(get_current_role)):
    """Check which mod bundles (geyser, optimization) are fully installed."""
    installed = set(m.lower() for m in docker_mgr.list_mods(server_id).get("mods", []))
    def _check(slugs):
        #A slug is installed if any jar file matches the normalized slug name
        for slug in slugs:
            normalized = slug.replace("-", "").replace("_", "")
            if not any(normalized in f.replace("-", "").replace("_", "") for f in installed):
                return False
        return True
    return {
        "geyser": _check(GEYSER_MODS),
        "optimization": _check(OPTIMIZATION_MODS),
    }

#Factorio mod portal
#The mod portal has no fuzzy search, the listing API is alphabetical and the detail API does exact name lookup, we fetch a page and filter by substring
#Downloads need Factorio account credentials

@app.get("/api/factorio/mods/search")
async def search_factorio_mods(query: str, limit: int = 15, factorio_version: str = "", expansion: str = ""):
    """Search the Factorio mod portal.
    The public listing API has no text search — q/query params are ignored.
    The website uses a separate /search endpoint that returns HTML.
    We scrape mod names from that, then do direct API lookups for release details."""
    if not query.strip():
        return {"results": []}
    try:
        async with httpx.AsyncClient() as client:
            #Website search returns HTML with mod links
            search_url = "https://mods.factorio.com/search"
            params = {"query": query, "show_deprecated": "False", "exclude_category": "internal"}
            if factorio_version:
                params["factorio_version"] = factorio_version
            if expansion:
                params["expansion"] = expansion
            resp = await client.get(search_url, params=params, timeout=15, follow_redirects=True)
            resp.raise_for_status()
            #Extract mod names from search links
            mod_names = list(dict.fromkeys(  #dedupe and preserve order
                m for m in re.findall(r'href="/mod/([^"?]+)\?from=search"', resp.text)
            ))[:limit * 3]  #fetch extra, some will not have releases for our factorio version
            #Direct lookup each mod for release details
            hits = []
            for name in mod_names:
                if len(hits) >= limit:
                    break
                r = await client.get(f"https://mods.factorio.com/api/mods/{urllib.parse.quote(name)}", timeout=10)
                if r.status_code != 200:
                    continue
                d = r.json()
                #Latest release is null on this endpoint, find the latest matching factorio version from releases
                releases = d.get("releases", [])
                if factorio_version:
                    matching = [rel for rel in releases
                                if rel.get("info_json", {}).get("factorio_version") == factorio_version]
                    releases = matching
                if not releases:
                    continue
                rel = releases[-1]  #latest release for our factorio version
                hits.append({
                    "name": d["name"],
                    "title": d.get("title", d["name"]),
                    "owner": d.get("owner", ""),
                    "summary": d.get("summary", ""),
                    "downloads": d.get("downloads_count", 0),
                    "thumbnail": d.get("thumbnail", ""),
                    "factorio_version": rel.get("info_json", {}).get("factorio_version", ""),
                    "version": rel.get("version", ""),
                    "download_url": rel.get("download_url", ""),
                    "file_name": rel.get("file_name", ""),
                })
        return {"results": hits}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Factorio mod portal API error")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@app.get("/api/factorio/mods/{mod_name}")
async def get_factorio_mod_details(mod_name: str):
    """Get full details + all releases for a Factorio mod."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://mods.factorio.com/api/mods/{urllib.parse.quote(mod_name)}",
                timeout=15)
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Mod '{mod_name}' not found.")
            resp.raise_for_status()
            data = resp.json()
        releases = []
        for r in data.get("releases", []):
            releases.append({
                "version": r.get("version", ""),
                "factorio_version": r.get("info_json", {}).get("factorio_version", ""),
                "download_url": r.get("download_url", ""),
                "file_name": r.get("file_name", ""),
                "released_at": r.get("released_at", ""),
            })
        return {
            "name": data.get("name", mod_name),
            "title": data.get("title", mod_name),
            "owner": data.get("owner", ""),
            "summary": data.get("summary", ""),
            "description": data.get("description", ""),
            "downloads": data.get("downloads_count", 0),
            "thumbnail": data.get("thumbnail", ""),
            "releases": releases,
        }
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Factorio mod portal API error")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lookup failed: {str(e)}")

#Factorio map gen presets transcribed from the Factorio 2.0 source, each preset patches map gen settings json, named values like very good and very high are valid
FACTORIO_MAP_PRESETS = {
    "default": {
        "label": "Default",
        "description": "Vanilla balanced map generation.",
        "settings": {
            "autoplace_controls": {
                "coal": {"frequency": 1, "size": 1, "richness": 1},
                "copper-ore": {"frequency": 1, "size": 1, "richness": 1},
                "crude-oil": {"frequency": 1, "size": 1, "richness": 1},
                "iron-ore": {"frequency": 1, "size": 1, "richness": 1},
                "stone": {"frequency": 1, "size": 1, "richness": 1},
                "uranium-ore": {"frequency": 1, "size": 1, "richness": 1},
                "enemy-base": {"frequency": 1, "size": 1, "richness": 1},
                "water": {"frequency": 1, "size": 1},
            },
            "starting_area": 1,
        },
    },
    #Space Age planet resources, each planet is a separate surface with its own resources
    "space_age_planets": {
        "nauvis": {
            "coal": {"frequency": 1, "size": 1, "richness": 1},
            "copper-ore": {"frequency": 1, "size": 1, "richness": 1},
            "crude-oil": {"frequency": 1, "size": 1, "richness": 1},
            "iron-ore": {"frequency": 1, "size": 1, "richness": 1},
            "stone": {"frequency": 1, "size": 1, "richness": 1},
            "uranium-ore": {"frequency": 1, "size": 1, "richness": 1},
            "enemy-base": {"frequency": 1, "size": 1, "richness": 1},
            "water": {"frequency": 1, "size": 1},
        },
        "vulcanus": {
            "coal": {"frequency": 1, "size": 1, "richness": 1},
            "sulfuric-acid-geyser": {"frequency": 1, "size": 1, "richness": 1},
        },
        "fulgora": {
            "scrap": {"frequency": 1, "size": 1, "richness": 1},
        },
        "gleba": {
            "sporenest": {"frequency": 1, "size": 1, "richness": 1},
            "stone": {"frequency": 1, "size": 1, "richness": 1},
        },
        "aquilo": {
            "lithium-brine": {"frequency": 1, "size": 1, "richness": 1},
            "fluorine-vent": {"frequency": 1, "size": 1, "richness": 1},
        },
    },
    "rich-resources": {
        "label": "Rich Resources",
        "description": "All ore patches have very high richness.",
        "settings": {
            "autoplace_controls": {
                "iron-ore": {"richness": 6},
                "copper-ore": {"richness": 6},
                "stone": {"richness": 6},
                "coal": {"richness": 6},
                "uranium-ore": {"richness": 6},
                "crude-oil": {"richness": 6},
            },
        },
    },
    "rail-world": {
        "label": "Rail World",
        "description": "Fewer, larger resource patches. No enemy expansion. Slow evolution.",
        "settings": {
            "autoplace_controls": {
                "coal": {"frequency": 0.3333333333, "size": 3, "richness": 1},
                "copper-ore": {"frequency": 0.3333333333, "size": 3, "richness": 1},
                "crude-oil": {"frequency": 0.3333333333, "size": 3, "richness": 1},
                "uranium-ore": {"frequency": 0.3333333333, "size": 3, "richness": 1},
                "iron-ore": {"frequency": 0.3333333333, "size": 3, "richness": 1},
                "stone": {"frequency": 0.3333333333, "size": 3, "richness": 1},
                "enemy-base": {"frequency": 0.1666666667, "size": 3, "richness": 1},
                "water": {"frequency": 0.5, "size": 1.5},
            },
        },
    },
    "death-world": {
        "label": "Death World",
        "description": "Many large enemy bases. Small starting area. Fast evolution.",
        "settings": {
            "starting_area": 0.3333333333,
            "autoplace_controls": {
                "enemy-base": {"frequency": 6, "size": 6, "richness": 6},
            },
        },
    },
    "death-world-marathon": {
        "label": "Death World Marathon",
        "description": "Death world + 4x technology cost.",
        "settings": {
            "starting_area": 0.3333333333,
            "autoplace_controls": {
                "enemy-base": {"frequency": 6, "size": 6, "richness": 6},
            },
        },
    },
    "ribbon-world": {
        "label": "Ribbon World",
        "description": "Map is 128 tiles tall. All resources frequent but small and rich.",
        "settings": {
            "height": 128,
            "starting_area": 3,
            "autoplace_controls": {
                "coal": {"frequency": 3, "size": 0.5, "richness": 2},
                "copper-ore": {"frequency": 3, "size": 0.5, "richness": 2},
                "crude-oil": {"frequency": 3, "size": 0.5, "richness": 2},
                "uranium-ore": {"frequency": 3, "size": 0.5, "richness": 2},
                "iron-ore": {"frequency": 3, "size": 0.5, "richness": 2},
                "stone": {"frequency": 3, "size": 0.5, "richness": 2},
                "water": {"frequency": 4, "size": 0.25},
            },
        },
    },
    "marathon": {
        "label": "Marathon",
        "description": "4x technology cost. Everything else default.",
        "settings": {},
    },
}

class FactorioPresetPayload(BaseModel):
    preset: str

@app.get("/api/factorio/map-presets")
def get_factorio_map_presets(space_age: bool = False):
    """Return available map-gen presets (static game data).
    Includes the default preset's autoplace_controls so the AddServerModal
    can build a custom ore table without a separate endpoint.
    When space_age=true, also returns planet resource definitions for tabs."""
    default_ap = FACTORIO_MAP_PRESETS["default"]["settings"]["autoplace_controls"]
    presets = [{
        "id": k, "label": v["label"], "description": v["description"],
        "resources": default_ap if k == "default" else None,
    } for k, v in FACTORIO_MAP_PRESETS.items()
      if k != "space_age_planets"]  #Internal data not a selectable preset
    result = {"presets": presets}
    if space_age:
        result["planets"] = FACTORIO_MAP_PRESETS.get("space_age_planets", {})
    return result

@app.post("/api/servers/{server_id}/factorio/apply-preset")
def apply_factorio_preset(server_id: str, payload: FactorioPresetPayload, role: str = Depends(require_admin)):
    """Apply a map-gen preset to map-gen-settings.json.
    Fills the Nauvis autoplace_controls table — only sets keys the preset
    specifies, leaving user-edited values for other resources intact."""
    recipe = SERVER_RECIPES.get(server_id)
    if not recipe or recipe.get("game_type") != "factorio":
        raise HTTPException(status_code=400, detail="Server is not a Factorio server.")
    preset = FACTORIO_MAP_PRESETS.get(payload.preset)
    if not preset:
        raise HTTPException(status_code=400, detail=f"Unknown preset: {payload.preset}")
    filepath = os.path.expanduser(f"~/Documents/server_data/{server_id}/config/map-gen-settings.json")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="map-gen-settings.json not found. Start the server once to generate it.")
    with open(filepath) as f:
        settings = json.load(f)
    #Merge preset autoplace controls into existing ones for Nauvis only, only keys the preset defines are overwritten
    existing_ap = settings.setdefault("autoplace_controls", {})
    preset_ap = preset["settings"].get("autoplace_controls", {})
    for resource, attrs in preset_ap.items():
        existing_ap[resource] = {**existing_ap.get(resource, {}), **attrs}
    #Top level keys like height and starting area overwrite directly
    for k, v in preset["settings"].items():
        if k != "autoplace_controls":
            settings[k] = v
    with open(filepath, "w") as f:
        json.dump(settings, f, indent=2)
    return {"status": "success", "message": f"Preset '{preset['label']}' applied to Nauvis. Takes effect on next map generation."}

@app.post("/api/servers/{server_id}/factorio/regenerate-map")
def regenerate_factorio_map(server_id: str, role: str = Depends(require_admin)):
    """Delete the existing save so the container regenerates on next start
    using current map-gen-settings.json."""
    recipe = SERVER_RECIPES.get(server_id)
    if not recipe or recipe.get("game_type") != "factorio":
        raise HTTPException(status_code=400, detail="Server is not a Factorio server.")
    save_name = recipe.get("env", {}).get("SAVE_NAME", server_id)
    saves_dir = os.path.expanduser(f"~/Documents/server_data/{server_id}/saves")
    if not os.path.isdir(saves_dir):
        raise HTTPException(status_code=404, detail=f"Saves dir not found: {saves_dir}")
    deleted = []
    for f in os.listdir(saves_dir):
        if f.endswith(".zip"):
            os.remove(os.path.join(saves_dir, f))
            deleted.append(f)
    return {"status": "success", "deleted": deleted,
            "message": f"Deleted {len(deleted)} save(s). Restart the server to generate a fresh map."}

class FactorioModInstallPayload(BaseModel):
    download_url: str
    filename: str

@app.post("/api/servers/{server_id}/factorio/mods/install")
def install_factorio_mod(server_id: str, payload: FactorioModInstallPayload, role: str = Depends(require_admin)):
    """Download a Factorio mod with account credentials.
    The mod portal requires username + token query params on the download URL."""
    if not FACTORIO_USERNAME or not FACTORIO_TOKEN:
        raise HTTPException(status_code=500, detail="FACTORIO_USERNAME and FACTORIO_TOKEN env vars not set. Mod downloads require Factorio account credentials.")
    #Build authenticated download URL
    base_url = payload.download_url
    if not base_url.startswith("https"):
        base_url = f"https://mods.factorio.com{base_url}"
    sep = "&" if "?" in base_url else "?"
    authed_url = f"{base_url}{sep}username={urllib.parse.quote(FACTORIO_USERNAME)}&token={urllib.parse.quote(FACTORIO_TOKEN)}"
    result = docker_mgr.install_mod(server_id, authed_url, payload.filename)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

@app.websocket("/api/servers/{server_id}/logs")
async def websocket_logs(websocket: WebSocket, server_id: str):
    """Live console WebSocket: burst history then stream new logs.
    Auth via cookie (auth_token) or ?token= query param, same tokens
    as the REST API."""
    token = websocket.query_params.get("token")
    if not token:
        token = websocket.cookies.get("auth_token")
    #Constant time check so WS auth can not be timing attacked
    if not token or not (compare_digest(token, ADMIN_TOKEN) or compare_digest(token, GUEST_TOKEN)):
        await websocket.accept()
        await websocket.send_text("[System] Unauthorized. Valid token required.")
        await websocket.close(code=1008)
        return
    await websocket.accept()
    history_lines = await asyncio.wait_for(
        asyncio.to_thread(docker_mgr.get_container_history, server_id, 50),
        timeout=8)
    for line in history_lines:
        if line.strip():
            await websocket.send_text(line)
    try:
        container = docker_mgr._get_container_safe(
            docker_mgr._get_client(server_id), server_id)
        if not container:
            await websocket.send_text("[System] Container not found or host offline.")
            return
        log_generator = container.logs(stream=True, follow=True, tail=0)
        log_iterator = iter(log_generator)
        while True:
            line = await asyncio.to_thread(next, log_iterator, None)
            if line is None:
                await websocket.send_text("[System] Container stream ended.")
                break
            await websocket.send_text(line.decode('utf-8').strip())
    except Exception as e:
        await websocket.send_text(f"[System] Live stream disconnected: {str(e)}")

@app.get("/api/servers/{server_id}/backups")
def api_get_server_backups(server_id: str, role: str = Depends(get_current_role)):
    recipe = SERVER_RECIPES.get(server_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Server not found.")
    result = docker_mgr.get_backup_list(server_id, recipe)
    return result

@app.post("/api/servers/{server_id}/backup")
def api_backup_server(server_id: str, role: str = Depends(require_admin)):
    """Manual hot or cold snapshot for a server."""
    recipe = SERVER_RECIPES.get(server_id)
    if not recipe:
        raise HTTPException(status_code=404, detail=f"Recipe for {server_id} not found.")
    result = docker_mgr.create_snapshot(server_id, recipe, retention_limit=99)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))
    return result

@app.post("/api/servers/{server_id}/backups/{filename}/restore")
def api_restore_server_backup(server_id: str, filename: str, payload: dict = Body(...), role: str = Depends(require_admin)):
    try:
        recipe = get_recipe(server_id)
        backup_type = payload.get("backup_type", "")
        result = docker_mgr.restore_backup(server_id, filename, backup_type, recipe)
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("message"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        print("\n=== FATAL RESTORE CRASH ===")
        traceback.print_exc()
        print("===========================\n")
        raise HTTPException(status_code=500, detail=f"Python crashed: {str(e)}")

@app.delete("/api/servers/{server_id}/backups/{filename}")
def api_delete_server_backup(server_id: str, filename: str, backup_type: str, role: str = Depends(require_admin)):
    recipe = get_recipe(server_id)
    result = docker_mgr.delete_backup(server_id, filename, backup_type, recipe)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))
    return result

@app.get("/api/servers/{server_id}/settings")
def get_server_settings(server_id: str, file_key: str = None, role: str = Depends(get_current_role)):
    try:
        recipe = SERVER_RECIPES.get(server_id)
        if not recipe:
            return {"status": "error", "content": "Server not found in recipes."}
        config_files = recipe.get("config_files")
        if not config_files:
            single_file = recipe.get("config_file")
            if single_file:
                config_files = {"Settings": single_file}
            else:
                return {"status": "unsupported", "content": "This server does not support dashboard configuration."}
        if not file_key or file_key not in config_files:
            file_key = list(config_files.keys())[0]
        filepath = os.path.expanduser(f"~/Documents/server_data/{server_id}/{config_files[file_key]}")
        if not os.path.exists(filepath):
            template_text = f"// --- FILE NOT FOUND ---\n// The server has not generated this file yet at:\n// {filepath}\n\n// Paste your configuration JSON here and click 'Save Changes' to create it!"
            return {"status": "success", "content": template_text, "current_file": file_key,
                    "current_filename": config_files[file_key],
                    "available_files": list(config_files.keys())}
        with open(filepath, "r") as f:
            content = f.read()
        return {"status": "success", "content": content, "current_file": file_key,
                "current_filename": config_files[file_key],
                "available_files": list(config_files.keys())}
    except Exception as e:
        return {"status": "error", "content": f"API Error: {str(e)}"}

@app.get("/api/minecraft/uuid/{username}")
def lookup_minecraft_uuid(username: str, role: str = Depends(get_current_role)):
    """Resolve a Minecraft username to its UUID via Mojang's API."""
    try:
        url = f"https://api.mojang.com/users/profiles/minecraft/{urllib.parse.quote(username)}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                if data and "id" in data:
                    raw = data["id"]
                    #Format as hyphenated UUID
                    uuid = f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}" if len(raw) == 32 else raw
                    return {"status": "success", "uuid": uuid, "name": data.get("name", username)}
            #204 No Content means the player does not exist
            return {"status": "error", "message": f"Player '{username}' not found."}
    except urllib.error.HTTPError as e:
        if e.code in (204, 400, 404):
            return {"status": "error", "message": f"Player '{username}' not found."}
        return {"status": "error", "message": f"Mojang API error: {e.code}"}
    except Exception as e:
        return {"status": "error", "message": f"Lookup failed: {str(e)}"}

@app.post("/api/servers/{server_id}/settings")
def save_server_settings(server_id: str, payload: dict = Body(...), file_key: str = None, role: str = Depends(require_admin)):
    try:
        content = payload.get("content", "")
        recipe = SERVER_RECIPES.get(server_id)
        config_files = recipe.get("config_files")
        if not config_files:
            single_file = recipe.get("config_file")
            if single_file:
                config_files = {"Settings": single_file}
            else:
                raise HTTPException(status_code=400, detail="Server does not support config editing.")
        if not file_key or file_key not in config_files:
            file_key = list(config_files.keys())[0]
        #Validate JSON before writing, a malformed whitelist json crashes the server on restart and can not be fixed from the UI
        target_filename = config_files[file_key]
        if target_filename.endswith(".json") and content.strip():
            try:
                json.loads(content)
            except json.JSONDecodeError as je:
                raise HTTPException(status_code=400, detail=f"Invalid JSON: {je}")
        filepath = os.path.expanduser(f"~/Documents/server_data/{server_id}/{target_filename}")
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            f.write(content)
        #Minecraft hot reloads the whitelist via rcon instead of a full restart, ops bans and server properties still need a real restart
        template = recipe.get("command_template", "{command}")
        if recipe.get("game_type") == "minecraft" and target_filename == "whitelist.json":
            docker_mgr.send_command(server_id, "whitelist reload", template)
            return {"status": "success", "message": f"{file_key} saved and whitelist reloaded!"}
        docker_mgr.restart_server(server_id)
        return {"status": "success", "message": f"{file_key} saved and server restarted!"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Save Error: {str(e)}")

@app.get("/api/servers/{server_id}/hardware")
def get_hardware_overrides(server_id: str, role: str = Depends(get_current_role)):
    """Hardware overrides for a server."""
    recipe = get_recipe(server_id)
    override_path = os.path.expanduser(f"~/Documents/server_data/{server_id}/lunk_overrides.json")
    payload = {"ram_limit": recipe.get("ram_limit", "2g"), "cf_modpack": "", "game_type": recipe.get("game_type")}
    if os.path.exists(override_path):
        try:
            with open(override_path, "r") as f:
                payload.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return payload

@app.post("/api/servers/{server_id}/hardware")
def save_hardware_overrides(server_id: str, payload: dict = Body(...), role: str = Depends(require_admin)):
    recipe = get_recipe(server_id)
    override_path = os.path.expanduser(f"~/Documents/server_data/{server_id}/lunk_overrides.json")
    try:
        with open(override_path, "w") as f:
            json.dump(payload, f, indent=4)
        #Merge overrides into the recipe so redeploy picks up the changes
        if "ram_limit" in payload:
            recipe["ram_limit"] = payload["ram_limit"]
        if "game_version" in payload:
            gv = payload["game_version"]
            recipe["game_version"] = gv
            #Minecraft sets VERSION env, Factorio updates image tag
            if recipe.get("game_type") == "minecraft":
                recipe.setdefault("env", {})["VERSION"] = gv
            elif recipe.get("game_type") == "factorio":
                recipe["image"] = f"factoriotools/factorio:{gv}"
        save_user_recipe(server_id, recipe)
        result = docker_mgr.redeploy_server(server_id, recipe)
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("message"))
        return {"status": "success", "message": "Hardware settings applied. Container rebuilt!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/servers/{server_id}/hardware")
def reset_hardware_overrides(server_id: str, role: str = Depends(require_admin)):
    """Delete overrides and rebuild with recipe defaults."""
    recipe = SERVER_RECIPES.get(server_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Server recipe not found.")
    override_path = os.path.expanduser(f"~/Documents/server_data/{server_id}/lunk_overrides.json")
    if os.path.exists(override_path):
        try:
            os.remove(override_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete overrides: {e}")
    try:
        result = docker_mgr.redeploy_server(server_id, recipe)
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("message"))
        return {"status": "success", "message": "Hardware reset to defaults. Container rebuilt!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/servers/redeploy")
def redeploy_server(server_id: str, role: str = Depends(require_admin)):
    recipe = get_recipe(server_id)
    result = docker_mgr.redeploy_server(server_id, recipe)
    return result

@app.post("/api/servers/start-group")
def start_group(server_ids: list[str], role: str = Depends(require_admin)):
    """Start a group of containers (compose or individual)."""
    results = _compose_group(server_ids, "up -d", "started")
    if results:
        return {"results": results}
    results = []
    for sid in server_ids:
        recipe = SERVER_RECIPES.get(sid)
        if not recipe:
            results.append({"server_id": sid, "status": "error", "message": "Recipe not found"})
            continue
        rh = recipe.get("remote_host")
        result = docker_mgr.start_server(
            server_id=sid,
            image_repo=recipe.get("image", ""),
            ports=recipe.get("ports", {}),
            env_vars=recipe.get("env", {}),
            remote_host=rh,
            ram_limit=recipe.get("ram_limit", ""),
        )
        results.append({"server_id": sid, "status": result.get("status"), "message": result.get("message")})
    return {"results": results}

@app.post("/api/servers/stop-group")
def stop_group(server_ids: list[str], role: str = Depends(require_admin)):
    """Stop a group of containers."""
    results = _compose_group(server_ids, "down", "stopped")
    if results:
        return {"results": results}
    results = []
    for sid in server_ids:
        result = docker_mgr.stop_server(server_id=sid)
        results.append({"server_id": sid, "status": result.get("status"), "message": result.get("message")})
    return {"results": results}

@app.post("/api/servers/restart-group")
def restart_group(server_ids: list[str], role: str = Depends(require_admin)):
    """Restart a group of containers."""
    results = _compose_group(server_ids, "restart", "restarted")
    if results:
        return {"results": results}
    results = []
    for sid in server_ids:
        result = docker_mgr.restart_server(server_id=sid)
        results.append({"server_id": sid, "status": result.get("status"), "message": result.get("message")})
    return {"results": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, proxy_headers=True, forwarded_allow_ips="*", reload=False)
