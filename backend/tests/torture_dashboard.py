"""Torture test: hammer /api/dashboard with concurrent requests.

NS_BINDING_ABORTED in browsers happens when the backend never responds (hangs)
and the browser aborts the fetch. This test proves the dashboard responds within
a bounded time under load. Run: python3 tests/torture_dashboard.py
"""
import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

BASE_URL = "http://127.0.0.1:8000"
AUTH_COOKIE = {"auth_token": os.environ.get("ADMIN_TOKEN", "lunkman4ever!")}
DASHBOARD = f"{BASE_URL}/api/dashboard"
SATELLITES = f"{BASE_URL}/api/satellites"
TIMEOUT = 30  #Each request must respond within 30s, dashboard has 10s caps internally
CONCURRENCY = 20
TOTAL_REQUESTS = 60


async def fetch_one(client, url, req_id):
    start = time.monotonic()
    try:
        r = await client.get(url, cookies=AUTH_COOKIE, timeout=TIMEOUT)
        elapsed = time.monotonic() - start
        return {"id": req_id, "status": r.status_code, "elapsed": elapsed, "ok": r.status_code == 200}
    except Exception as e:
        elapsed = time.monotonic() - start
        return {"id": req_id, "status": 0, "elapsed": elapsed, "ok": False, "error": str(e)[:80]}


async def run_load_test(label, url, total):
    print(f"\n=== {label}: {total} requests, {CONCURRENCY} concurrent ===")
    sem = asyncio.Semaphore(CONCURRENCY)

    async def bounded(client, req_id):
        async with sem:
            return await fetch_one(client, url, req_id)

    async with httpx.AsyncClient() as client:
        start = time.monotonic()
        results = await asyncio.gather(*[bounded(client, i) for i in range(total)])
        wall = time.monotonic() - start

    ok = [r for r in results if r["ok"]]
    fail = [r for r in results if not r["ok"]]
    times = sorted(r["elapsed"] for r in results)
    print(f"  wall time: {wall:.1f}s")
    print(f"  success: {len(ok)}/{total}")
    print(f"  p50: {times[len(times)//2]:.2f}s  p95: {times[int(len(times)*0.95)]:.2f}s  max: {times[-1]:.2f}s")
    if fail:
        print(f"  FAILURES ({len(fail)}):")
        for r in fail[:5]:
            print(f"    req {r['id']}: status={r['status']} err={r.get('error','?')}")
    return len(fail) == 0


async def main():
    #Health check
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{BASE_URL}/api/dashboard", cookies=AUTH_COOKIE, timeout=15)
            if r.status_code != 200:
                print(f"FATAL: dashboard returned {r.status_code} on health check")
                return 1
        except Exception as e:
            print(f"FATAL: backend unreachable: {e}")
            return 1

    print(f"Health check passed. Backend at {BASE_URL}")

    dashboard_ok = await run_load_test("Dashboard", DASHBOARD, TOTAL_REQUESTS)
    satellites_ok = await run_load_test("Satellites", SATELLITES, 20)

    if dashboard_ok and satellites_ok:
        print("\nALL PASSED — no hangs, no 500s, no timeouts under load.")
        return 0
    print("\nFAILURES DETECTED — see above.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
