"""Satellite Backend — Lightweight resource stats service for LunkserverManager.

All Docker management stays on the master dashboard. This microservice
just reports CPU, RAM, disk, VRAM, and bandwidth stats for remote hosts
that can't be reached via Docker SSH (e.g., NAT'd VPS on Tailscale).
"""
import os
import re
import json
import subprocess
import glob
import psutil
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

#Load env from working directory with no systemd drop-in needed
load_dotenv()

#Dashboard origin from env on the satellite host, falls back to the production domain
DASHBOARD_ORIGIN = os.environ.get("DASHBOARD_ORIGIN", "https://<DOMAIN>")

app = FastAPI(title="Lunkserver Satellite", version="2.0.0")

#CORS restricted to the dashboard origin only
app.add_middleware(
    CORSMiddleware,
    allow_origins=[DASHBOARD_ORIGIN],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def get_cpu_stats() -> dict:
    """Get CPU usage percentage."""
    per_core = psutil.cpu_percent(interval=1, percpu=True)
    return {
        "percent": round(sum(per_core) / len(per_core), 1) if per_core else 0,
        "cores": psutil.cpu_count(),
        "per_core": per_core
    }


def get_ram_stats() -> dict:
    """Get RAM usage statistics."""
    vm = psutil.virtual_memory()
    return {
        "percent": vm.percent,
        "used_gb": round(vm.used / (1024**3), 2),
        "total_gb": round(vm.total / (1024**3), 2),
        "available_gb": round(vm.available / (1024**3), 2)
    }


def get_disk_stats() -> dict:
    """Get disk usage statistics."""
    disk = psutil.disk_usage('/')
    return {
        "percent": disk.percent,
        "used_gb": round(disk.used / (1024**3), 2),
        "total_gb": round(disk.total / (1024**3), 2),
        "free_gb": round(disk.free / (1024**3), 2)
    }


def get_vram_stats() -> dict:
    """Get GPU VRAM statistics using AMD ROCm or sysfs."""
    result = {"status": "unknown", "used_gb": 0.0, "total_gb": 0.0, "gpu_name": "Unknown"}

    #Method one, rocm smi for AMD GPUs
    try:
        proc = subprocess.run(
            ["/opt/rocm/bin/rocm-smi", "--showmeminfo", "vram"],
            capture_output=True, text=True, timeout=5
        )
        if proc.returncode == 0 and "GPU" in proc.stdout:
            for line in proc.stdout.strip().split('\n'):
                if "GPU[0]" in line and "Total Memory (B)" in line:
                    match = re.search(r'Total Memory \(B\):\s*(\d+)', line)
                    if match:
                        result["total_gb"] = round(int(match.group(1)) / (1024**3), 2)
                if "GPU[0]" in line and "Total Used Memory (B)" in line:
                    match = re.search(r'Used Memory \(B\):\s*(\d+)', line)
                    if match:
                        result["used_gb"] = round(int(match.group(1)) / (1024**3), 2)
            result["status"] = "ok"
            result["gpu_name"] = "AMD (ROCm)"
            return result
    except Exception:
        pass

    #Method two, sysfs for AMD and Intel
    for card_dir in glob.glob("/sys/class/drm/card[0-9]*"):
        if not os.path.isdir(card_dir) or "-render" in card_dir:
            continue
        vram_total = os.path.join(card_dir, "device/mem_info_vram_total")
        vram_used = os.path.join(card_dir, "device/mem_info_vram_used")
        if os.path.exists(vram_total) and os.path.exists(vram_used):
            try:
                total = int(open(vram_total).read().strip())
                used = int(open(vram_used).read().strip())
                if total > 0:
                    result["total_gb"] = round(total / (1024**3), 2)
                    result["used_gb"] = round(used / (1024**3), 2)
                    result["status"] = "ok"
                    result["gpu_name"] = "sysfs"
                    return result
            except (ValueError, PermissionError):
                continue

    return result


def get_vnstat_bandwidth() -> dict:
    """Get bandwidth stats from vnstat on the host."""
    result = {
        "status": "ok",
        "percent": 0,
        "text": "0 GB / 3000 GB",
        "used": "0 GB",
        "lifetime_gb": 0,
        "reset_date": "Resets Unknown",
        "per_server_bandwidth": {}
    }

    try:
        proc = subprocess.run(
            ["vnstat", "--json"],
            capture_output=True, text=True, timeout=10
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            iface = data.get("interfaces", [{}])[0]
            months = iface.get("traffic", {}).get("month", [])

            if months:
                current = months[-1]
                total_bytes = current.get("rx", 0) + current.get("tx", 0)
                total_gb = total_bytes / (1024**3)
                percent = (total_bytes / (3000 * (1024**3))) * 100

                #Calculate lifetime as the sum of all months
                lifetime_bytes = sum(m.get("rx", 0) + m.get("tx", 0) for m in months)
                lifetime_gb = lifetime_bytes / (1024**3)

                result["percent"] = round(percent, 1)
                result["text"] = f"{total_gb:.1f} GB / 3000 GB"
                result["used"] = f"{total_gb:.1f} GB"
                result["lifetime_gb"] = round(lifetime_gb, 1)
                #Vnstat 2.x month entry has date year month rx and tx
                mdate = current.get("date", {})
                if isinstance(mdate, dict) and mdate.get("month"):
                    result["reset_date"] = f"Resets month {mdate['month']}"
                elif current.get("name"):
                    result["reset_date"] = f"Resets {current['name']}"
    except Exception:
        pass

    return result


@app.get("/api/health")
async def health_check():
    """Simple health check endpoint."""
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "lunkserver-satellite"
    }


@app.get("/api/containers")
async def get_containers():
    """Get all container statuses from docker ps."""
    try:
        proc = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}"],
            capture_output=True, text=True, timeout=5
        )
        containers = {}
        if proc.returncode == 0:
            for line in proc.stdout.strip().split('\n'):
                if '|' in line:
                    name, status = line.split('|', 1)
                    containers[name.strip()] = status.strip()
        return {"containers": containers}
    except Exception as e:
        return {"error": str(e), "containers": {}}


@app.get("/api/stats")
async def get_stats():
    """Get all resource statistics including bandwidth."""
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "cpu": get_cpu_stats(),
        "ram": get_ram_stats(),
        "disk": get_disk_stats(),
        "vram": get_vram_stats(),
        "bandwidth": get_vnstat_bandwidth()
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
