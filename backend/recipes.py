import os
import json

#CurseForge API key from env so it is never committed, required for auto curseforge modpack servers
CF_API_KEY = os.environ.get("CF_API_KEY", "")

#Path to user created recipes loaded at import and on each CRUD call, hand written recipes stay in this file, user created ones live separately so git diffs stay clean
USER_RECIPES_PATH = os.path.expanduser("~/Documents/server_data/user_recipes.json")

#Templates for the add server picker, each has the correct image ports container path env vars and a ram default
SERVER_TEMPLATES = {
    #Games
    "factorio": {
        "label": "Factorio",
        "category": "Games",
        "image": "factoriotools/factorio:stable",
        "ports": {"34197/udp": 34197, "27015/tcp": 27015},
        "client_port": 34197,
        "container_path": "/factorio",
        "game_type": "factorio",
        "game_version": "2.0",
        "env": {"GENERATE_NEW_SAVE": "true", "PUID": "1000", "PGID": "1000"},
        "ram_limit": "4g",
        "command_template": "rcon {command}",
        "version_options": [
            {"value": "stable", "label": "Stable (2.0)"},
            {"value": "latest", "label": "Experimental (2.1)"},
        ],
        "config_files": {
            "Server Settings": "config/server-settings.json",
            "Map Gen Settings": "config/map-gen-settings.json",
            "Map Settings": "config/map-settings.json"
        },
    },
    "minecraft": {
        "label": "Minecraft (Java)",
        "category": "Games",
        "image": "itzg/minecraft-server:latest",
        "ports": {"25565/tcp": 25565},
        "client_port": 25565,
        "container_path": "/data",
        "game_type": "minecraft",
        "game_version": "latest",
        "env": {"EULA": "TRUE", "TYPE": "FABRIC", "PUID": "1000", "GID": "1000"},
        "ram_limit": "4g",
        "command_template": "rcon-cli {command}",
        "version_options": [
            {"value": "FABRIC", "label": "Fabric (Mods)"},
        ],
        "config_files": {
            "Server Properties": "server.properties",
            "Whitelist": "whitelist.json",
            "Operators (Admins)": "ops.json",
            "Banned Players": "banned-players.json"
        },
    },
    "terraria": {
        "label": "Terraria (tShock)",
        "category": "Games",
        "image": "ryshe/terraria:latest",
        "ports": {"7777/tcp": 7777},
        "client_port": 7777,
        "container_path": "/root/.local/share/Terraria",
        "game_type": "terraria",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "2g",
        "command_template": "echo 'Terraria CLI not supported'",
    },
    "valheim": {
        "label": "Valheim Dedicated Server",
        "category": "Games",
        "image": "lloesche/valheim-server:latest",
        "ports": {"2456/udp": 2456, "2457/udp": 2457},
        "client_port": 2456,
        "container_path": "/config",
        "game_type": "valheim",
        "game_version": "latest",
        "env": {"SERVER_NAME": "Lunkserver Valheim", "WORLD_NAME": "Lunkworld", "PUID": "1000", "PGID": "1000"},
        "ram_limit": "4g",
        "command_template": "echo 'Valheim CLI not supported'",
    },
    "palworld": {
        "label": "Palworld Dedicated Server",
        "category": "Games",
        "image": "thijsvanloef/palworld-server-docker:latest",
        "ports": {"8211/udp": 8211},
        "client_port": 8211,
        "container_path": "/palworld",
        "game_type": "palworld",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "12g",
        "command_template": "echo 'Palworld CLI not supported'",
    },
    "satisfactory": {
        "label": "Satisfactory Dedicated Server",
        "category": "Games",
        "image": "wolveix/satisfactory-server:latest",
        "ports": {"7777/udp": 7777, "7777/tcp": 7777, "8888/tcp": 8888},
        "client_port": 7777,
        "container_path": "/config",
        "game_type": "satisfactory",
        "game_version": "1.1",
        "env": {"MAXPLAYERS": "4", "PUID": "1000", "PGID": "1000", "AUTOPAUSE": "true"},
        "ram_limit": "12g",
        "command_template": "echo 'Satisfactory CLI not supported'",
    },
    "cs2": {
        "label": "CS2 Dedicated Server",
        "category": "Games",
        "image": "joedwards32/cs2:latest",
        "ports": {"27015/tcp": 27015, "27015/udp": 27015, "27020/udp": 27020},
        "client_port": 27015,
        "container_path": "/data",
        "game_type": "container",
        "game_version": "latest",
        "env": {"SRCDS_TOKEN": "", "CS2_SERVERNAME": "Lunkserver CS2", "CS2_LAN": "1"},
        "ram_limit": "4g",
        "command_template": "echo 'CS2 CLI not supported'",
    },
    #Media
    "jellyfin": {
        "label": "Lunkflix Media Server",
        "category": "Media",
        "image": "jellyfin/jellyfin:latest",
        "ports": {"8096/tcp": 8096},
        "client_port": 8096,
        "container_path": "/config",
        "game_type": "media",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "2g",
        "command_template": "echo 'Lunkflix web UI only'",
    },
    "navidrome": {
        "label": "Navidrome Music Server",
        "category": "Media",
        "image": "deluan/navidrome:latest",
        "ports": {"4533/tcp": 4533},
        "client_port": 4533,
        "container_path": "/data",
        "game_type": "media",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "512m",
        "command_template": "echo 'Navidrome web UI only'",
    },
    "audiobookshelf": {
        "label": "Audiobookshelf (Audiobooks/Podcasts)",
        "category": "Media",
        "image": "ghcr.io/advplyr/audiobookshelf:latest",
        "ports": {"80/tcp": 8033},
        "client_port": 8033,
        "container_path": "/config",
        "game_type": "media",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "1g",
        "command_template": "echo 'Audiobookshelf web UI only'",
    },
    "immich": {
        "label": "Immich (Self-hosted Google Photos)",
        "category": "Media",
        "image": "ghcr.io/immich-app/immich-server:release",
        "ports": {"3001/tcp": 3003},
        "client_port": 3003,
        "container_path": "/usr/src/app/upload",
        "game_type": "media",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "2g",
        "command_template": "echo 'Immich web UI only'",
    },
    #Reading
    "komga": {
        "label": "Komga (Manga/Comics)",
        "category": "Reading",
        "image": "gotson/komga:latest",
        "ports": {"25600/tcp": 25600},
        "client_port": 25600,
        "container_path": "/config",
        "game_type": "media",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "1g",
        "command_template": "echo 'Komga web UI only'",
    },
    "kavita": {
        "label": "Kavita (Manga/Books)",
        "category": "Reading",
        "image": "jvmilazz0/kavita:latest",
        "ports": {"5000/tcp": 5000},
        "client_port": 5000,
        "container_path": "/kavita/config",
        "game_type": "media",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "1g",
        "command_template": "echo 'Kavita web UI only'",
    },
    #Utility
    "adguardhome": {
        "label": "AdGuard Home (DNS Adblocker)",
        "category": "Utility",
        "image": "adguard/adguardhome:latest",
        "ports": {"53/udp": 53, "53/tcp": 53, "3000/tcp": 3000},
        "client_port": 3000,
        "container_path": "/opt/adguardhome/work",
        "game_type": "utility",
        "game_version": "latest",
        "env": {},
        "ram_limit": "256m",
        "command_template": "echo 'AdGuard web UI only'",
    },
    "uptime-kuma": {
        "label": "Uptime Kuma (Status Page)",
        "category": "Utility",
        "image": "louislam/uptime-kuma:1",
        "ports": {"3001/tcp": 3001},
        "client_port": 3001,
        "container_path": "/app/data",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "256m",
        "command_template": "echo 'Uptime Kuma web UI only'",
    },
    "grafana": {
        "label": "Grafana (Dashboards)",
        "category": "Utility",
        "image": "grafana/grafana:latest",
        "ports": {"3000/tcp": 3000},
        "client_port": 3000,
        "container_path": "/var/lib/grafana",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "256m",
        "command_template": "echo 'Grafana web UI only'",
    },
    "vaultwarden": {
        "label": "Vaultwarden (Password Manager)",
        "category": "Utility",
        "image": "vaultwarden/server:latest",
        "ports": {"80/tcp": 8022},
        "client_port": 8022,
        "container_path": "/data",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"DOMAIN": "https://vault.local", "SIGNUPS_ALLOWED": "false"},
        "ram_limit": "256m",
        "command_template": "echo 'Vaultwarden web UI only'",
    },
    "gitea": {
        "label": "Gitea (Self-hosted Git)",
        "category": "Utility",
        "image": "gitea/gitea:latest",
        "ports": {"3000/tcp": 3000, "22/tcp": 2222},
        "client_port": 3000,
        "container_path": "/data",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "512m",
        "command_template": "echo 'Gitea web UI only'",
    },
    "nextcloud": {
        "label": "Nextcloud (Cloud Storage)",
        "category": "Utility",
        "image": "lscr.io/linuxserver/nextcloud:latest",
        "ports": {"443/tcp": 443},
        "client_port": 443,
        "container_path": "/config",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "1g",
        "command_template": "echo 'Nextcloud web UI only'",
    },
    "homepage": {
        "label": "Homepage (Dashboard)",
        "category": "Utility",
        "image": "ghcr.io/gethomepage/homepage:latest",
        "ports": {"3000/tcp": 3002},
        "client_port": 3002,
        "container_path": "/app/config",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "128m",
        "command_template": "echo 'Homepage web UI only'",
    },
    "bookstack": {
        "label": "BookStack (Wiki/Docs)",
        "category": "Utility",
        "image": "lscr.io/linuxserver/bookstack:latest",
        "ports": {"80/tcp": 8068},
        "client_port": 8068,
        "container_path": "/config",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "512m",
        "command_template": "echo 'BookStack web UI only'",
    },
    "pi-hole": {
        "label": "Pi-hole (DNS Adblocker)",
        "category": "Utility",
        "image": "pihole/pihole:latest",
        "ports": {"53/tcp": 53, "53/udp": 53, "80/tcp": 8053},
        "client_port": 8053,
        "container_path": "/etc/pihole",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"TZ": "America/Chicago", "FTL_CMD": "no-daemon"},
        "ram_limit": "256m",
        "command_template": "echo 'Pi-hole web UI only'",
    },
    "syncthing": {
        "label": "Syncthing (File Sync)",
        "category": "Utility",
        "image": "lscr.io/linuxserver/syncthing:latest",
        "ports": {"8384/tcp": 8384, "22000/tcp": 22000, "22000/udp": 22000},
        "client_port": 8384,
        "container_path": "/config",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "256m",
        "command_template": "echo 'Syncthing web UI only'",
    },
    "portainer": {
        "label": "Portainer (Docker Manager)",
        "category": "Utility",
        "image": "portainer/portainer-ce:latest",
        "ports": {"9000/tcp": 9000, "8000/tcp": 8000},
        "client_port": 9000,
        "container_path": "/data",
        "game_type": "utility",
        "game_version": "latest",
        "env": {},
        "ram_limit": "256m",
        "command_template": "echo 'Portainer web UI only'",
    },
    "dozzle": {
        "label": "Dozzle (Log Viewer)",
        "category": "Utility",
        "image": "amir20/dozzle:latest",
        "ports": {"8080/tcp": 8080},
        "client_port": 8080,
        "container_path": "/data",
        "game_type": "utility",
        "game_version": "latest",
        "env": {},
        "ram_limit": "128m",
        "command_template": "echo 'Dozzle web UI only'",
    },
    "redis": {
        "label": "Redis (In-memory Database)",
        "category": "Utility",
        "image": "redis:latest",
        "ports": {"6379/tcp": 6379},
        "client_port": 6379,
        "container_path": "/data",
        "game_type": "utility",
        "game_version": "latest",
        "env": {},
        "ram_limit": "256m",
        "command_template": "echo 'Redis CLI: redis-cli'",
    },
    "redisinsight": {
        "label": "Redis Insight (Redis GUI)",
        "category": "Utility",
        "image": "redislabs/redisinsight:latest",
        "ports": {"8001/tcp": 8001},
        "client_port": 8001,
        "container_path": "/db",
        "game_type": "utility",
        "game_version": "latest",
        "env": {},
        "ram_limit": "256m",
        "command_template": "echo 'RedisInsight web UI only'",
    },

    #Development
    "code-server": {
        "label": "Code Server (VS Code in Browser)",
        "category": "Development",
        "image": "lscr.io/linuxserver/code-server:latest",
        "ports": {"8443/tcp": 8443},
        "client_port": 8443,
        "container_path": "/config",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "1g",
        "command_template": "echo 'Code Server web UI only'",
    },
    "gitlab": {
        "label": "GitLab CE (DevOps Platform)",
        "category": "Development",
        "image": "gitlab/gitlab-ce:latest",
        "ports": {"80/tcp": 8089, "443/tcp": 8444, "22/tcp": 2223},
        "client_port": 8089,
        "container_path": "/var/opt/gitlab",
        "game_type": "utility",
        "game_version": "latest",
        "env": {},
        "ram_limit": "4g",
        "command_template": "echo 'GitLab web UI only'",
    },
    "drawio": {
        "label": "Draw.io (Diagram Editor)",
        "category": "Development",
        "image": "jgraph/drawio:latest",
        "ports": {"8080/tcp": 8090},
        "client_port": 8090,
        "container_path": "/data",
        "game_type": "utility",
        "game_version": "latest",
        "env": {},
        "ram_limit": "256m",
        "command_template": "echo 'Draw.io web UI only'",
    },
    "excalidraw": {
        "label": "Excalidraw (Whiteboard)",
        "category": "Development",
        "image": "excalidraw/excalidraw:latest",
        "ports": {"80/tcp": 8091},
        "client_port": 8091,
        "container_path": "/data",
        "game_type": "utility",
        "game_version": "latest",
        "env": {},
        "ram_limit": "128m",
        "command_template": "echo 'Excalidraw web UI only'",
    },

    #More games
    "foundry-vtt": {
        "label": "Foundry VTT (Tabletop RPG)",
        "category": "Games",
        "image": "felddy/foundryvtt:latest",
        "ports": {"30000/tcp": 30000},
        "client_port": 30000,
        "container_path": "/data",
        "game_type": "container",
        "game_version": "latest",
        "env": {"FOUNDRY_USERNAME": "", "FOUNDRY_PASSWORD": ""},
        "ram_limit": "2g",
        "command_template": "echo 'Foundry VTT web UI only'",
    },
    "v-rising": {
        "label": "V Rising Dedicated Server",
        "category": "Games",
        "image": "josdenoit/vrising-server:latest",
        "ports": {"27015/udp": 27015, "27016/udp": 27016},
        "client_port": 27015,
        "container_path": "/data",
        "game_type": "container",
        "game_version": "latest",
        "env": {},
        "ram_limit": "4g",
        "command_template": "echo 'V Rising CLI not supported'",
    },
    "rust": {
        "label": "Rust Dedicated Server",
        "category": "Games",
        "image": "didstopia/rust-server:latest",
        "ports": {"28015/tcp": 28015, "28015/udp": 28015, "28115/tcp": 28115},
        "client_port": 28015,
        "container_path": "/data",
        "game_type": "container",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "8g",
        "command_template": "echo 'Rust CLI not supported'",
    },
    "7dtd": {
        "label": "7 Days to Die Dedicated Server",
        "category": "Games",
        "image": "ich777/7dtd-server:latest",
        "ports": {"26900/udp": 26900, "26900/tcp": 26900, "8082/tcp": 8082},
        "client_port": 26900,
        "container_path": "/data",
        "game_type": "container",
        "game_version": "latest",
        "env": {},
        "ram_limit": "8g",
        "command_template": "echo '7DTD CLI not supported'",
    },

    #More media
    "plex": {
        "label": "Plex Media Server",
        "category": "Media",
        "image": "lscr.io/linuxserver/plex:latest",
        "ports": {"32400/tcp": 32400},
        "client_port": 32400,
        "container_path": "/config",
        "game_type": "media",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "4g",
        "command_template": "echo 'Plex web UI only'",
    },
    "tautulli": {
        "label": "Tautulli (Plex/Jellyfin Analytics)",
        "category": "Media",
        "image": "lscr.io/linuxserver/tautulli:latest",
        "ports": {"8181/tcp": 8181},
        "client_port": 8181,
        "container_path": "/config",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "512m",
        "command_template": "echo 'Tautulli web UI only'",
    },
    "bazarr": {
        "label": "Bazarr (Subtitle Manager)",
        "category": "Media",
        "image": "lscr.io/linuxserver/bazarr:latest",
        "ports": {"6767/tcp": 6767},
        "client_port": 6767,
        "container_path": "/config",
        "game_type": "media",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "512m",
        "command_template": "echo 'Bazarr web UI only'",
    },
    "overseerr": {
        "label": "Overseerr (Request Manager)",
        "category": "Media",
        "image": "lscr.io/linuxserver/overseerr:latest",
        "ports": {"5055/tcp": 5055},
        "client_port": 5055,
        "container_path": "/config",
        "game_type": "media",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "512m",
        "command_template": "echo 'Overseerr web UI only'",
    },
    "photoprism": {
        "label": "PhotoPrism (Photo Management)",
        "category": "Media",
        "image": "photoprism/photoprism:latest",
        "ports": {"2342/tcp": 2342},
        "client_port": 2342,
        "container_path": "/photoprism/storage",
        "game_type": "media",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "2g",
        "command_template": "echo 'PhotoPrism web UI only'",
    },

    #Home automation
    "homeassistant": {
        "label": "Home Assistant (Smart Home Hub)",
        "category": "Home Automation",
        "image": "lscr.io/linuxserver/homeassistant:latest",
        "ports": {"8123/tcp": 8123},
        "client_port": 8123,
        "container_path": "/config",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "1g",
        "command_template": "echo 'Home Assistant web UI only'",
    },
    "node-red": {
        "label": "Node-RED (Flow-Based Automation)",
        "category": "Home Automation",
        "image": "nodered/node-red:latest",
        "ports": {"1880/tcp": 1880},
        "client_port": 1880,
        "container_path": "/data",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "512m",
        "command_template": "echo 'Node-RED web UI only'",
    },
    "zigbee2mqtt": {
        "label": "Zigbee2MQTT (IoT Bridge)",
        "category": "Home Automation",
        "image": "koenkk/zigbee2mqtt:latest",
        "ports": {"8080/tcp": 8092},
        "client_port": 8092,
        "container_path": "/app/data",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "256m",
        "command_template": "echo 'Zigbee2MQTT web UI only'",
    },
    "mosquitto": {
        "label": "Eclipse Mosquitto (MQTT Broker)",
        "category": "Home Automation",
        "image": "eclipse-mosquitto:latest",
        "ports": {"1883/tcp": 1883, "9001/tcp": 9001},
        "client_port": 1883,
        "container_path": "/mosquitto",
        "game_type": "utility",
        "game_version": "latest",
        "env": {},
        "ram_limit": "128m",
        "command_template": "echo 'Mosquitto MQTT broker'",
    },
    "esphome": {
        "label": "ESPHome (Custom IoT Firmware)",
        "category": "Home Automation",
        "image": "esphome/esphome:latest",
        "ports": {"6052/tcp": 6052},
        "client_port": 6052,
        "container_path": "/config",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "256m",
        "command_template": "echo 'ESPHome web UI only'",
    },

    #Networking
    "wireguard": {
        "label": "WireGuard (VPN Server)",
        "category": "Networking",
        "image": "lscr.io/linuxserver/wireguard:latest",
        "ports": {"51820/udp": 51820},
        "client_port": 51820,
        "container_path": "/config",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "128m",
        "command_template": "echo 'WireGuard VPN server'",
    },
    "cloudflared": {
        "label": "Cloudflare Tunnel (Zero Trust)",
        "category": "Networking",
        "image": "cloudflare/cloudflared:latest",
        "ports": {},
        "client_port": None,
        "container_path": "/etc/cloudflared",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"TUNNEL_TOKEN": ""},
        "ram_limit": "128m",
        "command_template": "echo 'Cloudflare Tunnel'",
    },
    "nginx-proxy-manager": {
        "label": "Nginx Proxy Manager",
        "category": "Networking",
        "image": "jc21/nginx-proxy-manager:latest",
        "ports": {"80/tcp": 8081, "8181/tcp": 8182, "443/tcp": 444},
        "client_port": 81,
        "container_path": "/data",
        "game_type": "utility",
        "game_version": "latest",
        "env": {},
        "ram_limit": "256m",
        "command_template": "echo 'NPM web UI only'",
    },
    "headscale": {
        "label": "Headscale (Self-hosted Tailscale)",
        "category": "Networking",
        "image": "headscale/headscale:latest",
        "ports": {"8080/tcp": 8093},
        "client_port": 8093,
        "container_path": "/var/lib/headscale",
        "game_type": "utility",
        "game_version": "latest",
        "env": {},
        "ram_limit": "128m",
        "command_template": "echo 'Headscale control server'",
    },

    #Monitoring
    "prometheus": {
        "label": "Prometheus (Metrics Collection)",
        "category": "Monitoring",
        "image": "prom/prometheus:latest",
        "ports": {"9090/tcp": 9090},
        "client_port": 9090,
        "container_path": "/prometheus",
        "game_type": "utility",
        "game_version": "latest",
        "env": {},
        "ram_limit": "512m",
        "command_template": "echo 'Prometheus web UI only'",
    },
    "loki": {
        "label": "Loki (Log Aggregation)",
        "category": "Monitoring",
        "image": "grafana/loki:latest",
        "ports": {"3100/tcp": 3100},
        "client_port": 3100,
        "container_path": "/loki",
        "game_type": "utility",
        "game_version": "latest",
        "env": {},
        "ram_limit": "512m",
        "command_template": "echo 'Loki API only'",
    },
    "changedetection": {
        "label": "Changedetection.io (Website Monitor)",
        "category": "Monitoring",
        "image": "ghcr.io/dgtlmoon/changedetection.io:latest",
        "ports": {"5000/tcp": 5001},
        "client_port": 5001,
        "container_path": "/datastore",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"PUID": "1000", "PGID": "1000"},
        "ram_limit": "256m",
        "command_template": "echo 'Changedetection web UI only'",
    },

    #Security
    "authentik": {
        "label": "Authentik (Identity Provider / SSO)",
        "category": "Security",
        "image": "ghcr.io/goauthentik/server:latest",
        "ports": {"9000/tcp": 9001, "9443/tcp": 9444},
        "client_port": 9001,
        "container_path": "/media",
        "game_type": "utility",
        "game_version": "latest",
        "env": {"AUTHENTIK_SECRET_KEY": ""},
        "ram_limit": "2g",
        "command_template": "echo 'Authentik web UI only'",
    },
    "crowdsec": {
        "label": "CrowdSec (Collaborative IPS)",
        "category": "Security",
        "image": "crowdsecurity/crowdsec:latest",
        "ports": {"8080/tcp": 8095},
        "client_port": 8095,
        "container_path": "/var/lib/crowdsec/data",
        "game_type": "utility",
        "game_version": "latest",
        "env": {},
        "ram_limit": "256m",
        "command_template": "echo 'CrowdSec web UI only'",
    },
    "wazuh": {
        "label": "Wazuh (SIEM / XDR)",
        "category": "Security",
        "image": "wazuh/wazuh-manager:latest",
        "ports": {"1514/udp": 1514, "1515/tcp": 1515, "55000/tcp": 55000},
        "client_port": 55000,
        "container_path": "/var/ossec/data",
        "game_type": "utility",
        "game_version": "latest",
        "env": {},
        "ram_limit": "1g",
        "command_template": "echo 'Wazuh API'",
    },
}

def _load_user_recipes():
    """Load user-created recipes from JSON overlay file."""
    try:
        with open(USER_RECIPES_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_user_recipe(server_id, recipe):
    """Persist a user-created recipe to JSON overlay + update SERVER_RECIPES."""
    user_recipes = _load_user_recipes()
    user_recipes[server_id] = recipe
    os.makedirs(os.path.dirname(USER_RECIPES_PATH), exist_ok=True)
    with open(USER_RECIPES_PATH, "w") as f:
        json.dump(user_recipes, f, indent=2)
    SERVER_RECIPES[server_id] = recipe

def delete_user_recipe(server_id):
    """Remove a user-created recipe from JSON overlay + SERVER_RECIPES."""
    user_recipes = _load_user_recipes()
    if server_id in user_recipes:
        del user_recipes[server_id]
        with open(USER_RECIPES_PATH, "w") as f:
            json.dump(user_recipes, f, indent=2)
    SERVER_RECIPES.pop(server_id, None)

def create_from_template(template_key, server_id, name=None, description=""):
    """Build a recipe from a template and persist it.
    Returns (recipe_dict, error_message). error_message is None on success."""
    template = SERVER_TEMPLATES.get(template_key)
    if not template:
        return None, f"Unknown template '{template_key}'"
    if server_id in SERVER_RECIPES:
        return None, f"Server '{server_id}' already exists"
    recipe = {
        "name": name or template["label"],
        "game_type": template["game_type"],
        "game_version": template.get("game_version", "latest"),
        "image": template["image"],
        "ports": template["ports"].copy(),
        "client_port": template["client_port"],
        "container_path": template["container_path"],
        "command_template": template.get("command_template", "echo 'No CLI interface'"),
        "env": template["env"].copy(),
        "ram_limit": template.get("ram_limit", "0g"),
        "description": description or f"{template['label']} server",
        "_user_created": True,
        "_template": template_key,
    }
    if template.get("config_files"):
        recipe["config_files"] = template["config_files"].copy()
    if template.get("version_options"):
        recipe["version_options"] = [v.copy() for v in template["version_options"]]
    save_user_recipe(server_id, recipe)
    return recipe, None

def get_template_for_image(image):
    """Look up a template by Docker image name.
    Returns template dict or None if no match (manual mode)."""
    image_base = image.split(":")[0].lower()
    for t in SERVER_TEMPLATES.values():
        tmpl_base = t["image"].split(":")[0].lower()
        if image_base == tmpl_base:
            return t
    return None

def create_custom_recipe(server_id, name, image, ports, container_path="/data",
                         client_port=None, env=None, ram_limit="0g",
                         game_type="container", description=""):
    """Build a recipe from user-provided values (manual mode) and persist it.
    Returns (recipe_dict, error_message). error_message is None on success."""
    if server_id in SERVER_RECIPES:
        return None, f"Server '{server_id}' already exists"
    if not client_port and ports:
        client_port = list(ports.values())[0]
    recipe = {
        "name": name,
        "game_type": game_type,
        "game_version": "latest",
        "image": image,
        "ports": ports or {},
        "client_port": client_port,
        "container_path": container_path,
        "command_template": "echo 'No CLI interface'",
        "env": env or {},
        "ram_limit": ram_limit,
        "description": description or f"Custom container: {image}",
        "_user_created": True,
        "_template": "custom",
    }
    save_user_recipe(server_id, recipe)
    return recipe, None

#Containers we never auto discover like Hermes agent pods and FRP tunnel
_DISCOVERY_BLACKLIST_PREFIXES = ("hermes", "frpc", "frps", "network_frp")
_DISCOVERY_BLACKLIST_IMAGES = ("nikolaik/python-nodejs",)

def discover_containers():
    """Scan local Docker for running/stopped containers not in SERVER_RECIPES.
    Returns dict of auto-generated recipes keyed by container name."""
    import docker
    try:
        client = docker.from_env()
    except Exception:
        return {}
    discovered = {}
    for c in client.containers.list(all=True):
        name = c.name
        if name is None:
            continue
        if name.startswith(_DISCOVERY_BLACKLIST_PREFIXES) or name in SERVER_RECIPES:
            continue
        image = c.attrs.get("Config", {}).get("Image", "")
        if image and image.startswith(_DISCOVERY_BLACKLIST_IMAGES):
            continue
        attrs = c.attrs
        ports = {}
        port_bindings = attrs.get("HostConfig", {}).get("PortBindings") or {}
        for container_port, bindings in port_bindings.items():
            for b in bindings:
                host_port = int(b.get("HostPort", 0))
                if host_port:
                    ports[container_port] = host_port
        container_path = "/data"
        has_server_volume = False
        for m in attrs.get("Mounts", []):
            if m.get("Type") == "bind" and "server_data" in m.get("Source", ""):
                container_path = m["Destination"]
                has_server_volume = True
                break
        if not ports and not has_server_volume:
            continue
        client_port = list(ports.values())[0] if ports else None
        discovered[name] = {
            "name": name.replace("_", " ").replace("-", " ").title(),
            "game_type": "container",
            "image": attrs.get("Config", {}).get("Image", ""),
            "ports": ports,
            "client_port": client_port,
            "container_path": container_path,
            "command_template": "echo 'No CLI command interface detected'",
            "ram_limit": f"{attrs.get('HostConfig', {}).get('Memory', 0) // (1024**3)}g" if attrs.get("HostConfig", {}).get("Memory") else "0g",
            "description": f"Auto-discovered from Docker image {attrs.get('Config', {}).get('Image', '')}",
            "_auto": True,
        }
    return discovered

SERVER_RECIPES = {
    "jellyfin": {
        "name": "Lunkflix Media Server",
        "game_type": "media",
        "game_version": "latest",
        "image": "jellyfin/jellyfin:latest",
        "backup_excludes": ["backups", "transcodes", "log", "temp"],
        "ports": {
            "8096/tcp": 8096,
            "8920/tcp": 8920,
            "1900/udp": 1900,
            "7359/udp": 7359
        },
        "client_port": 8096,
        "container_path": "/config",
        "command_template": "echo 'Lunkflix does not support native CLI commands. Manage via the Web UI.'",
        "env": {
            "TZ": "America/Chicago",
            "PUID": "1000",
            "PGID": "1000"
        },
        "ram_limit": "4g",
        "description": "LUNKFLIX FOREVER!!!",
        "extra_volumes": {
            "/home/<USER>/Documents/shared_sportyfin_data": {"bind": "/sportyfin-data", "mode": "ro"}
        },
        "devices": [
            "/dev/dri:/dev/dri"
        ],
        "group_add": [
            "992",
            "44"
        ]
    },

    "wizarr": {
        "name": "Wizarr Invite System",
        "game_type": "utility",
        "game_version": "latest",
        "image": "ghcr.io/wizarrrr/wizarr:latest",
        "backup_excludes": ["backups"],
        "ports": {
            "5690/tcp": 5690
        },
        "client_port": 5690,
        "container_path": "/data/database",
        "command_template": "echo 'Wizarr runs entirely via Web UI.'",
        "env": {
            "TZ": "America/Chicago"
        },
        "ram_limit": "512mb",
        "description": "Lunkflix Security Guard"
    },

    "llama-swap": {
        "name": "LLM Engine",
        "game_type": "utility",
        "image": "ghcr.io/mostlygeek/llama-swap:vulkan",
        "ports": {"8080/tcp": 8080},
        "remote_host": "ssh://<USER>@<TAILSCALE_IP>",
        "alias": "Lunkserver 3.0",
        "description": "Actual container running the LunkLLMs"
    },

    "minecraft_01": {
        "name": "Lunkworld 26",
        "game_type": "minecraft",
        "game_version": "26.1.2",
        "image": "itzg/minecraft-server:latest",
        "ports": {"25565/tcp": 25565},
        "client_port": 25565,
        "container_path": "/data",
        "command_template": "rcon-cli {command}",
        "env": {
            "EULA": "TRUE",
            "TYPE": "FABRIC",
            "VERSION": "26.1.2",
            "MEMORY": "4G",
            "ENABLE_WHITELIST": "true"
        },
        "ram_limit": "4g",
        "description": "Vanilla Minecraft -- Lunkworld 26",
        "config_files": {
            "Server Properties": "server.properties",
            "Whitelist": "whitelist.json",
            "Operators (Admins)": "ops.json",
            "Banned Players": "banned-players.json"
        }
    },

    "minecraft_02": {
        "name": "Lunkworld 24",
        "game_type": "minecraft",
        "game_version": "26.1.2",
        "image": "itzg/minecraft-server:latest",
        "ports": {"25565/tcp": 25566},
        "client_port": 25566,
        "container_path": "/data",
        "command_template": "rcon-cli {command}",
        "env": {
            "EULA": "TRUE",
            "TYPE": "FABRIC",
            "VERSION": "26.1.2",
            "MEMORY": "4G",
            "ENABLE_WHITELIST": "true"
        },
        "ram_limit": "4g",
        "description": "Vanilla Minecraft -- Lunkworld 24",
        "config_files": {
            "Server Properties": "server.properties",
            "Whitelist": "whitelist.json",
            "Operators (Admins)": "ops.json",
            "Banned Players": "banned-players.json"
        }
    },

    "minecraft_03": {
        "name": "Heavily Modded Minecraft",
        "game_type": "minecraft",
        "image": "itzg/minecraft-server:latest",
        "ram_limit": "8g",
        "ports": {"25565/tcp": 25567 },
        "client_port": 25567,
        "container_path": "/data",
        "command_template": "rcon-cli '{command}'",
        "env": {
            "EULA": "TRUE",
            "MEMORY": "8G",
            "TYPE": "AUTO_CURSEFORGE",
            "ENABLE_WHITELIST": "true",
            "CF_API_KEY": CF_API_KEY,
            "CF_PAGE_URL": "https://www.curseforge.com/minecraft/modpacks/all-the-mods-10/files/8091114"
        },
        "description": "Modded Minecraft -- All The Mods 10, 1.21.1",
        "config_files": {
            "Server Properties": "server.properties",
            "Whitelist": "whitelist.json",
            "Operators (Admins)": "ops.json",
            "Banned Players": "banned-players.json"
        }
    },

    "factorio_01": {
        "name": "Lunktorio",
        "game_type": "factorio",
        "game_version": "2.0",
        "image": "factoriotools/factorio:stable",
        "ports": {
            "34197/udp": 34197,
            "27015/tcp": 27015
        },
        "client_port": 34197,
        "container_path": "/factorio",
        "command_template": "rcon {command}",
        "env": {
            "GENERATE_NEW_SAVE": "true",
            "SAVE_NAME": "lunktorioFINALThisTimeForSureRightGuys",
            "PUID": "1000",
            "PGID": "1000"
        },
        "ram_limit": "3g",
        "description": "This time we'll focus on defense?",
        "native_auto_save": True,
        "native_save_path": "saves",
        "native_save_ext": ".zip",
        "config_files": {
            "Server Settings": "config/server-settings.json",
            "Map Gen Settings": "config/map-gen-settings.json",
            "Map Settings": "config/map-settings.json"
        }
    },

    "satisfactory_01": {
        "name": "Lunkfactory",
        "game_type": "satisfactory",
        "game_version": "1.1",
        "image": "wolveix/satisfactory-server:latest",
        "ports": {
            "7777/udp": 7777,
            "7777/tcp": 7777,
            "8888/tcp": 8888
        },
        "client_port": 7777,
        "container_path": "/config",
        "command_template": "echo 'Satisfactory native RCON is not supported via CLI'",
        "env": {
            "MAXPLAYERS": "4",
            "PUID": "1000",
            "PGID": "1000",
            "AUTOPAUSE": "true"
        },
        "ram_limit": "12g",
        "description": "I got it back from them :the_happy_whale:",
        "native_auto_save": True,
        "native_save_path": "saved/server",
        "native_save_ext": ".sav",
        "config_files": {
            "Server Settings": "Saved/Config/LinuxServer/ServerSettings.ini",
            "Game Config": "Saved/Config/LinuxServer/Game.ini",
            "Engine Tweaks": "Saved/Config/LinuxServer/Engine.ini"
        }
    },

    "adguardhome": {
        "name": "AdGuard Home",
        "game_type": "utility",
        "game_version": "edge",
        "image": "adguard/adguardhome:edge",
        "ports": {
            "3000/tcp": 3000,
            "80/tcp": 80,
            "53/udp": 53
        },
        "client_port": 80,
        "container_path": "/opt/adguardhome/conf",
        "extra_volumes": {
            "/home/<USER>/Documents/server_data/adguardhome/work": {"bind": "/opt/adguardhome/work", "mode": "rw"}
        },
        "command_template": "echo 'AdGuard Home is managed via the Web UI (port 80 or 3000 for initial setup)'",
        "env": {
            "TZ": "America/Chicago"
        },
        "ram_limit": "256mb",
        "description": "Network-wide ad & tracker blocking DNS server",
        "config_files": {
            "Main Config": "AdGuardHome.yaml",
            "Query Log": "querylog.yml",
            "Filtering Rules": "filters.txt"
        }
    },

    "odysseus": {
        "name": "Odysseus AI",
        "game_type": "ai",
        "image": "odysseus:local",
        "ports": {"7000/tcp": 7000},
        "client_port": 7000,
        "remote_host": "ssh://<USER>@<TAILSCALE_IP>",
        "alias": "Lunkserver 3.0",
        "description": "LunkAI Frontend",
        "ram_limit": "8g",
        "group": "odysseyus-stack",
        "compose_file": "/home/<USER>/Documents/odysseus/docker-compose.yml"
    },

    "chromadb": {
        "name": "ChromaDB Vector Store",
        "game_type": "ai",
        "image": "chromadb/chroma:latest",
        "ports": {"8100/tcp": 8100},
        "client_port": 8100,
        "remote_host": "ssh://<USER>@<TAILSCALE_IP>",
        "alias": "Lunkserver 3.0",
        "description": "LunkAI database",
        "group": "odysseyus-stack",
        "compose_file": "/home/<USER>/Documents/odysseus/docker-compose.yml"
    },

    "searxng": {
        "name": "SearXNG Search",
        "game_type": "ai",
        "image": "searxng/searxng:2026.5.31-7159b8aed",
        "ports": {"8080/tcp": 8081},
        "client_port": 8081,
        "remote_host": "ssh://<USER>@<TAILSCALE_IP>",
        "alias": "Lunkserver 3.0",
        "description": "LunkAI search engine",
        "group": "odysseyus-stack",
        "compose_file": "/home/<USER>/Documents/odysseus/docker-compose.yml"
    },

    "ntfy": {
        "name": "NTfy Notifications",
        "game_type": "ai",
        "image": "binwiederhier/ntfy",
        "ports": {"8091/tcp": 8091},
        "client_port": 8091,
        "remote_host": "ssh://<USER>@<TAILSCALE_IP>",
        "alias": "Lunkserver 3.0",
        "description": "LunkAI notification system",
        "command_template": "echo 'NTfy runs via `ntfy serve` in the compose stack'",
        "group": "odysseyus-stack",
        "compose_file": "/home/<USER>/Documents/odysseus/docker-compose.yml"
    },

    "lunkbot": {
        "name": "Lunkbot",
        "game_type": "utility",
        "image": "lunkserverbot:latest",
        "ports": {},
        "container_path": "/data",
        "extra_volumes": {
            "/home/<USER>/Documents/server_data/jellyfin": {"bind": "/jellyfin", "mode": "ro"},
            "/home/<USER>/Documents/LunkserverDiscordBot/.env": {"bind": "/app/.env", "mode": "ro"}
        },
        "env": {},
        "ram_limit": "512mb",
        "description": "Discord bot companion for fleet status + Lunkflix leaderboards"
    },

    "rag-demo": {
        "name": "RAG Demo",
        "game_type": "utility",
        "image": "rag-demo:latest",
        "ports": {"8501/tcp": 8501},
        "client_port": 8501,
        "container_path": "/data",
        "command_template": "echo 'RAG Demo runs via Web UI'",
        "env": {},
        "ram_limit": "2g",
        "description": "DeployHub RAG support bot for internship demo"
    }
}

#Load user created recipes from the JSON overlay
try:
    SERVER_RECIPES.update(_load_user_recipes())
except Exception:
    pass

#Auto discover unmanaged Docker containers and merge them in
try:
    SERVER_RECIPES.update(discover_containers())
except Exception:
    pass
