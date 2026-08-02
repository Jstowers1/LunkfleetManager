<script>
  import '../app.css';
  let { children, data } = $props();
  let role = $derived(data?.role || 'unauthorized');

  //Fleet groups: array of { key, name, servers: {id: status}, stats?, alias?, game_types? }
  let fleetGroups = $state([]);
  
  //Compose container groups (e.g., odysseyus-stack)
  let containerGroups = $state([]);
  
  //Expanded group keys for O(1) lookup
  let expandedKeys = $state(new Set(['local']));

  //Search query & sort mode
  let searchQuery = $state('');
  let sortBy = $state('name'); //'name' | 'status' | 'type' | 'default'

  function isExpanded(key) {
    return expandedKeys.has(key);
  }

  function toggleGroup(key) {
    const next = new Set(expandedKeys);
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    expandedKeys = next;
  }

  function statusColor(status) {
    if (status === 'running') return 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]';
    if (status === 'restarting') return 'bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.6)]';
    if (status === 'stopped' || status === 'exited') return 'bg-red-500';
    return 'bg-gray-600';
  }

  function formatServerId(id) {
    return id.replace(/[-_]/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
  }

  function matchesSearch(serverId, query) {
    if (!query) return true;
    return formatServerId(serverId).toLowerCase().includes(query.toLowerCase());
  }

  function allServersInGroupRunning(servers) {
    return Object.values(servers).every(s => s === 'running' || s === 'restarting');
  }

  function hasAnyServerRunning(servers) {
    return Object.values(servers).some(s => s === 'running' || s === 'restarting');
  }

  //Reactive sorted view — $derived.by recalculates on fleetGroups or sortBy change
  let sortedView = $derived.by(() => {
    return fleetGroups.map(group => {
      let entries = Object.entries(group.servers);
      const gameTypes = group.game_types || {};
      
      entries.sort((a, b) => {
        const nameA = formatServerId(a[0]).toLowerCase();
        const nameB = formatServerId(b[0]).toLowerCase();
        
        if (sortBy === 'name') {
          return nameA.localeCompare(nameB);
        } else if (sortBy === 'status') {
          const statusOrder = { running: 0, restarting: 1, stopped: 2, exited: 3 };
          return (statusOrder[a[1]] || 99) - (statusOrder[b[1]] || 99);
        } else if (sortBy === 'type') {
          const typeA = gameTypes[a[0]] || 'other';
          const typeB = gameTypes[b[0]] || 'other';
          if (typeA !== typeB) {
            return typeA.localeCompare(typeB);
          }
          return nameA.localeCompare(nameB);
        } else if (sortBy === 'default') {
          return 0;
        }
        return 0;
      });
      
      return { ...group, sortedEntries: entries };
    });
  });

  //Global fleet poller — listens for home page broadcasts, falls back to slow poll.
  $effect(() => {
    let alive = true;
    let pollTimer = null;

    try {
      const cached = localStorage.getItem('lunk_fleet_groups');
      if (cached) {
        const parsed = JSON.parse(cached);
        fleetGroups = parsed;
      }
    } catch (e) {}

    function applyFleet(rawGroups) {
      const groups = Object.entries(rawGroups).map(([key, val]) => ({
        key,
        name: val.name,
        alias: val.alias,
        servers: val.servers || {},
        stats: val.stats || null,
        game_types: val.game_types || {},
        user_created: val.user_created || []
      }));
      fleetGroups = groups;
      try { localStorage.setItem('lunk_fleet_groups', JSON.stringify(groups)); } catch (e) {}
      const next = new Set(expandedKeys);
      for (const g of groups) next.add(g.key);
      expandedKeys = next;
    }

    function applyContainers(rawGroups) {
      const groups = Object.entries(rawGroups).map(([key, val]) => ({
        key,
        name: val.name,
        servers: val.servers || {},
        game_types: val.game_types || {}
      }));
      containerGroups = groups;
      const next = new Set(expandedKeys);
      for (const g of groups) next.add(g.key);
      expandedKeys = next;
    }

    //Home page broadcasts when it fetches /api/dashboard.
    function onFleetUpdate(e) {
      if (!alive) return;
      const { fleet, containers } = e.detail || {};
      if (fleet) applyFleet(fleet);
      if (containers) applyContainers(containers);
    }
    window.addEventListener('lunk:fleet-update', onFleetUpdate);

    //Server detail page broadcasts after start/stop so the sidebar
    //status dot updates immediately instead of waiting up to 45s.
    function onFleetRefresh() {
      if (!alive) return;
      poll();
    }
    window.addEventListener('lunk:fleet-refresh', onFleetRefresh);

    //Fallback: only fetch directly when home page isn't mounted (e.g. on
    ///server/[id]). Home page broadcasts keep sidebar fresh otherwise.
    //AbortController prevents the hanging-call pileup.
    async function poll() {
      if (!alive) return;
      try {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 12000);
        const res = await fetch('/api/dashboard', { signal: ctrl.signal });
        clearTimeout(timer);
        if (!res.ok) return;
        const data = await res.json();
        if (data.fleet_groups) applyFleet(data.fleet_groups);
        if (data.container_groups) applyContainers(data.container_groups);
      } catch (e) {
        //Aborted or network error — next tick retries.
      }
    }

    poll();
    pollTimer = setInterval(poll, 45000);

    return () => {
      alive = false;
      window.removeEventListener('lunk:fleet-update', onFleetUpdate);
      window.removeEventListener('lunk:fleet-refresh', onFleetRefresh);
      if (pollTimer) clearInterval(pollTimer);
    };
  });

  let isMobileMenuOpen = $state(false);

  //Group control functions
  function startGroup(groupKey) {
    const group = containerGroups.find(g => g.key === groupKey);
    if (!group) return;
    const serverIds = Object.keys(group.servers);
    if (!serverIds.length) return;
    fetch('/api/servers/start-group', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(serverIds)
    }).then(() => {
      window.dispatchEvent(new CustomEvent('lunk:fleet-refresh'));
    }).catch(e => console.error('Start group failed:', e));
  }

  function stopGroup(groupKey) {
    const group = containerGroups.find(g => g.key === groupKey);
    if (!group) return;
    const serverIds = Object.keys(group.servers);
    if (!serverIds.length) return;
    fetch('/api/servers/stop-group', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(serverIds)
    }).then(() => {
      window.dispatchEvent(new CustomEvent('lunk:fleet-refresh'));
    }).catch(e => console.error('Stop group failed:', e));
  }

  async function handleDeleteServer(serverId) {
    if (!confirm(`Permanently delete "${serverId}"? This stops the container, removes it from Docker, and deletes the recipe. Data on disk is NOT deleted.`)) return;
    try {
      const res = await fetch(`/api/servers/${serverId}`, { method: 'DELETE', credentials: 'same-origin' });
      if (res.ok) {
        window.dispatchEvent(new CustomEvent('lunk:fleet-refresh'));
        if (window.location.pathname.startsWith(`/server/${serverId}`)) {
          window.location.href = '/';
        } else {
          window.location.reload();
        }
      } else {
        const d = await res.json();
        alert(d.detail || 'Delete failed');
      }
    } catch (e) {
      alert('Network error: ' + e.message);
    }
  }
</script>

<div class="flex h-[100dvh] w-full bg-gray-900 text-white font-sans overflow-hidden">
  
  <!--Mobile backdrop -->
  {#if isMobileMenuOpen}
    <div class="fixed inset-0 z-40 bg-black/60 md:hidden" onclick={() => isMobileMenuOpen = false}></div>
  {/if}

  <aside class="{isMobileMenuOpen ? 'fixed inset-0 z-50 flex' : 'hidden'} md:relative md:flex md:w-64 lg:w-72 flex-col bg-gray-900 border-r border-gray-800"
         style="padding-top: env(safe-area-inset-top); padding-left: env(safe-area-inset-left); padding-bottom: env(safe-area-inset-bottom);">
    
    <!--Mobile close button -->
    <div class="md:hidden flex justify-end items-center border-b border-gray-800 p-3">
      <button onclick={() => isMobileMenuOpen = false} aria-label="Close menu" class="text-gray-400 hover:text-white">
        <svg class="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
        </svg>
      </button>
    </div>

    <nav class="flex-1 overflow-y-auto flex flex-col gap-0.5 mt-2">
      <!--Brand -->
      <div class="px-4 py-3 mb-1">
        <a href="/" onclick={() => isMobileMenuOpen = false} class="flex items-center gap-2 hover:bg-gray-800/40 rounded-lg px-2 py-1 -ml-2 transition-colors">
          <svg class="w-7 h-7 text-blue-400 flex-shrink-0" viewBox="0 0 24 24" fill="currentColor">
            <path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/>
          </svg>
          <h2 class="text-xl font-bold tracking-tight text-white">
            <span class="text-blue-400">Lunk</span>server
          </h2>
        </a>
      </div>
      
      <!--Search Bar -->
      <div class="px-4 mb-2">
        <div class="relative">
          <input
            type="text"
            placeholder="Search servers..."
            bind:value={searchQuery}
            class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
          />
          <svg class="w-4 h-4 absolute right-3 top-2.5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
          </svg>
        </div>
      </div>

      <!--Sort Dropdown -->
      <div class="px-4 mb-2">
        <select
          bind:value={sortBy}
          class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-300 focus:outline-none focus:border-blue-500 transition-colors"
        >
          <option value="name">Sort by Name</option>
          <option value="status">Sort by Status</option>
          <option value="type">Sort by Type</option>
          <option value="default">Default (Recipe Order)</option>
        </select>
      </div>
      
      {#if sortedView.length > 0}
        <!--Group: Lunkfleet -->
        <div class="px-4 py-1.5 text-xs font-semibold text-gray-500 uppercase tracking-widest">Fleet</div>

        <!--Iterate sorted entries -->
        {#each sortedView as group}
          <div>
            <!--Group header -->
            <button
              onclick={() => toggleGroup(group.key)}
              class="flex items-center justify-between w-full px-4 py-2 mx-2 rounded-lg hover:bg-gray-800/60 transition-colors text-left"
              aria-expanded="{isExpanded(group.key)}"
              aria-label="Toggle {group.alias || group.name}"
            >
              <div class="flex items-center gap-2 min-w-0">
                <svg 
                  class="w-4 h-4 text-gray-500 flex-shrink-0 transition-transform duration-200"
                  style="{isExpanded(group.key) ? 'transform: rotate(90deg)' : ''}"
                  fill="none" stroke="currentColor" viewBox="0 0 24 24"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                </svg>
                <span class="text-gray-200 text-base font-semibold truncate">
                  {group.alias || group.name}
                </span>
              </div>
            </button>

            <!--Expandable server list -->
            {#if isExpanded(group.key)}
              <div class="ml-3 flex flex-col gap-0.5">
                {#each group.sortedEntries as [serverId, status]}
                  {#if matchesSearch(serverId, searchQuery)}
                    <div class="flex items-center gap-1 px-4 py-2 mx-1 rounded-lg hover:bg-gray-800/40 transition-colors group">
                      <a href="/server/{serverId}"
                         onclick={() => isMobileMenuOpen = false}
                         class="flex items-center gap-2.5 flex-1 min-w-0">
                        <div class="w-2.5 h-2.5 rounded-full flex-shrink-0 {statusColor(status)}"></div>
                        <span class="text-gray-300 group-hover:text-white truncate text-base font-medium transition-colors">
                          {formatServerId(serverId)}
                        </span>
                      </a>
                      {#if group.user_created?.includes(serverId) && role === 'admin'}
                        <button
                          onclick={(e) => { e.preventDefault(); e.stopPropagation(); handleDeleteServer(serverId); }}
                          class="opacity-0 group-hover:opacity-100 text-red-400 hover:text-red-300 transition-opacity p-1"
                          title="Delete server">
                          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-4v6M1 7h22M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3"/>
                          </svg>
                        </button>
                      {/if}
                    </div>
                  {/if}
                {/each}
              </div>
            {/if}
          </div>
        {/each}
      {/if}
      
      <!--Container Groups -->
      {#if containerGroups.length > 0}
        <div class="px-4 py-1.5 text-xs font-semibold text-gray-500 uppercase tracking-widest mt-2">Groups</div>
        {#each containerGroups as group}
          <div>
            <!--Group header with action buttons -->
            <div
              onclick={() => toggleGroup(group.key)}
              class="flex items-center justify-between w-full px-4 py-2 mx-2 rounded-lg hover:bg-gray-800/60 transition-colors cursor-pointer text-left"
              aria-expanded="{isExpanded(group.key)}"
              aria-label="Toggle {group.name}"
            >
              <div class="flex items-center gap-2 min-w-0">
                <svg 
                  class="w-4 h-4 text-purple-400 flex-shrink-0 transition-transform duration-200"
                  style="{isExpanded(group.key) ? 'transform: rotate(90deg)' : ''}"
                  fill="none" stroke="currentColor" viewBox="0 0 24 24"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                </svg>
                <span class="text-purple-300 text-sm font-semibold truncate">
                  {group.name}
                </span>
              </div>
              <div class="flex items-center gap-1 flex-shrink-0">
                <button
                  onclick={(e) => { e.stopPropagation(); startGroup(group.key); }}
                  class="p-1 rounded hover:bg-green-600/30 transition-colors"
                  title="Start all"
                  disabled={hasAnyServerRunning(group.servers)}
                >
                  <svg class="w-3.5 h-3.5 text-green-400" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M8 5v14l11-7z"/>
                  </svg>
                </button>
                <button
                  onclick={(e) => { e.stopPropagation(); stopGroup(group.key); }}
                  class="p-1 rounded hover:bg-red-600/30 transition-colors"
                  title="Stop all"
                  disabled={!hasAnyServerRunning(group.servers)}
                >
                  <svg class="w-3.5 h-3.5 text-red-400" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M6 6h12v12H6z"/>
                  </svg>
                </button>
              </div>
            </div>

            <!--Expandable group server list -->
            {#if isExpanded(group.key)}
              <div class="ml-3 flex flex-col gap-0.5">
                {#each Object.entries(group.servers) as [serverId, status]}
                  {#if matchesSearch(serverId, searchQuery)}
                    <a href="/server/{serverId}"
                       onclick={() => isMobileMenuOpen = false}
                       class="flex items-center gap-2.5 px-4 py-2 mx-1 rounded-lg hover:bg-gray-800/40 transition-colors group">
                      <div class="w-2.5 h-2.5 rounded-full flex-shrink-0 {statusColor(status)}"></div>
                      <span class="text-gray-300 group-hover:text-white truncate text-sm font-medium transition-colors">
                        {formatServerId(serverId)}
                      </span>
                    </a>
                  {/if}
                {/each}
              </div>
            {/if}
          </div>
        {/each}
      {/if}
      
      <!--No results message -->
      {#if searchQuery && (fleetGroups.length > 0 || containerGroups.length > 0)}
        <div class="px-4 py-2 text-xs text-gray-500 text-center">
          {#if !fleetGroups.some(g => Object.keys(g.servers).some(sid => matchesSearch(sid, searchQuery)))
              && !containerGroups.some(g => Object.keys(g.servers).some(sid => matchesSearch(sid, searchQuery)))}
            No servers match "{searchQuery}"
          {/if}
        </div>
      {/if}
    </nav>
  </aside>

  <div class="flex-1 flex flex-col min-w-0">
    <!--Mobile header -->
    <header class="md:hidden flex-none bg-gray-800 border-b border-gray-700 flex justify-between items-center px-4 py-2"
            style="padding-top: calc(0.5rem + env(safe-area-inset-top));">
      <a href="/" onclick={() => isMobileMenuOpen = false} class="text-white font-bold text-lg">
        <span class="text-blue-400">Lunk</span>server
      </a>
      <button onclick={() => isMobileMenuOpen = true} aria-label="Open menu" class="text-gray-300 hover:text-white">
        <svg class="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/>
        </svg>
      </button>
    </header>

    <!--Main content -->
    <main class="flex-1 overflow-y-auto p-4 md:p-8"
          style="padding-bottom: calc(2rem + env(safe-area-inset-bottom));">
      <div class="max-w-6xl mx-auto w-full">
        <slot />
      </div>
    </main>
  </div>
</div>
