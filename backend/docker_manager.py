import docker
import httpx
from docker.errors import NotFound, APIError
from datetime import datetime, timezone
import os
import json
import shutil
import subprocess
import tarfile
import threading
import time
import glob
import urllib.request
from recipes import SERVER_RECIPES

SATELLITE_IP = "<VPS_TAILSCALE_IP>"

class DockerManager:
    def __init__(self):
        self.client = docker.from_env()
        self._fleet_cache = None
        self._FLEET_TTL = 15
        self._cache_lock = threading.Lock()

    def _get_client(self, server_id: str, provided_remote_host: str = None):
        """Get Docker client (local or SSH-based for remote)."""
        if provided_remote_host:
            return docker.DockerClient(base_url=provided_remote_host, use_ssh_client=True)
        recipe = SERVER_RECIPES.get(server_id, {})
        remote_url = recipe.get("remote_host")
        if remote_url:
            return docker.DockerClient(base_url=remote_url, use_ssh_client=True)
        return self.client

    def _get_container_safe(self, target_client, server_id: str):
        """Find container by exact name or compose-style suffix match."""
        try:
            all_containers = target_client.containers.list(all=True)
            for c in all_containers:
                name = c.name.lstrip("/")
                if name == server_id or name.endswith(f"-{server_id}"):
                    return c
        except Exception:
            pass
        return None

    def get_all_statuses(self, server_ids: list, recipes: dict = None):
        """Cached server statuses grouped by remote host."""
        recipes = recipes or SERVER_RECIPES
        now = time.time()
        with self._cache_lock:
            if self._fleet_cache:
                cache_time, cached_result = self._fleet_cache
                if now - cache_time < self._FLEET_TTL:
                    return cached_result

        statuses = {}
        hosts_to_check = {}

        for s_id in server_ids:
            recipe = recipes.get(s_id, {})
            host = recipe.get("remote_host", "local")
            if host not in hosts_to_check:
                hosts_to_check[host] = []
            hosts_to_check[host].append(s_id)

        for host, s_ids in hosts_to_check.items():
            if SATELLITE_IP in str(host):
                #SSH to docker first (reliable), fall back to satellite HTTP.
                containers = {}
                try:
                    proc = subprocess.run(
                        ["ssh", "-i", "~/.ssh/github_deploy_key", "-o", "ConnectTimeout=2",
                         "-o", "StrictHostKeyChecking=no", "lunkserver3",
                         "docker ps -a --format '{{.Names}}|{{.Status}}'"],
                        capture_output=True, text=True, timeout=8
                    )
                    if proc.returncode == 0:
                        for line in proc.stdout.strip().split('\n'):
                            if '|' in line:
                                name, status = line.split('|', 1)
                                containers[name.strip()] = status.strip()
                except Exception:
                    pass
                #If SSH gave nothing, try the satellite HTTP endpoint.
                if not containers:
                    try:
                        url = f"http://{SATELLITE_IP}:8765/api/containers"
                        response = httpx.get(url, timeout=10)
                        if response.status_code == 200:
                            containers = response.json().get("containers", {})
                    except Exception:
                        pass
                #Map container statuses. Host reachable but container absent = stopped.
                for s_id in s_ids:
                    if s_id in containers:
                        statuses[s_id] = containers[s_id]
                    else:
                        matched = False
                        for cname, cstatus in containers.items():
                            if cname == s_id or cname.endswith(f"-{s_id}-") or cname.endswith(f"-{s_id}-1"):
                                statuses[s_id] = cstatus
                                matched = True
                                break
                        if not matched:
                            statuses[s_id] = "stopped" if containers else "unknown"
                continue
            if host == "local":
                client = self.client
            else:
                #ponytail: do NOT eagerly construct docker.DockerClient here.
                #The ctor opens an SSH control socket and hangs >30s when the
                #host is down, blocking the SSH-first path below. Defer to the
                #docker-py fallback only.
                client = None
            try:
                if host != "local":
                    try:
                        ssh_target = host.replace("ssh://", "").replace(SATELLITE_IP, "Lunkserver3")
                        result = subprocess.run(
                             ["ssh", "-o", "ConnectTimeout=2", "-o", "StrictHostKeyChecking=no",
                              ssh_target,
                              "docker ps -a --format '{{.Names}}|{{.Status}}'"],
                             capture_output=True, text=True, timeout=8
                         )
                        container_map = {}
                        host_reachable = result.returncode == 0
                        if host_reachable:
                            for line in result.stdout.strip().split('\n'):
                                if '|' in line:
                                    name, status = line.split('|', 1)
                                    container_map[name.strip()] = status
                        for s_id in s_ids:
                            if s_id in container_map:
                                statuses[s_id] = container_map[s_id]
                            else:
                                #Try compose-style names (e.g., "odysseus-chromadb-1" for "chromadb")
                                found = False
                                for cname, cstatus in container_map.items():
                                    if cname.endswith(f"-{s_id}") and cstatus != "Exited":
                                        statuses[s_id] = "running" if "Up" in cstatus else cstatus.lower()
                                        found = True
                                        break
                                if not found:
                                    #SSH worked but container absent = stopped.
                                    #SSH failed (host down) = unknown, so group
                                    #filter hides container groups on dead hosts.
                                    statuses[s_id] = "stopped" if host_reachable else "unknown"
                    except Exception:
                        #ponytail: SSH failed (host down). Don't try docker-py
                        #— DockerClient ctor hangs >30s on unreachable hosts.
                        #"unknown" (not "stopped") so the group filter can hide
                        #container groups whose host is entirely offline.
                        for s_id in s_ids:
                            statuses[s_id] = "unknown"
                else:
                    all_containers = client.containers.list(all=True)
                    container_map = {c.name: c.status for c in all_containers}
                    for s_id in s_ids:
                        if s_id in container_map:
                            statuses[s_id] = container_map[s_id]
                        else:
                            for cname, cstatus in container_map.items():
                                if cname.endswith(f"-{s_id}") and cstatus != "exited":
                                    statuses[s_id] = cstatus
                                    break
                            else:
                                statuses[s_id] = "stopped"
            except Exception:
                for s_id in s_ids:
                    statuses[s_id] = "error"

        with self._cache_lock:
            self._fleet_cache = (now, statuses)
        return statuses

    def get_container_stats(self, server_id: str, remote_host: str = None):
        """Docker container stats (CPU, RAM, uptime)."""
        #Skip satellite hosts — they manage their own stats via microservice
        recipe = SERVER_RECIPES.get(server_id, {})
        host = remote_host or recipe.get("remote_host", "")
        if SATELLITE_IP in str(host):
            try:
                url = f"http://{SATELLITE_IP}:8765/api/stats"
                response = httpx.get(url, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "id": server_id, "status": "running", "container_id": "", "image": "",
                        "cpu_load": float(data.get("cpu", {}).get("percent", 0)),
                        "ram_used": float(data.get("ram", {}).get("used_gb", 0)),
                        "ram_allocated": float(data.get("ram", {}).get("total_gb", 0)),
                        "uptime": "Live"
                    }
            except Exception:
                pass
            return {
                "id": server_id, "status": "unknown", "container_id": "", "image": "",
                "cpu_load": 0.0, "ram_used": 0.0, "ram_allocated": 0.0, "uptime": "Offline"
            }
        target_client = self._get_client(server_id, remote_host)
        container = self._get_container_safe(target_client, server_id)

        if not container:
            return {"id": server_id, "status": "stopped", "container_id": "", "image": "",
                    "cpu_load": 0.0, "ram_used": 0.0, "ram_allocated": 0.0, "uptime": "Offline"}

        try:
            status = container.status
            #Use attrs for image name — container.image.tags triggers a lazy
            #API call and returns [] when the image digest has been superseded
            #by a newer pull (old digest loses its tag). attrs has the original
            #image name the container was created with.
            image_name = container.attrs.get("Config", {}).get("Image") or \
                (container.image.tags[0] if container.image.tags else "unknown")

            if status not in ['running', 'restarting']:
                return {"id": server_id, "status": status, "container_id": container.short_id,
                        "image": image_name, "cpu_load": 0.0, "ram_used": 0.0,
                        "ram_allocated": 0.0, "uptime": "Offline"}

            stats = container.stats(stream=False)
            mem_usage = stats.get('memory_stats', {}).get('usage', 0)
            mem_limit = stats.get('memory_stats', {}).get('limit', 0)
            ram_used = round(mem_usage / (1024**3), 2)
            ram_allocated = round(mem_limit / (1024**3), 2)

            precpu_total = stats.get('precpu_stats', {}).get('cpu_usage', {}).get('total_usage', 0)
            precpu_system = stats.get('precpu_stats', {}).get('system_cpu_usage', 0)
            cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - precpu_total
            system_delta = stats['cpu_stats'].get('system_cpu_usage', 0) - precpu_system
            #online_cpus multiplier: without it a container maxing all cores
            #shows as ~12.5% on an 8-core host instead of 100%.
            online_cpus = stats['cpu_stats'].get('online_cpus') or len(stats['cpu_stats'].get('cpu_usage', {}).get('percpu_usage', [])) or 1

            cpu_load = 0.0
            #First poll after start: precpu_stats baseline is all zeros, so the
            #delta covers the entire container lifetime → bogus 100+% spike.
            if precpu_total > 0 and system_delta > 0 and cpu_delta > 0:
                cpu_load = round((cpu_delta / system_delta) * online_cpus * 100.0, 1)

            uptime_str = "Live"
            try:
                started_at_raw = container.attrs['State']['StartedAt']
                #Docker uses 0001-01-01T00:00:00Z for containers that were
                #created but never started (or freshly created). Parsing it
                #produces absurd uptimes like "17755750h 0m".
                if not started_at_raw or started_at_raw.startswith("0001"):
                    uptime_str = "Never started"
                else:
                    clean_time = started_at_raw.split('.')[0]
                    if clean_time.endswith('Z'):
                        clean_time = clean_time[:-1]
                    started_at = datetime.strptime(clean_time, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    delta = now - started_at
                    hours, remainder = divmod(int(delta.total_seconds()), 3600)
                    minutes, _ = divmod(remainder, 60)
                    uptime_str = f"{hours}h {minutes}m"
            except Exception:
                pass

            return {"id": server_id, "status": status, "container_id": container.short_id,
                    "image": image_name, "cpu_load": cpu_load, "ram_used": ram_used,
                    "ram_allocated": ram_allocated, "uptime": uptime_str}
        except Exception:
            return {"id": server_id, "status": "error", "container_id": "", "image": "",
                    "cpu_load": 0.0, "ram_used": 0.0, "ram_allocated": 0.0, "uptime": "Error"}

    def start_server(self, server_id: str, image_repo: str, ports: dict, env_vars: dict,
                     container_path: str = "/data", network_mode: str = "bridge",
                     ram_limit: str = None, autostart: bool = True, extra_volumes: dict = None,
                     extra_args: list = None, remote_host: str = None,
                     devices: list = None, group_add: list = None):
        """Start or create a Docker container."""
        target_client = self._get_client(server_id, remote_host)
        container = self._get_container_safe(target_client, server_id)

        if container:
            if autostart:
                try:
                    container.start()
                except Exception as e:
                    return {"status": "error", "message": f"Docker refused to start: {str(e)}"}
            return {"status": "success"}

        local_data_path = os.path.expanduser(f"~/Documents/server_data/{server_id}")
        os.makedirs(local_data_path, exist_ok=True)

        override_path = os.path.join(local_data_path, "lunk_overrides.json")
        if os.path.exists(override_path):
            try:
                with open(override_path, "r") as f:
                    overrides = json.load(f)
                    ram_limit = overrides.get("ram_limit") or ram_limit
                    game_version = overrides.get("game_version") or env_vars.get("VERSION")
                    if game_version and game_version != "unknown":
                        env_vars["VERSION"] = game_version
            except Exception as e:
                print(f"Failed to process overrides for {server_id}: {e}")

        if ram_limit and "minecraft" in image_repo.lower():
            env_vars["MEMORY"] = ram_limit.upper()

        try:
            target_client.images.pull(image_repo)
            print(f"[Docker] Image '{image_repo}' ready.")
        except Exception as pull_err:
            #Local-only images (e.g. lunkserverbot:latest) can't be pulled.
            #If the image exists locally, proceed; otherwise it's a real error.
            try:
                target_client.images.get(image_repo)
                print(f"[Docker] Image '{image_repo}' found locally (no pull needed).")
            except Exception:
                return {"status": "error", "message": f"Failed to pull image: {pull_err}"}

        container_volumes = {local_data_path: {'bind': container_path, 'mode': 'rw'}}
        if extra_volumes:
            container_volumes.update(extra_volumes)

        try:
            container = target_client.containers.create(
                image=image_repo,
                name=server_id,
                command=extra_args,
                ports=ports if network_mode != "host" else None,
                environment=env_vars,
                detach=True,
                network_mode=network_mode,
                volumes=container_volumes,
                mem_limit=ram_limit,
                devices=devices,
                group_add=group_add
            )
            if autostart:
                container.start()
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def stop_server(self, server_id: str, remote_host: str = None):
        """Stop a Docker container."""
        target_client = self._get_client(server_id, remote_host)
        container = self._get_container_safe(target_client, server_id)
        try:
            if container:
                container.stop(timeout=10)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def stop_server_group(self, server_ids: list):
        """Stop a group of containers."""
        results = []
        for sid in server_ids:
            result = self.stop_server(server_id=sid)
            results.append({"server_id": sid, "status": result.get("status"), "message": result.get("message")})
        return {"results": results}

    def restart_server(self, server_id: str, remote_host: str = None):
        """Restart a Docker container."""
        target_client = self._get_client(server_id, remote_host)
        container = self._get_container_safe(target_client, server_id)
        try:
            if container:
                container.restart(timeout=10)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def redeploy_server(self, server_id: str, recipe: dict):
        """Remove and recreate a container from recipe."""
        remote_host = recipe.get("remote_host")
        target_client = self._get_client(server_id, remote_host)
        container = self._get_container_safe(target_client, server_id)

        try:
            if container:
                container.remove(force=True)
        except Exception as e:
            return {"status": "error", "message": f"Failed to remove container: {str(e)}"}

        return self.start_server(
            server_id=server_id,
            image_repo=recipe["image"],
            ports=recipe.get("ports", {}),
            env_vars=recipe.get("env", {}),
            container_path=recipe.get("container_path", "/data"),
            network_mode=recipe.get("network_mode", "bridge"),
            ram_limit=recipe.get("ram_limit"),
            autostart=False,
            extra_volumes=recipe.get("extra_volumes"),
            remote_host=remote_host,
            devices=recipe.get("devices"),
            group_add=recipe.get("group_add")
        )

    def check_for_update(self, server_id: str):
        """Pull the image tag and compare digest to the running container.

        Docker skips unchanged layers so the pull is near-instant when current.
        The existing Force Rebuild (redeploy_server) IS the apply step — it
        re-pulls and recreates. This method just reports whether one is needed.
        """
        recipe = SERVER_RECIPES.get(server_id, {})
        if SATELLITE_IP in str(recipe.get("remote_host", "")):
            return {"error": "Satellite hosts manage images separately"}

        target_client = self._get_client(server_id)
        container = self._get_container_safe(target_client, server_id)
        if not container:
            return {"error": "Container not found"}

        image_tag = recipe.get("image") or (container.image.tags[0] if container.image.tags else None)
        if not image_tag:
            return {"error": "No image tag to check"}

        #Use the pinned digest from attrs — container.image.id triggers a
        #lazy API call and can resolve to a DIFFERENT image if the tag was
        #re-pulled. attrs["Image"] is the digest the container was created with.
        current_id = container.attrs.get("Image", container.image.id)

        try:
            pulled = target_client.images.pull(image_tag)
        except Exception:
            #Local-only images (lunkserverbot:latest, odysseus:local) have no
            #registry — same fallback pattern as start_server().
            try:
                target_client.images.get(image_tag)
                return {"error": "Local-only image (no registry to check)"}
            except Exception:
                return {"error": "Failed to pull image for update check"}

        return {"update_available": pulled.id != current_id}

    def send_command(self, server_id: str, command: str, command_template: str, remote_host: str = None):
        """Send command to running container via exec."""
        target_client = self._get_client(server_id, remote_host)
        container = self._get_container_safe(target_client, server_id)
        try:
            if not container or container.status != 'running':
                return {"status": "error", "message": "Server is not running."}
            exec_command = command_template.replace("{command}", command)
            container.exec_run(exec_command, detach=True)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_container_history(self, server_id: str, lines: int = 50, remote_host: str = None):
        """Get container log history."""
        target_client = self._get_client(server_id, remote_host)
        container = self._get_container_safe(target_client, server_id)
        try:
            if not container:
                return ["[System] Container not found or not created yet."]
            raw_logs = container.logs(tail=lines, stream=False)
            return raw_logs.decode('utf-8').splitlines()
        except Exception as e:
            return [f"[System] Error reading history: {str(e)}"]

    def create_snapshot(self, server_id: str, recipe: dict, retention_limit: int = 6):
        """Zero-downtime hot tar.gz backup with retention cleanup.

        Backups land OUTSIDE the source tree (~/Documents/server_backups/)
        so tarring doesn't archive previous backups forever. Minecraft gets
        a save-off/save-all freeze for corruption-free hot copies.

        Per-recipe 'backup_excludes' skips top-level dirs (e.g. Jellyfin's
        own backups/, transcodes/, log/, temp/) that bloat snapshots.
        """
        source_dir = os.path.expanduser(f"~/Documents/server_data/{server_id}")
        backup_dir = os.path.expanduser(f"~/Documents/server_backups/{server_id}")
        if not os.path.exists(source_dir):
            return {"status": "error", "message": f"No data found at {source_dir}"}
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        archive_path = os.path.join(backup_dir, f"snapshot_{timestamp}.tar.gz")

        excludes = set(recipe.get("backup_excludes", []))

        #Freeze running minecraft so the tarball isn't half-written.
        game_type = recipe.get("game_type")
        template = recipe.get("command_template", "{command}")
        froze = False
        if game_type == "minecraft":
            try:
                container = self._get_container_safe(self._get_client(server_id), server_id)
                if container and container.status in ['running', 'restarting']:
                    self.send_command(server_id, "save-off", template)
                    self.send_command(server_id, "save-all", template)
                    froze = True
            except Exception:
                pass

        try:
            with tarfile.open(archive_path, "w:gz") as tar:
                for entry in os.listdir(source_dir):
                    if entry in excludes:
                        continue
                    tar.add(os.path.join(source_dir, entry), arcname=entry)
        finally:
            if froze:
                self.send_command(server_id, "save-on", template)

        #Retention: oldest first.
        existing = sorted(glob.glob(os.path.join(backup_dir, "*.tar.gz")), key=os.path.getctime)
        for old in existing[:-retention_limit]:
            os.remove(old)
        return {"status": "success", "message": f"Snapshot saved: snapshot_{timestamp}.tar.gz"}

    def get_backup_list(self, server_id: str, recipe: dict):
        """Scan manager snapshots and native in-game saves, unified list."""
        backups = []

        manager_dir = os.path.expanduser(f"~/Documents/server_backups/{server_id}")
        if os.path.exists(manager_dir):
            for f in glob.glob(os.path.join(manager_dir, "*.tar.gz")):
                backups.append({
                    "filename": os.path.basename(f),
                    "type": "Lunkserver Backup",
                    "timestamp": os.path.getctime(f),
                    "size_mb": round(os.path.getsize(f) / (1024 * 1024), 2)
                })

        if recipe.get("native_auto_save"):
            save_path = recipe.get("native_save_path", "")
            ext = recipe.get("native_save_ext", "")
            native_dir = os.path.expanduser(f"~/Documents/server_data/{server_id}/{save_path}")
            if os.path.exists(native_dir):
                for f in glob.glob(os.path.join(native_dir, f"**/*{ext}"), recursive=True):
                    backups.append({
                        "filename": os.path.basename(f),
                        "type": "In-Game Auto-Save",
                        "timestamp": os.path.getctime(f),
                        "size_mb": round(os.path.getsize(f) / (1024 * 1024), 2)
                    })

        backups.sort(key=lambda x: x["timestamp"], reverse=True)
        for b in backups:
            b["timestamp"] = time.strftime('%Y-%m-%d %I:%M %p', time.localtime(b["timestamp"]))
        return {"status": "success", "backups": backups}

    def delete_backup(self, server_id: str, filename: str, backup_type: str, recipe: dict):
        """Safely delete a backup file (manager snapshot or native save)."""
        #Prevent path traversal — only filenames, no dirs.
        if "/" in filename or "\\" in filename:
            return {"status": "error", "message": "Invalid filename."}
        try:
            if backup_type == "Lunkserver Backup":
                filepath = os.path.expanduser(f"~/Documents/server_backups/{server_id}/{filename}")
                if os.path.exists(filepath):
                    os.remove(filepath)
                    return {"status": "success", "message": "Backup deleted."}
                return {"status": "error", "message": "File not found."}

            #Native saves can be nested in subdirs — walk to find them,
            #same as restore_backup does. get_backup_list uses recursive glob,
            #so the list can show files delete can't reach without walking.
            save_path = recipe.get("native_save_path", "")
            native_dir = os.path.expanduser(f"~/Documents/server_data/{server_id}/{save_path}")
            if os.path.exists(native_dir):
                for root, _, files in os.walk(native_dir):
                    if filename in files:
                        os.remove(os.path.join(root, filename))
                        return {"status": "success", "message": "Backup deleted."}
            return {"status": "error", "message": "File not found."}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def restore_backup(self, server_id: str, filename: str, backup_type: str, recipe: dict):
        """Stop server, restore target data (game-aware), restart."""
        if "/" in filename or "\\" in filename:
            return {"status": "error", "message": "Invalid filename."}
        game_type = recipe.get("game_type")
        try:
            target_client = self._get_client(server_id)
            container = self._get_container_safe(target_client, server_id)

            #==========================================
            #1. LUNKSERVER TARBALLS
            #==========================================
            if backup_type == "Lunkserver Backup":
                if container and container.status in ['running', 'restarting']:
                    container.stop(timeout=15)
                backup_path = os.path.expanduser(f"~/Documents/server_backups/{server_id}/{filename}")
                source_dir = os.path.expanduser(f"~/Documents/server_data/{server_id}")
                if not os.path.exists(backup_path):
                    return {"status": "error", "message": "Backup archive not found."}
                if os.path.exists(source_dir):
                    shutil.rmtree(source_dir)
                os.makedirs(source_dir, exist_ok=True)
                shutil.unpack_archive(backup_path, source_dir, 'gztar')
                if container:
                    container.start()
                return {"status": "success", "message": f"{filename} restored successfully!"}

            #==========================================
            #2. NATIVE GAME SAVES
            #==========================================
            if game_type in ["factorio", "satisfactory"]:
                save_path = recipe.get("native_save_path", "")
                native_dir = os.path.expanduser(f"~/Documents/server_data/{server_id}/{save_path}")
                #Find the backup file while the server is STILL running.
                target_path = None
                for root, dirs, files in os.walk(native_dir):
                    if filename in files:
                        target_path = os.path.join(root, filename)
                        break
                if not target_path:
                    return {"status": "error", "message": f"Could not find {filename} on the disk."}

                #------------------------------------------
                #FACTORIO (the pre-stash overwrite trick)
                #------------------------------------------
                if game_type == "factorio":
                    env_vars = recipe.get("env", {})
                    save_name = env_vars.get("SAVE_NAME", "save")
                    final_save = os.path.join(native_dir, f"{save_name}.zip")
                    #Stash a copy of the backup BEFORE stopping the container
                    #(stopping generates an unwanted Exit Save that would overwrite).
                    stash_path = os.path.join(native_dir, "restore_stash.tmp")
                    shutil.copy2(target_path, stash_path)
                    if container and container.status in ['running', 'restarting']:
                        container.stop(timeout=15)
                    shutil.move(stash_path, final_save)
                    os.utime(final_save, None)
                    try:
                        os.chown(final_save, 845, 845)
                    except Exception:
                        pass
                    if container:
                        container.start()
                    return {"status": "success", "message": f"{filename} safely restored!"}

                #------------------------------------------
                #SATISFACTORY (the archive shield)
                #------------------------------------------
                if game_type == "satisfactory":
                    if container and container.status in ['running', 'restarting']:
                        container.stop(timeout=60)
                    archive_dir = os.path.join(native_dir, "archived_autosaves")
                    os.makedirs(archive_dir, exist_ok=True)
                    ext = recipe.get("native_save_ext", "")
                    #Move everything out of the way first.
                    for root, dirs, files in os.walk(native_dir):
                        if root == archive_dir:
                            continue
                        for file in files:
                            if file.endswith(ext):
                                current_path = os.path.join(root, file)
                                if current_path != target_path:
                                    dest_path = os.path.join(archive_dir, file)
                                    if os.path.exists(dest_path):
                                        name, extension = os.path.splitext(file)
                                        dest_path = os.path.join(archive_dir, f"{name}_{int(time.time())}{extension}")
                                    shutil.move(current_path, dest_path)
                    final_save = os.path.join(native_dir, "RestoredSave.sav")
                    if target_path != final_save:
                        shutil.copy(target_path, final_save)
                        os.utime(final_save, None)
                    try:
                        os.chown(final_save, 1000, 1000)
                    except Exception:
                        pass
                    if container:
                        container.start()
                    return {"status": "success", "message": "Backup copied! Open Satisfactory -> Server Manager -> Manage Saves, and load 'RestoredSave'."}

            #==========================================
            #3. FALLBACK
            #==========================================
            if container:
                if container.status in ['running', 'restarting']:
                    container.stop(timeout=15)
                container.start()
            return {"status": "success", "message": "Server restarted."}
        except Exception as e:
            return {"status": "error", "message": f"Restore failed: {str(e)}"}

    def list_mods(self, server_id: str):
        """List .jar/.zip mods from the host bind-mount (works while offline)."""
        try:
            mods_dir = os.path.expanduser(f"~/Documents/server_data/{server_id}/mods")
            if not os.path.exists(mods_dir):
                return {"status": "success", "mods": []}
            mods = [f for f in os.listdir(mods_dir) if f.endswith('.jar') or f.endswith('.zip')]
            return {"status": "success", "mods": mods}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def install_mod(self, server_id: str, download_url: str, filename: str):
        """Download a mod file directly to the host mods dir."""
        if "/" in filename or "\\" in filename:
            return {"status": "error", "message": "Invalid filename."}
        try:
            mods_dir = os.path.expanduser(f"~/Documents/server_data/{server_id}/mods")
            os.makedirs(mods_dir, exist_ok=True)
            target_path = os.path.join(mods_dir, filename)
            #Modrinth CDN requires a unique User-Agent or it 403s.
            req = urllib.request.Request(download_url, headers={'User-Agent': 'Lunkman/LunkServerManager/1.0.0'})
            with urllib.request.urlopen(req, timeout=60) as response, open(target_path, 'wb') as out_file:
                out_file.write(response.read())
            return {"status": "success"}
        except Exception as e:
            print(f"[Backend Error] Mod download failed: {str(e)}")
            return {"status": "error", "message": str(e)}

    def delete_mod(self, server_id: str, filename: str):
        """Delete a mod file from the host mods dir."""
        if "/" in filename or "\\" in filename:
            return {"status": "error", "message": "Invalid filename."}
        try:
            mods_dir = os.path.expanduser(f"~/Documents/server_data/{server_id}/mods")
            target_path = os.path.join(mods_dir, filename)
            if os.path.exists(target_path):
                os.remove(target_path)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
