<script>
    import AddServerModal from '$lib/AddServerModal.svelte';
    let { data } = $props();
    let role = $derived(data?.role || 'guest');

    //Add Server modal
    let showAddServer = $state(false);

    //Local server stats
    let lunkStats = $state(null);
    
    //Remote hosts & fleet data
    let remoteHosts = $state([]);
    let fleetGroups = $state([]);
    
    //VPS bandwidth
    let vpsBandwidth = $state(null);
    
    //Satellite stats
    let satelliteStats = $state([]);
    
    //Loading state
    let loading = $state(true);
    let dashboardLoaded = $state(false);
    
    //Local GPU VRAM
    let localVRAM = $state(null);
    
    //Fleet overview counters
    let fleetOverview = $state({
        totalHosts: 0,
        onlineHosts: 0,
        totalServers: 0,
        runningServers: 0
    });

    //RAM allocation across running local containers
    let allocation = $state(null);

    //Cache TTL: 24h
    const CACHE_TTL = 30 * 1000;
    const CACHE_KEY = 'lunkserver_home_cache';

    const OFFLINE_SAT = JSON.parse('{"host":"<TAILSCALE_IP>","alias":"Lunkserver 3.0","stats":{"status":"offline","cpu":0,"ram":0,"ram_used":0,"ram_total":0,"disk":0,"disk_used":0,"disk_total":0,"vram_used":0,"vram_total":0,"gpu_name":"Unknown"}}');

    function getCache() {
        try {
            const cached = localStorage.getItem(CACHE_KEY);
            if (cached) {
                const data = JSON.parse(cached);
                if (Date.now() - data.timestamp < CACHE_TTL) {
                    return data;
                }
                localStorage.removeItem(CACHE_KEY);
            }
        } catch (e) {
            localStorage.removeItem(CACHE_KEY);
        }
        return null;
    }

    function saveCache() {
        try {
            localStorage.setItem(CACHE_KEY, JSON.stringify({
                lunkStats,
                remoteHosts,
                fleetGroups,
                vpsBandwidth,
                satelliteStats,
                localVRAM,
                fleetOverview,
                allocation,
                timestamp: Date.now()
            }));
        } catch (e) {}
    }

    async function safeFetch(url, fallback, timeout = 10000) {
        try {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), timeout);
            const res = await fetch(url, { signal: controller.signal });
            clearTimeout(timer);
            const json = await res.json();
            return json;
        } catch {
            return fallback;
        }
    }

    //Fast tier: local system + VRAM (cheap, local, no SSH).
    async function fetchFastTelemetry() {
        try {
            const [lunkData, vramData] = await Promise.all([
                safeFetch('/api/system', { cpu_percent: 0, ram_percent: 0, ram_used: "0", ram_total: "0", storage_percent: 0, storage_used: "0", storage_total: "0" }, 5000),
                safeFetch('/api/vram', { status: "unknown", vram_used: 0, vram_total: 0, gpu_name: "Unknown" }, 5000)
            ]);
            lunkStats = lunkData;
            localVRAM = vramData;
            loading = false;
            saveCache();
        } catch (err) {
            console.error("Fast telemetry failed:", err);
        }
    }

    //Slow tier: consolidated dashboard endpoint (one round trip).
    async function fetchDashboard() {
        try {
            const data = await safeFetch('/api/dashboard', null, 20000);
            if (!data) return;

            loading = false;
            lunkStats = data.system ?? lunkStats;
            localVRAM = data.vram ?? localVRAM;
            vpsBandwidth = data.vps_telem ?? vpsBandwidth;
            allocation = data.allocation ?? allocation;
            dashboardLoaded = true;

            //Map satellite microservice to remote host format
            const satellites = (data.satellites || []).map(s => ({
                host: `ssh://<USER>@${s.host}`,
                ip: s.host,
                alias: s.alias || `Lunkserver ${s.host.split('.').pop()}`,
                stats: {
                    cpu: s.stats?.cpu || 0,
                    ram: s.stats?.ram || 0,
                    ram_used: s.stats?.ram_used || 0,
                    ram_total: s.stats?.ram_total || 0,
                    disk: s.stats?.disk || 0,
                    disk_used: s.stats?.disk_used || 0,
                    disk_total: s.stats?.disk_total || 0,
                    vram_used: s.stats?.vram_used || 0,
                    vram_total: s.stats?.vram_total || 0,
                    gpu_name: s.stats?.gpu_name || "Unknown",
                    status: s.stats?.status || "offline"
                }
            }));

            remoteHosts = [...(data.remote_hosts || []), ...satellites];

            if (data.fleet_groups && typeof data.fleet_groups === 'object') {
                fleetGroups = Object.entries(data.fleet_groups).map(([key, val]) => ({
                    key,
                    name: val.name,
                    alias: val.alias,
                    servers: val.servers || {},
                    stats: val.stats || null,
                    game_types: val.game_types || {},
                    user_created: val.user_created || []
                }));
            }
            //Always broadcast — fleet and containers succeed/fail independently.
            //Don't broadcast empty container_groups from an offline host —
            //it wipes the sidebar. Only update when we have real data.
            const containerGroups = data.container_groups;
            const hasContainers = containerGroups && Object.keys(containerGroups).length > 0;
            try { window.dispatchEvent(new CustomEvent('lunk:fleet-update', { detail: { fleet: fleetGroups, containers: hasContainers ? containerGroups : undefined } })); } catch (e) {}

            updateFleetOverview();
            saveCache();
        } catch (err) {
            console.error("Dashboard fetch failed:", err);
        }
    }

    function updateFleetOverview() {
        const totalHosts = 1 + remoteHosts.length;
        
        let runningServers = 0;
        let totalServers = 0;
        for (const group of fleetGroups) {
            for (const [serverId, status] of Object.entries(group.servers || {})) {
                totalServers++;
                if (status === 'running' || status === 'restarting') {
                    runningServers++;
                }
            }
        }
        
        fleetOverview = {
            totalHosts,
            onlineHosts: runningServers > 0 ? totalHosts : 1,
            totalServers,
            runningServers
        };
    }

    $effect(() => {
        const cache = getCache();
        if (cache) {
            lunkStats = cache.lunkStats;
            remoteHosts = cache.remoteHosts;
            fleetGroups = cache.fleetGroups;
            vpsBandwidth = cache.vpsBandwidth;
            localVRAM = cache.localVRAM;
            fleetOverview = cache.fleetOverview;
            allocation = cache.allocation;
            loading = false;
        } else {
            loading = true;
        }

    let fastTimer = null;
    let slowTimer = null;
    let alive = true;

    //Recursive setTimeout: next tick only schedules AFTER the previous
    //completes. Eliminates NS_BINDING_ABORTED from overlapping fetches.
    async function fastLoop() {
        if (!alive) return;
        await fetchFastTelemetry();
        fastTimer = setTimeout(fastLoop, 3000);
    }

    async function slowLoop() {
        if (!alive) return;
        await fetchDashboard();
        slowTimer = setTimeout(slowLoop, 12000);
    }

    //Kick off both tiers immediately, then they self-schedule.
    fastLoop();
    slowLoop();

    return () => {
        alive = false;
        if (fastTimer) clearTimeout(fastTimer);
        if (slowTimer) clearTimeout(slowTimer);
    };
});

    function formatServerId(id) {
        return id.replace(/[-_]/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
    }

    function getStatusColor(status) {
        if (status === 'running') return 'bg-emerald-500';
        if (status === 'restarting') return 'bg-amber-400';
        return 'bg-red-500';
    }
    
    function formatStorage(used, total) {
        if (!used || !total) return "0 / 0 GB";
        return `${parseFloat(used).toFixed(1)} / ${parseFloat(total).toFixed(1)} GB`;
    }
</script>

<div class="max-w-7xl mx-auto">
    
    <div class="mb-8 mt-2">
        <h1 class="text-3xl font-bold text-white tracking-tight">Welcome to the Lunkserver Management Dashboard!</h1>
        <p class="text-gray-400 mt-2 text-lg">The greatest software engineering feat ever seen!</p>
    </div>

    <!--Skeleton Loaders-->
    {#if loading}
        <div class="bg-gray-800 rounded-lg p-6 shadow-md border border-gray-700 mb-6 animate-pulse">
            <div class="h-6 bg-gray-700 rounded w-1/3 mb-4"></div>
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div class="bg-[#111827] rounded-lg p-4 border border-gray-700">
                    <div class="h-5 bg-gray-700 rounded w-1/4 mb-3"></div>
                    <div class="space-y-3">
                        <div class="h-4 bg-gray-700 rounded w-1/2"></div>
                        <div class="h-4 bg-gray-700 rounded w-2/3"></div>
                        <div class="h-4 bg-gray-700 rounded w-3/4"></div>
                        <div class="h-4 bg-gray-700 rounded w-1/2"></div>
                    </div>
                </div>
                <div class="bg-[#111827] rounded-lg p-4 border border-gray-700">
                    <div class="h-5 bg-gray-700 rounded w-1/4 mb-3"></div>
                    <div class="space-y-3">
                        <div class="h-4 bg-gray-700 rounded w-1/2"></div>
                        <div class="h-4 bg-gray-700 rounded w-2/3"></div>
                        <div class="h-4 bg-gray-700 rounded w-1/2"></div>
                    </div>
                </div>
            </div>
        </div>
    {/if}

    <!--Lunkfleet Resource Statistics (Local + Remote)-->
    {#if !loading}
        <div class="bg-gray-800 rounded-lg p-6 shadow-md border border-gray-700 mb-6">
            <h2 class="text-xl text-white font-semibold mb-4">Lunkfleet Resource Statistics</h2>
            
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <!--Local Server Card-->
                <div class="bg-[#111827] rounded-lg p-4 border border-gray-700">
                    <h3 class="text-white font-semibold text-lg mb-3">Lunkserver 2.0</h3>
                    
                    <div class="space-y-3">
                         <!--CPU-->
                        <div>
                            <div class="flex justify-between text-sm text-gray-400 mb-1">
                                <span>CPU</span>
                                <span>{lunkStats?.cpu_percent || 0}%</span>
                            </div>
                            <div class="w-full bg-gray-700 rounded-full h-2">
                                <div class="bg-blue-500 h-2 rounded-full" style="width: {lunkStats?.cpu_percent || 0}%"></div>
                            </div>
                        </div>

                        <!--RAM-->
                        <div>
                            <div class="flex justify-between text-sm text-gray-400 mb-1">
                                <span>RAM</span>
                                <span>{lunkStats?.ram_used || 0} / {lunkStats?.ram_total || 0} GB</span>
                            </div>
                            <div class="w-full bg-gray-700 rounded-full h-2">
                                <div class="bg-green-500 h-2 rounded-full" style="width: {lunkStats?.ram_percent || 0}%"></div>
                            </div>
                        </div>

                        <!--Storage-->
                        <div>
                            <div class="flex justify-between text-sm text-gray-400 mb-1">
                                <span>Storage</span>
                                <span>{lunkStats?.storage_used || 0} / {lunkStats?.storage_total || 0} GB</span>
                            </div>
                            <div class="w-full bg-gray-700 rounded-full h-2">
                                <div class="bg-yellow-500 h-2 rounded-full" style="width: {lunkStats?.storage_percent || 0}%"></div>
                            </div>
                        </div>

                        <!--VRAM(GPU)-->
                        {#if localVRAM && localVRAM.status === 'ok'}
                            <div>
                                <div class="flex justify-between text-sm text-gray-400 mb-1">
                                    <span>VRAM ({localVRAM.gpu_name})</span>
                                    <span>{localVRAM.vram_used || 0} / {localVRAM.vram_total || 0} GB</span>
                                </div>
                                <div class="w-full bg-gray-700 rounded-full h-2">
                                    <div class="bg-purple-500 h-2 rounded-full" style="width: {localVRAM.vram_total > 0 ? (localVRAM.vram_used / localVRAM.vram_total * 100) : 0}%"></div>
                                </div>
                            </div>
                        {/if}
                    </div>
                </div>

                <!--Remote Host Cards-->
                {#each remoteHosts as remote}
                    {#if remote.stats?.status === 'offline'}
                        <!--Offline satellite card-->
                        <div class="bg-gray-800/50 rounded-lg p-4 border border-gray-700 opacity-60">
                            <div class="flex items-center justify-between mb-3">
                                <h3 class="text-gray-400 font-semibold text-lg">{remote.alias}</h3>
                            </div>
                            <div class="flex items-center justify-center h-24">
                                <span class="text-red-400 font-semibold">{remote.alias} is offline</span>
                            </div>
                        </div>
                    {:else}
                        <div class="bg-[#111827] rounded-lg p-4 border border-gray-700">
                            <div class="flex items-center justify-between mb-3">
                                <h3 class="text-white font-semibold text-lg">{remote.alias}</h3>
                            </div>
                            
                            <div class="space-y-3">
                                <div>
                                    <div class="flex justify-between text-sm text-gray-400 mb-1">
                                        <span>CPU</span>
                                        <span>{remote.stats?.cpu || 0}%</span>
                                    </div>
                                    <div class="w-full bg-gray-700 rounded-full h-2">
                                        <div class="bg-blue-500 h-2 rounded-full" style="width: {remote.stats?.cpu || 0}%"></div>
                                    </div>
                                </div>

                                <div>
                                    <div class="flex justify-between text-sm text-gray-400 mb-1">
                                        <span>RAM</span>
                                        <span>{remote.stats?.ram_used || 0} / {remote.stats?.ram_total || 0} GB</span>
                                    </div>
                                    <div class="w-full bg-gray-700 rounded-full h-2">
                                        <div class="bg-green-500 h-2 rounded-full" style="width: {remote.stats?.ram || 0}%"></div>
                                    </div>
                                </div>

                                <div>
                                    <div class="flex justify-between text-sm text-gray-400 mb-1">
                                        <span>Storage</span>
                                        <span>{remote.stats?.disk_used || 0} / {remote.stats?.disk_total || 0} GB</span>
                                    </div>
                                    <div class="w-full bg-gray-700 rounded-full h-2">
                                        <div class="bg-yellow-500 h-2 rounded-full" style="width: {remote.stats?.disk || 0}%"></div>
                                    </div>
                                </div>

                                <!--VRAM for remote hosts-->
                                {#if remote.stats?.vram_total > 0}
                                    <div>
                                        <div class="flex justify-between text-sm text-gray-400 mb-1">
                                            <span>VRAM ({remote.stats?.gpu_name || 'Unknown'})</span>
                                            <span>{remote.stats?.vram_used || 0} / {remote.stats?.vram_total || 0} GB</span>
                                        </div>
                                        <div class="w-full bg-gray-700 rounded-full h-2">
                                            <div class="bg-purple-500 h-2 rounded-full" style="width: {remote.stats?.vram_total > 0 ? (remote.stats?.vram_used / remote.stats?.vram_total * 100) : 0}%"></div>
                                        </div>
                                    </div>
                                {/if}
                            </div>
                        </div>
                    {/if}
                {/each}

                <!--Skeleton: remote host card while dashboard loads-->
                {#if !dashboardLoaded}
                    <div class="bg-[#111827] rounded-lg p-4 border border-gray-700 animate-pulse">
                        <div class="h-5 bg-gray-700 rounded w-1/4 mb-3"></div>
                        <div class="space-y-3">
                            <div class="h-4 bg-gray-700 rounded w-1/2"></div>
                            <div class="h-4 bg-gray-700 rounded w-2/3"></div>
                            <div class="h-4 bg-gray-700 rounded w-3/4"></div>
                        </div>
                    </div>
                {/if}
            </div>
        </div>
    {/if}

    <!--Active Fleet Overview-->
    {#if !loading}
        <div class="bg-gray-800 rounded-lg p-6 shadow-md border border-gray-700 mb-6">
            <div class="flex justify-between items-center mb-4">
                <h2 class="text-xl text-white font-semibold">Active Fleet Overview</h2>
                {#if role === 'admin'}
                    <button onclick={() => showAddServer = true}
                        class="px-4 py-2 bg-green-600 hover:bg-green-700 text-white text-sm font-medium rounded-lg transition-colors flex items-center gap-1">
                        <span class="text-lg leading-none">+</span> Add Server
                    </button>
                {/if}
            </div>
            
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div class="bg-[#111827] rounded-lg p-4 border-l-4 border-l-emerald-500">
                    <div class="text-2xl font-bold text-emerald-400">{fleetOverview.totalHosts}</div>
                    <div class="text-gray-500 text-sm mt-1">Total Hosts</div>
                </div>
                <div class="bg-[#111827] rounded-lg p-4 border-l-4 border-l-blue-500">
                    <div class="text-2xl font-bold text-blue-400">{fleetOverview.totalServers}</div>
                    <div class="text-gray-500 text-sm mt-1">Total Servers</div>
                </div>
                <div class="bg-[#111827] rounded-lg p-4 border-l-4 border-l-amber-500">
                    <div class="text-2xl font-bold text-amber-400">{fleetOverview.runningServers}</div>
                    <div class="text-gray-500 text-sm mt-1">Running</div>
                </div>
                <div class="bg-[#111827] rounded-lg p-4 border-l-4 border-l-gray-500">
                    <div class="text-2xl font-bold text-gray-400">{fleetOverview.totalServers - fleetOverview.runningServers}</div>
                    <div class="text-gray-500 text-sm mt-1">Offline</div>
                </div>
            </div>
        </div>
    {/if}

    <!--RAM Allocation Overview-->
    {#if !loading && allocation}
        {@const pct = allocation.total_ram_gb > 0 ? (allocation.allocated_gb / allocation.total_ram_gb * 100) : 0}
        {@const overCommit = allocation.allocated_gb > allocation.total_ram_gb}
        <div class="bg-gray-800 rounded-lg p-6 shadow-md border border-gray-700 mb-6">
            <h2 class="text-xl text-white font-semibold mb-4">RAM Allocation</h2>
            <div class="flex justify-between text-sm text-gray-400 mb-2">
                <span>{allocation.running_local} running {allocation.running_local === 1 ? 'container' : 'containers'}</span>
                <span class={overCommit ? 'text-red-400 font-bold' : ''}>
                    {allocation.allocated_gb.toFixed(1)} / {allocation.total_ram_gb.toFixed(1)} GB allocated
                </span>
            </div>
            <div class="w-full bg-gray-700 rounded-full h-3 overflow-hidden">
                <div class="{overCommit ? 'bg-red-500' : 'bg-green-500'} h-3 rounded-full transition-all" style="width: {Math.min(pct, 100)}%"></div>
            </div>
            {#if overCommit}
                <p class="text-red-400 text-xs mt-2">⚠ Over-committed by {(allocation.allocated_gb - allocation.total_ram_gb).toFixed(1)} GB — not all containers can use their full limit simultaneously.</p>
            {/if}
        </div>
    {/if}

    <!--VPS Network Pipeline-->
    {#if !loading && vpsBandwidth && vpsBandwidth.status === 'ok'}
        <div class="bg-gray-800 rounded-lg p-6 shadow-md border border-gray-700 mb-6">
            <h2 class="text-xl text-white font-semibold mb-4">VPS Network Pipeline</h2>
            
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                <div class="bg-[#111827] rounded-lg p-4 border border-gray-700">
                    <div class="text-2xl font-bold text-purple-400">{vpsBandwidth.text}</div>
                    <div class="text-gray-500 text-sm mt-1">Monthly Usage</div>
                </div>
                <div class="bg-[#111827] rounded-lg p-4 border border-gray-700">
                    <div class="text-2xl font-bold text-blue-400">{(vpsBandwidth?.bandwidth?.lifetime_gb || vpsBandwidth?.lifetime_gb || 0).toFixed(1)} GB Lifetime</div>
                    <div class="text-gray-500 text-sm mt-1">Total Usage</div>
                </div>
            </div>
            
            <!--Per-server bandwidth breakdown-->
            {#if vpsBandwidth.per_server_bandwidth && Object.keys(vpsBandwidth.per_server_bandwidth).length > 0}
                <div class="mt-4">
                    <h3 class="text-white font-semibold mb-2">Per-Server Bandwidth Usage</h3>
                    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-2">
                        {#each Object.entries(vpsBandwidth.per_server_bandwidth) as [server, bandwidth]}
                            <div class="bg-[#111827] rounded p-2 text-center">
                                <div class="text-xs text-gray-400 truncate">{formatServerId(server)}</div>
                                <div class="text-sm text-white font-mono">{bandwidth}</div>
                            </div>
                        {/each}
                    </div>
                </div>
            {/if}
            
            <div class="mt-4">
                <div class="w-full bg-gray-700 rounded-full h-3">
                    <div class="bg-purple-500 h-3 rounded-full" style="width: {vpsBandwidth.percent || 0}%"></div>
                </div>
            </div>
        </div>
    {:else if !loading && !dashboardLoaded}
        <!--Skeleton: VPS Network Pipeline while dashboard loads-->
        <div class="bg-gray-800 rounded-lg p-6 shadow-md border border-gray-700 mb-6 animate-pulse">
            <div class="h-6 bg-gray-700 rounded w-1/4 mb-4"></div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                <div class="bg-[#111827] rounded-lg p-4 border border-gray-700">
                    <div class="h-7 bg-gray-700 rounded w-1/2 mb-2"></div>
                    <div class="h-4 bg-gray-700 rounded w-1/4"></div>
                </div>
                <div class="bg-[#111827] rounded-lg p-4 border border-gray-700">
                    <div class="h-7 bg-gray-700 rounded w-1/2 mb-2"></div>
                    <div class="h-4 bg-gray-700 rounded w-1/4"></div>
                </div>
            </div>
            <div class="mt-4 h-3 bg-gray-700 rounded-full"></div>
        </div>
    {/if}

    <!--System Reminders-->
    <div class="bg-gray-800 rounded-lg p-6 shadow-md border border-gray-700 mb-6">
        <h2 class="text-xl text-white font-semibold mb-4">System Reminders</h2>
        
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div class="bg-[#111827] p-4 rounded-r-lg border-y border-r border-gray-700 border-l-4 border-l-blue-500">
                <h3 class="text-blue-400 font-bold mb-1 text-sm uppercase tracking-wider">Authentication</h3>
                <p class="text-gray-300 text-sm">Doesn't look right? Replace your token in the URL! (Required every 365 days)</p>
            </div>

            <div class="bg-[#111827] p-4 rounded-r-lg border-y border-r border-gray-700 border-l-4 border-l-red-500">
                <h3 class="text-red-400 font-bold mb-1 text-sm uppercase tracking-wider">Outage Protocol</h3>
                <p class="text-gray-300 text-sm">If nothing is filled out, that means there's something physically wrong with the hardware. A lunktastrophy occurred!</p>
            </div>

            {#if role === 'admin'}
                <div class="bg-[#111827] p-4 rounded-r-lg border-y border-r border-gray-700 border-l-4 border-l-green-500">
                  <h3 class="text-green-400 font-bold mb-1 text-sm uppercase tracking-wider">Modding Notice</h3>
                  <p class="text-gray-300 text-sm">Remember to restart the server after installing or deleting any mods. Changes won't take effect until the next boot cycle.</p>
                </div>

                <div class="bg-[#111827] p-4 rounded-r-lg border-y border-r border-gray-700 border-l-4 border-l-amber-500">
                    <h3 class="text-amber-400 font-bold mb-1 text-sm uppercase tracking-wider">Tailscale Expiration</h3>
                    <p class="text-gray-300 text-sm">Backend deploy not functional? Try looking at the Tailscale authorization token and replace it. (Expires every 90 days)</p>
                </div>

                <div class="bg-[#111827] p-4 rounded-r-lg border-y border-r border-gray-700 border-l-4 border-l-purple-500">
                    <h3 class="text-purple-400 font-bold mb-1 text-sm uppercase tracking-wider">LunkVPS Direct Access</h3>
                    <p class="text-gray-300 text-sm text-wrap break-words">Here's the IP Address: <span class="font-mono text-purple-300"><VPS_IP></span>. Only use SSH keys for access. Don't forget about sshm!</p>
                </div>
            {/if}

        </div>
    </div>

    <!--Add Server Modal-->
    {#if showAddServer}
        <div class="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4"
            onclick={(e) => { if (e.target === e.currentTarget) showAddServer = false }}>
            <div class="bg-[#0f172a] border border-gray-700 rounded-xl shadow-2xl max-w-2xl w-full max-h-[90vh] overflow-y-auto p-6">
                <div class="flex justify-between items-start mb-6">
                    <div></div>
                    <button onclick={() => showAddServer = false}
                        class="text-gray-500 hover:text-gray-300 text-2xl leading-none">&times;</button>
                </div>
                <AddServerModal
                    onCreated={(sid) => { showAddServer = false; fetchDashboard(); }}
                    onClose={() => showAddServer = false} />
            </div>
        </div>
    {/if}

</div>
