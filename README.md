# LunkserverManager

A self-hosted game server fleet dashboard built on FastAPI and SvelteKit.
Manage Docker game containers (Minecraft, Factorio, Satisfactory), media
servers, AI stacks, and remote hosts from a single web UI.

## Highlights

- **Multi-host orchestration.** Start, stop, and redeploy Docker containers on
  local and remote hosts through a unified API.
- **Real-time telemetry.** Live CPU, RAM, disk, and GPU VRAM stats stream over
  WebSocket. Poll a single dashboard endpoint for the full fleet view.
- **Declarative server recipes.** Define servers as data. The backend builds
  the container, maps ports, and wires the tunnel from one config dict.
- **Automated CI/CD.** Three GitHub Actions workflows deploy on push to
  `master`. Each workflow triggers only when its component changes.
- **Zero-downtime tunnel hot-reload.** The backend rewrites the tunnel config
  on every server state change and hot-reloads it without a process restart.
- **Role-based access control.** Token-based auth with admin and guest roles.
  Token comparison uses constant-time comparison to prevent timing attacks.
- **Backup and restore.** Snapshot any server volume and restore from the UI.
- **Mod management.** Search and install Minecraft mods from Modrinth. Search
  and install Factorio mods from the Factorio mod portal.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, Uvicorn, Docker SDK, psutil |
| Frontend | SvelteKit, Vite, TypeScript |
| Infrastructure | Docker, Docker Compose, systemd, nginx, pm2 |
| Networking | Tailscale mesh VPN, FRP (Fast Reverse Proxy) |
| CI/CD | GitHub Actions, path-based triggers |
| Monitoring | WebSocket logs, psutil, vnstat |

## Architecture

```
┌─────────────────────────┐        ┌──────────────────────┐       ┌──────────────────────┐
│  SvelteKit Frontend     │        │  Remote Docker Host  │       │  Satellite Backend   │
│  Served behind nginx    │        │  (managed via SSH)   │       │  FastAPI microservice│
├─────────────────────────┤        │                      │       │                      │
│  FastAPI Backend        │─ SSH ─▶│  Game containers     │       │  System telemetry    │
│  Docker socket access   │        │  AI / media services │       │  Bandwidth stats     │
│  Automated backups      │        │                      │       │                      │
│  Tunnel automation      │        └──────────────────────┘       └──────────────────────┘
└─────────────────────────┘                  │                              │
                                             └── Tailscale mesh ────────────┘
```

### Components

| Component | Path | Role |
|-----------|------|------|
| Backend API | `backend/` | FastAPI service. Docker management, stats, backups, tunnel automation. Runs as a systemd unit. |
| SvelteKit Frontend | `frontend/` | Dashboard UI. Served behind nginx in production via pm2. |
| Satellite Backend | `satellite-backend/` | Lightweight stats microservice for remote hosts without Docker SSH access. |

### Network Topology

- **Local host**: Runs the dashboard backend and frontend. It has direct Docker
  socket access.
- **Remote Docker host**: Managed via SSH over Tailscale. Runs game containers
  and the AI stack.
- **Cloud VPS**: Runs the satellite stats microservice and the tunnel server.
  Connected via Tailscale.

All hosts connect through a [Tailscale](https://tailscale.com) mesh VPN.

## Quick Start

### Prerequisites

- Python 3.12+ with venv
- Node.js 18+ and npm
- Docker Engine and Docker Compose
- SSH key access to all remote hosts
- Tailscale (for inter-host networking)

### Backend Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run
uvicorn main:app --host 127.0.0.1 --port 8000
```

### Frontend Setup

```bash
cd frontend
npm install
npm run dev -- --port 5173
```

### Satellite Backend Setup

```bash
cd satellite-backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8765
```

## Server Recipes

Server definitions live in `backend/recipes.py` as the `SERVER_RECIPES` dict.
Each entry defines:

```python
"server_id": {
    "game_type": "minecraft",          # UI category + backup behavior
    "image": "itzg/minecraft-server",  # Docker image
    "ports": {"25565/tcp": 25565},     # Container to host port map
    "client_port": 25565,              # Primary port shown in UI
    "env": {"EULA": "TRUE"},           # Container env vars
    "ram_limit": "4g",                 # Docker memory limit
    "remote_host": "ssh://user@host",  # Optional: SSH target for remote Docker
    "compose_file": "/path/to/compose.yml",  # Optional: docker-compose group
    "config_files": {"Settings": "server.properties"},  # Editable via UI
}
```

Add a new server by adding one dict entry. The backend handles the rest.

## API Reference

### System

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/system` | None | Local CPU, RAM, disk stats. |
| GET | `/api/vram` | None | Local GPU VRAM stats. |
| GET | `/api/dashboard` | None | Consolidated telemetry (system + fleet + satellites in one call). |

### Fleet

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/servers` | None | All server statuses (lightweight). |
| GET | `/api/servers/{id}` | guest+ | Detailed stats for one server. |
| GET | `/api/fleet/groups` | None | Servers grouped by host. |
| GET | `/api/containers/groups` | None | Docker-compose container groups. |
| GET | `/api/remote-hosts` | None | Remote host system stats. |
| GET | `/api/satellites` | None | Satellite microservice stats. |
| GET | `/api/vps-telem` | None | VPS bandwidth stats. |

### Server Control

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/servers/{id}/start` | guest+ | Start a server. |
| POST | `/api/servers/{id}/stop` | admin | Stop a server. |
| POST | `/api/servers/{id}/restart` | admin | Restart a server. |
| POST | `/api/redeploy/{id}` | admin | Destroy and rebuild container from recipe. |
| POST | `/api/servers/{id}/command` | admin | Send game console command. |
| WS | `/api/servers/{id}/logs` | None | Live console log stream. |

### Group Operations

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/servers/start-group` | admin | Start multiple servers. |
| POST | `/api/servers/stop-group` | admin | Stop multiple servers. |
| POST | `/api/servers/restart-group` | admin | Restart multiple servers. |

### Configuration

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/servers/{id}/settings` | guest+ | Read server config file. |
| POST | `/api/servers/{id}/settings` | admin | Save server config (auto-restarts). |
| GET | `/api/servers/{id}/hardware` | guest+ | Read RAM and version overrides. |
| POST | `/api/servers/{id}/hardware` | admin | Save overrides and rebuild. |
| DELETE | `/api/servers/{id}/hardware` | admin | Reset to defaults and rebuild. |

### Backups

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/servers/{id}/backups` | guest+ | List all backups. |
| POST | `/api/servers/{id}/backup` | admin | Create manual snapshot. |
| POST | `/api/servers/{id}/backups/{file}/restore` | admin | Restore a backup. |
| DELETE | `/api/servers/{id}/backups/{file}` | admin | Delete a backup. |

### Mods

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/modrinth/search` | None | Search Modrinth mod database. |
| GET | `/api/servers/{id}/mods` | None | List installed mods. |
| POST | `/api/servers/{id}/mods/install` | admin | Download and install a Minecraft mod. |
| DELETE | `/api/servers/{id}/mods/{file}` | admin | Delete a mod. |
| GET | `/api/factorio/mods/search` | None | Search Factorio mod portal. |
| GET | `/api/factorio/mods/{name}` | None | Factorio mod details and releases. |
| POST | `/api/servers/{id}/factorio/mods/install` | admin | Download and install a Factorio mod. |

## Security

- **No secrets in source.** The app reads all tokens, API keys, and
  credentials from environment variables. Nothing is hardcoded.
- **Constant-time token comparison.** Token checks use `hmac.compare_digest`
  to prevent timing side-channel attacks.
- **Role-based access control.** Two roles: `admin` (full access) and `guest`
  (read-only). Every mutation endpoint requires admin.
- **Strict CORS.** The backend allows only known origins. No wildcards.
- **Path traversal guards.** All file operations (backups, mods, settings)
  validate paths against the server data root.
- **Satellite isolation.** The satellite backend allows CORS only from the
  dashboard origin.

## Testing

```bash
cd backend
source .venv/bin/activate
pytest tests/test_unit.py -v
```

For concurrent load testing (simulates dashboard polling under stress):

```bash
python tests/torture_dashboard.py
```

## Deployment

### CI/CD

All three services auto-deploy on push to `master` via GitHub Actions:

| Workflow | Trigger | Target |
|----------|---------|--------|
| `deploy-backend.yml` | `backend/**` changes | Local host via Tailscale SSH |
| `deploy-vps-frontend.yml` | `frontend/**` changes | Cloud VPS via SSH |
| `deploy-satellite.yml` | `satellite-backend/**` changes | Cloud VPS via SSH and scp |

Each workflow connects to Tailscale, SSHes into the target host, pulls the
latest code, and restarts the service. Secrets come from GitHub Actions
Secrets. They are never stored in the repo.

## File Structure

```
LunkserverManager/
├── backend/
│   ├── main.py              # FastAPI app: routes, auth, background tasks
│   ├── docker_manager.py    # Docker container lifecycle, stats, backups
│   ├── stats_fetcher.py     # System stats: GPU VRAM, remote SSH, satellite
│   ├── recipes.py           # Server definitions (images, ports, env, configs)
│   └── tests/
│       ├── test_unit.py     # Unit tests (pytest)
│       └── torture_dashboard.py  # Concurrent load stress test
├── satellite-backend/
│   ├── main.py              # Lightweight stats microservice (FastAPI)
│   ├── deploy.sh            # systemd deployment script
│   ├── requirements.txt
│   └── satellite.service    # systemd unit file
├── frontend/
│   ├── package.json
│   └── src/
│       ├── lib/index.ts
│       └── routes/
│           ├── +layout.server.js    # Auth: token to role resolution
│           ├── +layout.svelte       # Global layout, sidebar, theme
│           ├── +page.svelte         # Dashboard overview page
│           └── server/[id]/+page.svelte  # Server detail page
├── .gitignore
└── README.md
```
