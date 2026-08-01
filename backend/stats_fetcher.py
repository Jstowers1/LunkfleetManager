import asyncio
import glob
import json
import os
import re
import subprocess
import threading
import time
import urllib.request

import httpx
import psutil

from recipes import SERVER_RECIPES

class StatsFetcher:
    def __init__(self):
        self._stats_cache = {}          # {host: (timestamp, result)}
        self._vps_cache = None          # (timestamp, result)
        self._satellite_cache = None    # (timestamp, result)
        self._CACHE_TTL = 30            #SSH polling cache
        self._VPS_CACHE_TTL = 60        #VPS cache
        self._SATELLITE_CACHE_TTL = 15  #Satellite cache
        self._lock = threading.Lock()

    def get_local_system_stats(self) -> dict:
        """CPU, RAM, and disk for the local machine."""
        cpu_pct = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        ram_total = vm.total / (1024**3)
        ram_used = vm.used / (1024**3)
        disk_total = disk.total / (1024**3)
        disk_used = disk.used / (1024**3)
        return {
            "cpu_percent": round(cpu_pct, 1),
            "ram_percent": round(vm.percent, 1),
            "ram_used": f"{ram_used:.2f}",
            "ram_total": f"{ram_total:.2f}",
            "storage_percent": round(disk.percent, 1),
            "storage_used": f"{disk_used:.2f}",
            "storage_total": f"{disk_total:.2f}",
        }

    def get_vram_stats(self) -> dict:
        """Local GPU VRAM via rocm-smi (AMD) with sysfs fallback."""
        result = {"status": "unknown", "vram_used": 0.0, "vram_total": 0.0, "gpu_name": "Unknown"}

        try:
            proc = subprocess.run(
                ["/opt/rocm/bin/rocm-smi", "--showmeminfo", "vram"],
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0 and "GPU" in proc.stdout:
                for line in proc.stdout.strip().split('\n'):
                    if "GPU[0]" in line and "Total Memory (B)" in line:
                        m = re.search(r'Total Memory \(B\):\s*(\d+)', line)
                        if m:
                            result["vram_total"] = round(int(m.group(1)) / (1024**3), 2)
                    if "GPU[0]" in line and "Total Used Memory (B)" in line:
                        m = re.search(r'Used Memory \(B\):\s*(\d+)', line)
                        if m:
                            result["vram_used"] = round(int(m.group(1)) / (1024**3), 2)
                if result["vram_total"] > 0 or result["vram_used"] > 0:
                    result["status"] = "ok"
                    result["gpu_name"] = "AMD (ROCm)"
                    return result
        except Exception:
            pass

        for card_dir in glob.glob("/sys/class/drm/card[0-9]*"):
            if not os.path.isdir(card_dir) or "-render" in card_dir:
                continue
            base = f"{card_dir}/device/"
            vram_t = os.path.join(base, "mem_info_vram_total")
            vram_u = os.path.join(base, "mem_info_vram_used")
            if os.path.exists(vram_t) and os.path.exists(vram_u):
                try:
                    total = int(open(vram_t).read().strip())
                    used = int(open(vram_u).read().strip())
                    if total > 0:
                        result["vram_total"] = round(total / (1024**3), 2)
                        result["vram_used"] = round(used / (1024**3), 2)
                        result["status"] = "ok"
                        result["gpu_name"] = "AMD (sysfs)"
                        return result
                except (ValueError, PermissionError):
                    continue

            intel_mem = os.path.join(base, "gt_total_mem")
            if os.path.exists(intel_mem):
                try:
                    total_kb = int(open(intel_mem).read().strip())
                    result["vram_total"] = round(total_kb / (1024**2), 2)
                    result["status"] = "ok"
                    result["gpu_name"] = "Intel iGPU"
                    return result
                except (ValueError, PermissionError):
                    continue

        return result

    def get_unique_remote_hosts(self) -> list:
        hosts = set()
        for sid, recipe in SERVER_RECIPES.items():
            host = recipe.get("remote_host")
            if host:
                hosts.add(host)
        return list(hosts)

    def get_remote_system_stats(self, remote_host: str) -> dict:
        """SSH remote host stats (CPU, RAM, disk, VRAM) with caching."""
        now = time.time()
        cache_key = remote_host

        with self._lock:
            if cache_key in self._stats_cache:
                cached_time, cached_result = self._stats_cache[cache_key]
                if now - cached_time < self._CACHE_TTL:
                    return cached_result

        ssh_target = remote_host.replace("ssh://", "")
        results = {"status": "unknown", "cpu": 0.0, "ram": 0.0, "disk": 0.0,
                   "vram_used": 0.0, "vram_total": 0.0, "gpu_name": "unknown"}

        #top -bn2: first sample is boot-average, second is current usage.
        #top -bn1 (old) reported the lifetime average, not current load.
        #Also try mpstat as a more accurate fallback if available.
        remote_cmd = (
            "top -bn2 -d 0.5 2>/dev/null | grep -i '^%Cpu' | tail -1 | "
            "awk '{i=1; while(i<=NF && $i!~/id/) i++; if(i<=NF) {gsub(/,/,\\\"\\\",$(i+1)); print 100-$(i+1)}}'; "
            "echo '|||'; "
            "free | awk 'NR==2{printf \\\"%.1f\\\", $3/$2 * 100}'; "
            "echo '|||'; "
            "df / 2>/dev/null | awk 'NR==2{gsub(/%/,\\\"\\\",$5); print $5}'; "
            "echo '|||'; "
            "if command -v rocm-smi >/dev/null 2>&1; then "
            "rocm-smi --showmeminfo vram 2>/dev/null | grep 'GPU[0-9]' | head -1; "
            "else echo ''; fi"
        )
        ssh_rc = -1
        try:
            proc = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
                 ssh_target, remote_cmd],
                capture_output=True, text=True, timeout=10,
            )
            raw = proc.stdout.strip()
            ssh_rc = proc.returncode
        except Exception:
            raw = ""

        parts = raw.split("|||")
        cpu_out = parts[0].strip() if len(parts) > 0 else ""
        #/proc/stat fallback — ONLY when SSH connected (rc==0) but top produced
        #no cpu line. Old code took a single snapshot (boot-average, not current).
        #Now takes two samples 0.5s apart and computes the real delta.
        if not cpu_out and ssh_rc == 0:
            try:
                proc = subprocess.run(
                    ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
                     ssh_target,
                     "S1=$(head -1 /proc/stat); sleep 0.5; S2=$(head -1 /proc/stat); "
                     "awk -v s1=\"$S1\" -v s2=\"$S2\" '"
                     "BEGIN{split(s1,a); split(s2,b); "
                     "t1=0; t2=0; "
                     "for(i=2;i<=length(a);i++){t1+=a[i]}; "
                     "for(i=2;i<=length(b);i++){t2+=b[i]}; "
                     "d=t2-t1; di=b[5]-a[5]; "
                     "if(d>0) printf \"%.1f\", (d-di)/d*100}'"],
                    capture_output=True, text=True, timeout=10,
                )
                cpu_out = proc.stdout.strip()
            except Exception:
                cpu_out = ""
        try:
            results["cpu"] = round(float(cpu_out), 1) if cpu_out and float(cpu_out) > 0 else 0.0
        except ValueError:
            results["cpu"] = 0.0

        ram_out = parts[1].strip() if len(parts) > 1 else ""
        try:
            results["ram"] = round(float(ram_out), 1) if ram_out else 0.0
        except ValueError:
            results["ram"] = 0.0

        disk_out = parts[2].strip() if len(parts) > 2 else ""
        try:
            results["disk"] = round(float(disk_out), 1) if disk_out else 0.0
        except ValueError:
            results["disk"] = 0.0

        vram_out = parts[3].strip() if len(parts) > 3 else ""
        if vram_out and "Used Memory" in vram_out:
            m = re.search(r'Used Memory \(B\):\s*(\d+)', vram_out)
            if m:
                results["vram_used"] = round(int(m.group(1)) / (1024**3), 2)
            m = re.search(r'Total Memory \(B\):\s*(\d+)', vram_out)
            if m:
                results["vram_total"] = round(int(m.group(1)) / (1024**3), 2)
            results["gpu_name"] = "AMD (ROCm)"
            results["status"] = "ok"
        #Local sysfs fallback for VRAM — ONLY for local stats. If this is a
        #remote SSH call, reading the dashboard host's /sys/class/drm would
        #falsely report local GPU as the remote host's VRAM.
        elif results["vram_total"] == 0 and not remote_host.startswith("ssh://"):
            for card_dir in glob.glob("/sys/class/drm/card[0-9]*"):
                if "-render" in card_dir:
                    continue
                base = f"{card_dir}/device/"
                vram_t = os.path.join(base, "mem_info_vram_total")
                vram_u = os.path.join(base, "mem_info_vram_used")
                if os.path.exists(vram_t) and os.path.exists(vram_u):
                    try:
                        t = int(open(vram_t).read().strip())
                        u = int(open(vram_u).read().strip())
                        if t > 0:
                            results["vram_total"] = round(t / (1024**3), 2)
                            results["vram_used"] = round(u / (1024**3), 2)
                            results["gpu_name"] = "AMD (sysfs)"
                            results["status"] = "ok"
                            break
                    except (ValueError, PermissionError):
                        continue
                intel_mem = os.path.join(base, "gt_total_mem")
                if os.path.exists(intel_mem):
                    try:
                        total_kb = int(open(intel_mem).read().strip())
                        results["vram_total"] = round(total_kb / (1024**2), 2)
                        results["gpu_name"] = "Intel iGPU (shared)"
                        results["status"] = "ok"
                        break
                    except (ValueError, PermissionError):
                        continue

        with self._lock:
            self._stats_cache[cache_key] = (now, results)
        return results

    async def _fetch_satellite_http(self, client, sat):
        """HTTP call to satellite microservice."""
        try:
            url = f"http://{sat['host']}:{sat['port']}/api/stats"
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json()
                return {
                    "host": sat["host"],
                    "alias": sat["alias"],
                    "stats": {
                        "cpu": data.get("cpu", {}).get("percent", 0),
                        "ram": data.get("ram", {}).get("percent", 0),
                        "ram_used": data.get("ram", {}).get("used_gb", 0),
                        "ram_total": data.get("ram", {}).get("total_gb", 0),
                        "disk": data.get("disk", {}).get("percent", 0),
                        "disk_used": data.get("disk", {}).get("used_gb", 0),
                        "disk_total": data.get("disk", {}).get("total_gb", 0),
                        "vram_used": data.get("vram", {}).get("used_gb", 0),
                        "vram_total": data.get("vram", {}).get("total_gb", 0),
                        "gpu_name": data.get("vram", {}).get("gpu_name", "Unknown"),
                        "status": "ok",
                    },
                }
            return {
                "host": sat["host"],
                "alias": sat["alias"],
                "stats": {
                    "cpu": 0, "ram": 0, "ram_used": 0, "ram_total": 0,
                    "disk": 0, "disk_used": 0, "disk_total": 0,
                    "vram_used": 0, "vram_total": 0,
                    "gpu_name": "Unknown", "status": "error"
                },
            }
        except Exception:
            return {
                "host": sat["host"],
                "alias": sat["alias"],
                "stats": {
                    "cpu": 0, "ram": 0, "ram_used": 0, "ram_total": 0,
                    "disk": 0, "disk_used": 0, "disk_total": 0,
                    "vram_used": 0, "vram_total": 0,
                    "gpu_name": "Unknown", "status": "offline"
                },
            }

    async def _get_satellite_stats_async(self) -> list:
        """Async satellite stats fetching."""
        satellites = [
            {"host": "<VPS_TAILSCALE_IP>", "alias": "LunkVPS", "port": 8765}
        ]
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                tasks = [self._fetch_satellite_http(client, sat) for sat in satellites]
                results = await asyncio.gather(*tasks)
                return list(results)
        except Exception as e:
            print(f"[StatsFetcher] Satellite gather failed: {type(e).__name__}: {e}")
            return [{"host": s["host"], "alias": s["alias"],
                     "stats": {"status": "offline", "cpu": 0, "ram": 0,
                               "ram_used": 0, "ram_total": 0,
                               "disk": 0, "disk_used": 0, "disk_total": 0,
                               "vram_used": 0, "vram_total": 0, "gpu_name": "Unknown"}}
                    for s in satellites]

    def get_satellite_stats(self) -> list:
        """Sync wrapper for satellite stats."""
        return asyncio.run(self._get_satellite_stats_async())

    def get_vps_bandwidth(self) -> dict:
        """VPS bandwidth data from remote microservice (cached 60s)."""
        now = time.time()
        with self._lock:
            if self._vps_cache:
                cache_time, cache_result = self._vps_cache
                if now - cache_time < self._VPS_CACHE_TTL:
                    return cache_result

        result = {
            "status": "ok", "percent": 0,
            "text": "0.0 GB / 3000 GB", "used": "0.0 GB",
            "lifetime_gb": 0, "reset_date": "Resets Unknown"
        }

        vps_url = "http://<VPS_TAILSCALE_IP>:8765/api/stats"
        try:
            req = urllib.request.Request(vps_url, method="GET")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    bandwidth = data.get("bandwidth", {})
                    if bandwidth.get("status") == "ok":
                        result["percent"] = bandwidth.get("percent", 0)
                        result["text"] = bandwidth.get("text", "0.0 GB / 3000 GB")
                        result["used"] = bandwidth.get("used", "0.0 GB")
                        result["lifetime_gb"] = bandwidth.get("lifetime_gb", 0)
                        result["reset_date"] = bandwidth.get("reset_date", "Resets Unknown")
                        result["per_server_bandwidth"] = bandwidth.get("per_server_bandwidth", {})
        except Exception as e:
            result["status"] = "error"
            print(f"[StatsFetcher] VPS bandwidth fetch failed: {e}")
            return result  # don't cache errors

        with self._lock:
            self._vps_cache = (now, result)
        return result
