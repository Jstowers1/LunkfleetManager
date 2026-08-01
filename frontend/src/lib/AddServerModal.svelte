<script>
  let { onCreated, onClose } = $props();

  let templates = $state([]);
  let step = $state(1); //1=pick template or search, 2=configure, 3=creating
  let mode = $state(''); //'template' | 'custom'
  let selectedTemplate = $state(null);
  let loading = $state(false);
  let error = $state('');
  let success = $state('');

  //Search state
  let searchQuery = $state('');
  let searchResults = $state([]);
  let searching = $state(false);
  let selectedImage = $state('');

  //Form fields
  let serverId = $state('');
  let serverName = $state('');
  let description = $state('');
  let image = $state('');
  let ports = $state('{}');
  let containerPath = $state('/data');
  let clientPort = $state('');
  let ramLimit = $state('0g');
  let startNow = $state(true);

  //Factorio map-gen ore tick presets (percent values, matching the smart-gui editor)
  const ORE_PRESETS = [25, 33, 50, 75, 100, 150, 200, 250, 300, 350, 400, 450, 500];

  //Game-specific option state (factorio/minecraft)
  let factorioVersion = $state('');         //image tag: stable, latest
  let factorioVersionLabels = $state({stable: '', experimental: '', all_versions: []});
  let mapGenPreset = $state('');            //preset key from /api/factorio/map-presets
  let factorioPresets = $state([]);
  let factorioModQuery = $state('');
  let factorioModResults = $state([]);
  let factorioSearching = $state(false);
  let selectedFactorioMods = $state([]);    //{name,title,version,download_url,filename}
  let spaceAgeDlc = $state(false);
  let factorioPlanetResources = $state({}); //space age planet resource defs
  let factorioMapOverrides = $state({});    //user edits: {planet: {resource: {freq,size,richness}}}

  function updateMapOverride(planet, resource, attr, value) {
    if (!factorioMapOverrides[planet]) factorioMapOverrides[planet] = {};
    if (!factorioMapOverrides[planet][resource]) factorioMapOverrides[planet][resource] = {};
    factorioMapOverrides[planet][resource][attr] = parseFloat(value) || 0;
  }

  let mcServerType = $state('FABRIC');
  let mcVersion = $state('');
  let mcVersions = $state([]);              //Mojang release versions for dropdown
  let modpackQuery = $state('');
  let modpackResults = $state([]);
  let modpackSearching = $state(false);
  let selectedModpack = $state(null);       //{name,page_url}
  let mcModQuery = $state('');
  let mcModResults = $state([]);
  let mcModSearching = $state(false);
  let selectedMcMods = $state([]);          //{slug,title,filename,download_url}
  let enableGeyser = $state(false);         //one-click Geyser+Floodgate
  let geyserPort = $state('19132');         //Bedrock UDP port
  let enableOptimization = $state(false);   //one-click optimization mods
  let activeFactorioTab = $state('nauvis'); //which planet tab is selected

  //Load templates on mount
  $effect(() => {
    fetchTemplates();
  });

  async function fetchTemplates() {
    try {
      const res = await fetch('/api/templates');
      if (res.ok) {
        const data = await res.json();
        templates = data.templates;
      }
    } catch (e) {}
  }

  async function doSearch() {
    if (!searchQuery.trim() || searchQuery.trim().length < 2) return;
    searching = true;
    error = '';
    try {
      const res = await fetch(`/api/docker/search?query=${encodeURIComponent(searchQuery)}`);
      if (res.ok) {
        const data = await res.json();
        searchResults = data.results;
      } else {
        error = 'Search failed';
      }
    } catch (e) {
      error = 'Search failed: ' + e.message;
    }
    searching = false;
  }

  function pickTemplate(t) {
    mode = 'template';
    selectedTemplate = t;
    serverId = t.key + '_01';
    serverName = t.label;
    image = t.image;
    ports = JSON.stringify(t.ports, null, 2);
    containerPath = t.container_path;
    clientPort = String(t.client_port);
    ramLimit = t.ram_limit || '0g';
    resetGameOptions();
    if (t.game_type === 'factorio') {
      loadFactorioVersionLabels();
      loadFactorioPresets();
    } else if (t.game_type === 'minecraft') {
      loadMcVersions();
    }
    step = 2;
  }

  function pickImage(img) {
    //Check if this image matches a template
    const match = templates.find(t => t.image.split(':')[0] === img.split(':')[0]);
    if (match) {
      pickTemplate(match);
    } else {
      //Manual mode
      mode = 'custom';
      selectedTemplate = null;
      image = img;
      serverId = img.split('/')[0].replace(/[^a-z0-9]/g, '_') + '_01';
      serverName = img.split('/').pop().split(':')[0];
      ports = '{}';
      containerPath = '/data';
      clientPort = '';
      ramLimit = '0g';
      step = 2;
    }
  }

  function startCustom() {
    mode = 'custom';
    selectedTemplate = null;
    image = '';
    serverId = '';
    serverName = '';
    ports = '{}';
    containerPath = '/data';
    clientPort = '';
    ramLimit = '0g';
    resetGameOptions();
    step = 2;
  }

  //--- Game-specific helpers ---

  function resetGameOptions() {
    factorioVersion = '';
    mapGenPreset = '';
    factorioModQuery = '';
    factorioModResults = [];
    selectedFactorioMods = [];
    spaceAgeDlc = false;
    factorioPlanetResources = {};
    factorioMapOverrides = {};
    activeFactorioTab = 'nauvis';
    mcServerType = 'FABRIC';
    mcVersion = '';
    modpackQuery = '';
    modpackResults = [];
    selectedModpack = null;
    mcModQuery = '';
    mcModResults = [];
    selectedMcMods = [];
    enableGeyser = false;
    geyserPort = '19132';
    enableOptimization = false;
  }

  async function loadMcVersions() {
    if (mcVersions.length > 0) return;
    try {
      const res = await fetch('/api/minecraft/versions');
      if (res.ok) {
        const data = await res.json();
        mcVersions = data.versions || [];
        if (mcVersions.length > 0 && !mcVersion) mcVersion = mcVersions[0];
      }
    } catch (e) {}
  }

  async function loadFactorioVersionLabels() {
    try {
      const res = await fetch('/api/factorio/versions');
      if (res.ok) {
        factorioVersionLabels = await res.json();
        if (!factorioVersion) factorioVersion = 'stable';
      }
    } catch (e) {}
  }

  async function loadFactorioPresets() {
    try {
      const res = await fetch(`/api/factorio/map-presets${spaceAgeDlc ? '?space_age=true' : ''}`);
      if (res.ok) {
        const data = await res.json();
        factorioPresets = data.presets || [];
        if (data.planets) factorioPlanetResources = data.planets;
      }
    } catch (e) {}
  }

  //Reload presets with planet data when DLC checkbox changes
  $effect(() => {
    if (selectedTemplate?.game_type === 'factorio') loadFactorioPresets();
  });

  async function searchFactorioMods() {
    if (!factorioModQuery.trim()) return;
    factorioSearching = true;
    try {
      const fv = factorioVersion === 'stable' ? '2.0' : '2.1';
      const exp = spaceAgeDlc ? '&expansion=space-age' : '';
      const res = await fetch(`/api/factorio/mods/search?query=${encodeURIComponent(factorioModQuery)}&limit=15&factorio_version=${encodeURIComponent(fv)}${exp}`);
      if (res.ok) {
        const data = await res.json();
        factorioModResults = data.results || [];
      }
    } catch (e) {}
    factorioSearching = false;
  }

  function addFactorioMod(mod) {
    if (!selectedFactorioMods.find(m => m.name === mod.name)) {
      selectedFactorioMods = [...selectedFactorioMods, mod];
    }
  }

  function removeFactorioMod(name) {
    selectedFactorioMods = selectedFactorioMods.filter(m => m.name !== name);
  }

  async function searchModpacks() {
    if (!modpackQuery.trim()) return;
    modpackSearching = true;
    try {
      const verParam = mcVersion ? `&game_version=${encodeURIComponent(mcVersion)}` : '';
      const res = await fetch(`/api/minecraft/modpacks/search?query=${encodeURIComponent(modpackQuery)}${verParam}`);
      if (res.ok) {
        const data = await res.json();
        modpackResults = data.results || [];
      }
    } catch (e) {}
    modpackSearching = false;
  }

  function pickModpack(mp) {
    selectedModpack = mp;
    mcServerType = 'AUTO_CURSEFORGE';
  }

  async function searchMcMods() {
    if (!mcModQuery.trim()) return;
    mcModSearching = true;
    try {
      let facetArray = [["categories:fabric"], ["project_type:mod"]];
      if (mcVersion) facetArray.push([`versions:${mcVersion}`]);
      const facets = encodeURIComponent(JSON.stringify(facetArray));
      const res = await fetch(`/api/modrinth/search?query=${encodeURIComponent(mcModQuery)}&limit=15&facets=${facets}`);
      if (res.ok) {
        const data = await res.json();
        mcModResults = data.hits || [];
      }
    } catch (e) {}
    mcModSearching = false;
  }

  async function addMcMod(mod) {
    if (selectedMcMods.find(m => m.slug === mod.slug)) return;
    //Resolve the latest fabric release file for this mod
    try {
      let queryParams = '?loaders=%5B%22fabric%22%5D';
      if (mcVersion) queryParams += `&game_versions=${encodeURIComponent(JSON.stringify([mcVersion]))}`;
      const verRes = await fetch(`https://api.modrinth.com/v2/project/${mod.slug}/version${queryParams}`);
      if (!verRes.ok) throw new Error('version lookup failed');
      let versions = await verRes.json();
      versions = versions.filter(v => v.version_type === 'release');
      if (mcVersion) versions = versions.filter(v => v.game_versions.includes(mcVersion));
      if (versions.length === 0) { alert(`No stable Fabric release for ${mod.title}.`); return; }
      const f = versions[0].files.find(x => x.primary) || versions[0].files[0];
      selectedMcMods = [...selectedMcMods, { slug: mod.slug, title: mod.title, filename: f.filename, download_url: f.url }];
    } catch (e) { alert(`Failed to resolve ${mod.title}: ${e.message}`); }
  }

  function removeMcMod(slug) {
    selectedMcMods = selectedMcMods.filter(m => m.slug !== slug);
  }

  function buildGameOptions() {
    if (!selectedTemplate) return {};
    const gt = selectedTemplate.game_type;
    if (gt === 'factorio') {
      //Build map-gen overrides from planet table edits (all planets merged into one settings object)
      let mapOverrides = undefined;
      if (mapGenPreset === '' && Object.keys(factorioMapOverrides).length > 0) {
        const settings = { autoplace_controls: {} };
        const allResources = spaceAgeDlc ? factorioPlanetResources : {nauvis: factorioPlanetResources.nauvis};
        //If no planets loaded yet (non-DLC default), fall back to default preset resources
        const fallback = factorioPresets.find(p => p.id === 'default');
        if (!allResources.nauvis && fallback?.resources) allResources.nauvis = fallback.resources;
        for (const [planet, resources] of Object.entries(allResources)) {
          for (const [resource, attrs] of Object.entries(resources)) {
            settings.autoplace_controls[resource] = {};
            for (const attr of ['frequency', 'size', 'richness']) {
              const def = attrs[attr];
              const edited = factorioMapOverrides[planet]?.[resource]?.[attr];
              //UI stores percent (25-500); JSON needs raw decimal (÷100)
              if (edited !== undefined) settings.autoplace_controls[resource][attr] = edited / 100;
              else if (def !== undefined) settings.autoplace_controls[resource][attr] = def;
            }
          }
        }
        mapOverrides = settings;
      }
      return {
        factorio_version: factorioVersion || undefined,
        space_age_dlc: spaceAgeDlc || undefined,
        map_gen_preset: mapGenPreset || undefined,
        map_gen_overrides: mapOverrides,
        mods: selectedFactorioMods.length > 0
          ? selectedFactorioMods.map(m => ({
              name: m.name, download_url: m.download_url, filename: m.file_name || m.filename
            }))
          : undefined,
      };
    }
    if (gt === 'minecraft') {
      const opts = {
        server_type: mcServerType || undefined,
        mc_version: mcVersion.trim() || undefined,
        modpack_url: selectedModpack ? selectedModpack.page_url : undefined,
        fabric_mods: mcServerType === 'FABRIC' && selectedMcMods.length > 0
          ? selectedMcMods.map(m => ({ download_url: m.download_url, filename: m.filename }))
          : undefined,
        enable_geyser: mcServerType === 'FABRIC' && enableGeyser || undefined,
        geyser_port: mcServerType === 'FABRIC' && enableGeyser ? parseInt(geyserPort) || 19132 : undefined,
        enable_optimization: mcServerType === 'FABRIC' && enableOptimization || undefined,
      };
      return opts;
    }
    return {};
  }

  async function handleCreate() {
    error = '';
    if (!serverId.trim()) {
      error = 'Server ID is required';
      return;
    }
    step = 3;
    let parsedPorts = {};
    try {
      parsedPorts = JSON.parse(ports);
    } catch (e) {
      error = 'Invalid JSON in ports field';
      step = 2;
      return;
    }

    const payload = {
      server_id: serverId,
      name: serverName,
      template: mode === 'template' && selectedTemplate ? selectedTemplate.key : 'custom',
      image: image,
      description: description,
      ports: parsedPorts,
      container_path: containerPath,
      client_port: clientPort ? parseInt(clientPort) : null,
      env: {},
      ram_limit: ramLimit,
      start_now: startNow,
      game_options: buildGameOptions(),
    };

    try {
      const res = await fetch('/api/servers/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (res.ok && (data.status === 'success' || data.status === 'warning')) {
        success = data.status === 'warning' ? data.message : 'Server created successfully!';
        //Wait a moment for Docker to settle, then close
        setTimeout(() => {
          onCreated?.(data.server_id);
        }, 1500);
      } else {
        error = data.detail || 'Creation failed';
        step = 2;
      }
    } catch (e) {
      error = 'Request failed: ' + e.message;
      step = 2;
    }
  }

  //Group templates by category
  let templatesByCategory = $derived(
    templates.reduce((acc, t) => {
      const cat = t.category || 'Other';
      if (!acc[cat]) acc[cat] = [];
      acc[cat].push(t);
      return acc;
    }, {})
  );
</script>

{#if step === 1}
  <!--Step 1: Pick template or search Docker Hub-->
  <div class="space-y-6">
    <div class="text-center">
      <h2 class="text-2xl font-bold text-white mb-2">Add a Server</h2>
      <p class="text-gray-400 text-sm">Pick a template below, search Docker Hub, or create a custom container.</p>
    </div>

    <!--Template grid-->
    {#if Object.keys(templatesByCategory).length > 0}
      {#each Object.entries(templatesByCategory) as [category, items]}
        <div>
          <h3 class="text-xs uppercase tracking-wider text-gray-500 mb-2 font-semibold">{category}</h3>
          <div class="grid grid-cols-2 md:grid-cols-3 gap-3">
            {#each items as t}
              <button
                onclick={() => pickTemplate(t)}
                class="bg-[#1f2937] hover:bg-[#374151] border border-gray-700 hover:border-gray-500 rounded-lg p-4 text-left transition-colors group">
                <div class="font-semibold text-gray-200 group-hover:text-white text-sm">{t.label}</div>
                <div class="text-xs text-gray-500 mt-1 font-mono truncate">{t.image}</div>
              </button>
            {/each}
          </div>
        </div>
      {/each}
    {/if}

    <!--Docker Hub search-->
    <div class="border-t border-gray-700 pt-6">
      <h3 class="text-xs uppercase tracking-wider text-gray-500 mb-3 font-semibold">Search Docker Hub</h3>
      <div class="flex gap-2 mb-3">
        <input
          type="text"
          bind:value={searchQuery}
          onkeydown={(e) => e.key === 'Enter' && doSearch()}
          placeholder="e.g. terraria, navidrome, grafana..."
          class="flex-1 bg-[#111827] border border-gray-700 rounded-lg px-4 py-2 text-gray-200 text-sm focus:outline-none focus:border-gray-500" />
        <button
          onclick={doSearch}
          disabled={searching}
          class="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50">
          {searching ? '...' : 'Search'}
        </button>
      </div>
      {#if searchResults.length > 0}
        <div class="space-y-2 max-h-60 overflow-y-auto">
          {#each searchResults as r}
            <button
              onclick={() => pickImage(r.name)}
              class="w-full bg-[#1f2937] hover:bg-[#374151] border border-gray-700 hover:border-gray-500 rounded-lg p-3 text-left transition-colors">
              <div class="flex justify-between items-start">
                <div class="flex-1 min-w-0">
                  <div class="font-mono text-sm text-gray-200">{r.name}</div>
                  <div class="text-xs text-gray-500 mt-1 line-clamp-2">{r.description}</div>
                </div>
                <div class="text-xs text-gray-600 ml-2 flex-shrink-0 text-right">
                  {r.star_count > 0 ? `★ ${r.star_count}` : ''}
                </div>
              </div>
            </button>
          {/each}
        </div>
      {:else if searching}
        <p class="text-gray-500 text-sm">Searching...</p>
      {/if}
    </div>

    <!--Custom mode-->
    <div class="border-t border-gray-700 pt-6">
      <button
        onclick={startCustom}
        class="w-full bg-[#1f2937] hover:bg-[#374151] border border-gray-700 hover:border-gray-500 rounded-lg p-4 text-left transition-colors">
        <div class="font-semibold text-gray-300 text-sm">+ Custom Container</div>
        <div class="text-xs text-gray-500 mt-1">Manually specify image, ports, and volumes for any Docker image</div>
      </button>
    </div>

    {#if error}
      <div class="text-red-400 text-sm">{error}</div>
    {/if}
  </div>

{:else if step === 2}
  <!--Step 2: Configure-->
  <div class="space-y-5">
    <div class="flex items-center justify-between">
      <h2 class="text-xl font-bold text-white">
        {mode === 'template' ? selectedTemplate?.label : 'Custom Container'}
      </h2>
      <button onclick={() => step = 1} class="text-gray-500 hover:text-gray-300 text-sm">&larr; Back</button>
    </div>

    {#if mode === 'custom'}
      <div class="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3">
        <p class="text-amber-300 text-xs">
          Manual mode: you need to know the correct ports and volume path for this image.
          Check the image's documentation on Docker Hub.
        </p>
      </div>
    {/if}

    <div class="space-y-4">
      <div>
        <label class="block text-xs uppercase tracking-wider text-gray-500 mb-1">Server ID</label>
        <input bind:value={serverId} placeholder="e.g. terraria_01"
          class="w-full bg-[#111827] border border-gray-700 rounded-lg px-4 py-2 text-gray-200 text-sm font-mono focus:outline-none focus:border-gray-500" />
        <p class="text-xs text-gray-600 mt-1">Unique identifier, no spaces (auto-generated from name)</p>
      </div>
      <div>
        <label class="block text-xs uppercase tracking-wider text-gray-500 mb-1">Display Name</label>
        <input bind:value={serverName}
          class="w-full bg-[#111827] border border-gray-700 rounded-lg px-4 py-2 text-gray-200 text-sm focus:outline-none focus:border-gray-500" />
      </div>
      <div>
        <label class="block text-xs uppercase tracking-wider text-gray-500 mb-1">Description (optional)</label>
        <input bind:value={description}
          class="w-full bg-[#111827] border border-gray-700 rounded-lg px-4 py-2 text-gray-200 text-sm focus:outline-none focus:border-gray-500" />
      </div>
      <div>
        <label class="block text-xs uppercase tracking-wider text-gray-500 mb-1">Docker Image</label>
        <input bind:value={image} placeholder="e.g. factoriotools/factorio:stable"
          class="w-full bg-[#111827] border border-gray-700 rounded-lg px-4 py-2 text-gray-200 text-sm font-mono focus:outline-none focus:border-gray-500" />
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-xs uppercase tracking-wider text-gray-500 mb-1">Container Path</label>
          <input bind:value={containerPath}
            class="w-full bg-[#111827] border border-gray-700 rounded-lg px-4 py-2 text-gray-200 text-sm font-mono focus:outline-none focus:border-gray-500" />
        </div>
        <div>
          <label class="block text-xs uppercase tracking-wider text-gray-500 mb-1">Client Port</label>
          <input bind:value={clientPort} placeholder="e.g. 34197"
            class="w-full bg-[#111827] border border-gray-700 rounded-lg px-4 py-2 text-gray-200 text-sm font-mono focus:outline-none focus:border-gray-500" />
        </div>
      </div>
      <div>
        <label class="block text-xs uppercase tracking-wider text-gray-500 mb-1">RAM Limit</label>
        <input bind:value={ramLimit} placeholder="0g"
          class="w-full bg-[#111827] border border-gray-700 rounded-lg px-4 py-2 text-gray-200 text-sm font-mono focus:outline-none focus:border-gray-500" />
      </div>
      {#if mode === 'custom'}
      <div>
        <label class="block text-xs uppercase tracking-wider text-gray-500 mb-1">Ports (JSON)</label>
        <textarea bind:value={ports} rows="3"
          class="w-full bg-[#111827] border border-gray-700 rounded-lg px-4 py-2 text-gray-200 text-sm font-mono focus:outline-none focus:border-gray-500"
          placeholder="Map: container_port to host_port"></textarea>
        <p class="text-xs text-gray-600 mt-1">JSON map of container_port to host_port</p>
      </div>
      {/if}

      <!--Factorio-specific creation options-->
      {#if mode === 'template' && selectedTemplate?.game_type === 'factorio'}
        <div class="border border-gray-700 rounded-lg p-4 bg-gray-900/50 space-y-4">
          <h3 class="text-sm font-semibold text-emerald-400 uppercase tracking-wide">Factorio Options</h3>
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label class="block text-xs uppercase tracking-wider text-gray-500 mb-1">Game Version</label>
              <select bind:value={factorioVersion}
                class="w-full bg-[#111827] border border-gray-700 rounded-lg px-4 py-2 text-gray-200 text-sm focus:outline-none focus:border-gray-500">
                {#if factorioVersionLabels.stable}
                  <option value="stable">Stable ({factorioVersionLabels.stable})</option>
                {/if}
                {#if factorioVersionLabels.experimental}
                  <option value="latest">Experimental ({factorioVersionLabels.experimental})</option>
                {/if}
                {#if factorioVersionLabels.all_versions?.length > 0}
                  <optgroup label="Previous Versions">
                    {#each factorioVersionLabels.all_versions as v}
                      <option value={v}>{v}</option>
                    {/each}
                  </optgroup>
                {/if}
              </select>
            </div>
            <div class="flex items-end">
              <label class="flex items-center gap-2 cursor-pointer pb-2">
                <input type="checkbox" bind:checked={spaceAgeDlc} class="rounded" />
                <span class="text-sm text-gray-300">Space Age DLC</span>
              </label>
            </div>
          </div>
          <div>
            <label class="block text-xs uppercase tracking-wider text-gray-500 mb-1">Map Generation Preset (Nauvis)</label>
            <select bind:value={mapGenPreset}
              class="w-full bg-[#111827] border border-gray-700 rounded-lg px-4 py-2 text-gray-200 text-sm focus:outline-none focus:border-gray-500">
              <option value="">Default (no preset)</option>
              {#each factorioPresets as p}
                <option value={p.id}>{p.label} — {p.description}</option>
              {/each}
            </select>
            <p class="text-xs text-gray-600 mt-1">Fills the table below with preset values. Only affects Nauvis.</p>
          </div>
          {#if mapGenPreset === '' && Object.keys(factorioPlanetResources).length > 0}
            <div>
              <label class="block text-xs uppercase tracking-wider text-gray-500 mb-2">Custom Map Generation</label>
              <!--Planet tabs (only shown with Space Age DLC; non-DLC shows Nauvis only)-->
              {#if spaceAgeDlc}
                <div class="flex gap-1 mb-2 flex-wrap">
                  {#each Object.keys(factorioPlanetResources) as planet}
                    <button onclick={() => activeFactorioTab = planet}
                      class="px-3 py-1 text-xs rounded font-medium capitalize transition-colors {activeFactorioTab === planet ? 'bg-emerald-600 text-white' : 'bg-[#1f2937] text-gray-400 hover:text-gray-200'}">
                      {planet}
                    </button>
                  {/each}
                </div>
              {/if}
              {#if true}
              {@const currentResources = factorioPlanetResources[activeFactorioTab] || factorioPlanetResources.nauvis || {}}
              <div class="overflow-x-auto">
                <datalist id="ore-ticks-modal">
                  {#each ORE_PRESETS as pct}<option value={pct}></option>{/each}
                </datalist>
                <table class="w-full text-xs">
                  <thead>
                    <tr class="text-gray-500 border-b border-gray-700/50">
                      <th class="text-left py-1 pr-2">resource</th>
                      {#each ['frequency', 'size', 'richness'] as attr}
                        <th class="text-center py-1 px-1">{attr}</th>
                      {/each}
                    </tr>
                  </thead>
                  <tbody>
                    {#each Object.entries(currentResources) as [resource, attrs]}
                      <tr class="border-b border-gray-800/50">
                        <td class="py-1.5 pr-2 font-mono text-gray-300 whitespace-nowrap">{resource}</td>
                        {#each ['frequency', 'size', 'richness'] as attr}
                          <td class="py-1.5 px-1 text-center">
                            {#if attrs[attr] !== undefined}
                              <div class="flex items-center gap-1 justify-center">
                                <input type="range" min="25" max="500" step="1"
                                  value={factorioMapOverrides[activeFactorioTab]?.[resource]?.[attr] ?? Math.round((attrs[attr]) * 100)}
                                  oninput={(e) => updateMapOverride(activeFactorioTab, resource, attr, Number(e.currentTarget.value))}
                                  class="w-16 accent-emerald-500"
                                  list="ore-ticks-modal" />
                                <input type="number" min="25" max="500" step="1"
                                  value={factorioMapOverrides[activeFactorioTab]?.[resource]?.[attr] ?? Math.round((attrs[attr]) * 100)}
                                  oninput={(e) => updateMapOverride(activeFactorioTab, resource, attr, Number(e.currentTarget.value))}
                                  class="w-14 bg-[#0b0f19] border border-gray-600 text-gray-300 rounded px-1.5 py-1 text-xs focus:outline-none focus:border-emerald-500" />
                                <span class="text-[10px] text-gray-500">%</span>
                              </div>
                            {/if}
                          </td>
                        {/each}
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
              {/if}
            </div>
          {/if}
          <div>
            <label class="block text-xs uppercase tracking-wider text-gray-500 mb-1">Mods (optional)</label>
            <div class="flex gap-2 mb-2">
              <input type="text" bind:value={factorioModQuery}
                onkeydown={(e) => e.key === 'Enter' && (e.preventDefault(), searchFactorioMods())}
                placeholder="Search mods (e.g. Krastorio 2)..."
                class="flex-1 min-w-0 bg-[#111827] border border-gray-700 rounded-lg px-3 py-2 text-gray-200 text-sm focus:outline-none focus:border-gray-500" />
              <button onclick={searchFactorioMods} disabled={factorioSearching}
                class="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg disabled:opacity-50">{factorioSearching ? '...' : 'Search'}</button>
            </div>
            {#if selectedFactorioMods.length > 0}
              <div class="space-y-1 mb-2">
                {#each selectedFactorioMods as mod}
                  <div class="flex items-center justify-between bg-emerald-500/10 border border-emerald-500/30 rounded px-3 py-1.5">
                    <span class="text-sm text-emerald-300 truncate">{mod.title || mod.name}</span>
                    <button onclick={() => removeFactorioMod(mod.name)} class="text-red-400 hover:text-red-300 text-xs ml-2 flex-shrink-0">Remove</button>
                  </div>
                {/each}
              </div>
            {/if}
            {#if factorioModResults.length > 0}
              <div class="space-y-1 max-h-48 overflow-y-auto">
                {#each factorioModResults as mod}
                  <div class="flex items-center justify-between bg-[#1f2937] border border-gray-700 rounded px-3 py-2">
                    <div class="flex-1 min-w-0 mr-2">
                      <div class="text-sm text-gray-200 truncate">{mod.title}</div>
                      <div class="text-xs text-gray-500">{mod.owner} · v{mod.version} · fv {mod.factorio_version}</div>
                    </div>
                    {#if selectedFactorioMods.find(m => m.name === mod.name)}
                      <span class="text-emerald-400 text-xs flex-shrink-0">Added</span>
                    {:else}
                      <button onclick={() => addFactorioMod(mod)}
                        class="text-blue-400 hover:text-blue-300 text-xs flex-shrink-0">+ Add</button>
                    {/if}
                  </div>
                {/each}
              </div>
            {/if}
          </div>
        </div>
      {/if}

      <!--Minecraft-specific creation options-->
      {#if mode === 'template' && selectedTemplate?.game_type === 'minecraft'}
        <div class="border border-gray-700 rounded-lg p-4 bg-gray-900/50 space-y-4">
          <h3 class="text-sm font-semibold text-emerald-400 uppercase tracking-wide">Minecraft Options</h3>
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label class="block text-xs uppercase tracking-wider text-gray-500 mb-1">Server Type</label>
              <select bind:value={mcServerType}
                class="w-full bg-[#111827] border border-gray-700 rounded-lg px-4 py-2 text-gray-200 text-sm focus:outline-none focus:border-gray-500">
                <option value="FABRIC">Fabric (Mods)</option>
                <option value="AUTO_CURSEFORGE">CurseForge Modpack</option>
              </select>
            </div>
            <div>
              <label class="block text-xs uppercase tracking-wider text-gray-500 mb-1">MC Version</label>
              <select bind:value={mcVersion}
                class="w-full bg-[#111827] border border-gray-700 rounded-lg px-4 py-2 text-gray-200 text-sm focus:outline-none focus:border-gray-500">
                {#each mcVersions as v}
                  <option value={v}>{v}</option>
                {/each}
              </select>
            </div>
          </div>

          {#if mcServerType === 'AUTO_CURSEFORGE'}
            <!--CurseForge modpack search — only shown when CurseForge is the selected type-->
            <div>
              <label class="block text-xs uppercase tracking-wider text-gray-500 mb-1">CurseForge Modpack</label>
              <div class="flex gap-2 mb-2">
                <input type="text" bind:value={modpackQuery}
                  onkeydown={(e) => e.key === 'Enter' && (e.preventDefault(), searchModpacks())}
                  placeholder="Search CurseForge modpacks... (select a version to filter)"
                  class="flex-1 min-w-0 bg-[#111827] border border-gray-700 rounded-lg px-3 py-2 text-gray-200 text-sm focus:outline-none focus:border-gray-500" />
                <button onclick={searchModpacks} disabled={modpackSearching}
                  class="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg disabled:opacity-50">{modpackSearching ? '...' : 'Search'}</button>
              </div>
              {#if selectedModpack}
                <div class="flex items-center justify-between bg-emerald-500/10 border border-emerald-500/30 rounded px-3 py-2 mb-2">
                  <div class="flex-1 min-w-0">
                    <div class="text-sm text-emerald-300 truncate">{selectedModpack.name}</div>
                    <div class="text-xs text-gray-500 truncate">{selectedModpack.page_url}</div>
                  </div>
                  <button onclick={() => { selectedModpack = null; mcServerType = 'FABRIC'; }}
                    class="text-red-400 hover:text-red-300 text-xs ml-2 flex-shrink-0">Remove</button>
                </div>
              {/if}
              {#if modpackResults.length > 0 && !selectedModpack}
                <div class="space-y-1 max-h-48 overflow-y-auto">
                  {#each modpackResults as mp}
                    <div class="flex items-center justify-between bg-[#1f2937] border border-gray-700 rounded px-3 py-2">
                      <div class="flex-1 min-w-0 mr-2">
                        <div class="text-sm text-gray-200 truncate">{mp.name}</div>
                        <div class="text-xs text-gray-500 line-clamp-1">{mp.summary}</div>
                        <div class="text-xs text-gray-600">{(mp.download_count / 1000).toFixed(0)}k downloads</div>
                      </div>
                      <button onclick={() => pickModpack(mp)}
                        class="text-blue-400 hover:text-blue-300 text-xs flex-shrink-0">+ Use</button>
                    </div>
                  {/each}
                </div>
              {/if}
              {#if mcVersion}
                <p class="text-xs text-gray-600 mt-1">Filtered to MC {mcVersion} compatible modpacks.</p>
              {/if}
            </div>
          {/if}

          {#if mcServerType === 'FABRIC'}
            <!--Fabric mod search + one-click checkboxes — only for Fabric servers-->
            <div>
              <label class="block text-xs uppercase tracking-wider text-gray-500 mb-1">Fabric Mods (optional)</label>
              <div class="flex gap-2 mb-2">
                <input type="text" bind:value={mcModQuery}
                  onkeydown={(e) => e.key === 'Enter' && (e.preventDefault(), searchMcMods())}
                  placeholder="Search Modrinth for Fabric mods..."
                  class="flex-1 min-w-0 bg-[#111827] border border-gray-700 rounded-lg px-3 py-2 text-gray-200 text-sm focus:outline-none focus:border-gray-500" />
                <button onclick={searchMcMods} disabled={mcModSearching}
                  class="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg disabled:opacity-50">{mcModSearching ? '...' : 'Search'}</button>
              </div>
              {#if selectedMcMods.length > 0}
                <div class="space-y-1 mb-2">
                  {#each selectedMcMods as mod}
                    <div class="flex items-center justify-between bg-emerald-500/10 border border-emerald-500/30 rounded px-3 py-1.5">
                      <div class="flex-1 min-w-0">
                        <div class="text-sm text-emerald-300 truncate">{mod.title}</div>
                        <div class="text-xs text-gray-500 truncate">{mod.filename}</div>
                      </div>
                      <button onclick={() => removeMcMod(mod.slug)} class="text-red-400 hover:text-red-300 text-xs ml-2 flex-shrink-0">Remove</button>
                    </div>
                  {/each}
                </div>
              {/if}
              {#if mcModResults.length > 0}
                <div class="space-y-1 max-h-48 overflow-y-auto">
                  {#each mcModResults as mod}
                    <div class="flex items-center justify-between bg-[#1f2937] border border-gray-700 rounded px-3 py-2">
                      <div class="flex-1 min-w-0 mr-2">
                        <div class="text-sm text-gray-200 truncate">{mod.title}</div>
                        <div class="text-xs text-gray-500">{mod.description?.substring(0, 80)}</div>
                      </div>
                      {#if selectedMcMods.find(m => m.slug === mod.slug)}
                        <span class="text-emerald-400 text-xs flex-shrink-0">Added</span>
                      {:else}
                        <button onclick={() => addMcMod(mod)}
                          class="text-blue-400 hover:text-blue-300 text-xs flex-shrink-0">+ Add</button>
                      {/if}
                    </div>
                  {/each}
                </div>
              {/if}
            </div>
            <!--One-click optimization mods (Lithium, FerriteCore, Krypton, ServerCore, Fabric API)-->
            <label class="flex items-center gap-2 cursor-pointer bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-3 py-2.5">
              <input type="checkbox" bind:checked={enableOptimization} class="rounded" />
              <div>
                <div class="text-sm text-emerald-300 font-medium">Optimization Mods</div>
                <div class="text-xs text-gray-500">Lithium, FerriteCore, Krypton, ServerCore + Fabric API. Server-side TPS optimization.</div>
              </div>
            </label>
            <!--One-click Geyser + Floodgate for Bedrock crossplay-->
            <label class="flex items-center gap-2 cursor-pointer bg-blue-500/10 border border-blue-500/30 rounded-lg px-3 py-2.5">
              <input type="checkbox" bind:checked={enableGeyser} class="rounded" />
              <div class="flex-1">
                <div class="text-sm text-blue-300 font-medium">Enable Geyser + Floodgate (Bedrock Crossplay)</div>
                <div class="text-xs text-gray-500">Installs Fabric API + both mods. Opens a UDP port for Bedrock players.</div>
              </div>
              {#if enableGeyser}
                <input type="number" bind:value={geyserPort} min="1024" max="65535"
                  class="w-20 bg-[#0b0f19] border border-blue-500/30 text-blue-300 rounded px-2 py-1 text-xs text-center focus:outline-none focus:border-blue-500"
                  title="Bedrock UDP port" />
                <span class="text-xs text-gray-500">/udp</span>
              {/if}
            </label>
          {/if}
        </div>
      {/if}

      <label class="flex items-center gap-2 cursor-pointer">
        <input type="checkbox" bind:checked={startNow} class="rounded" />
        <span class="text-sm text-gray-300">Start container immediately after creation</span>
      </label>
    </div>

    {#if error}
      <div class="text-red-400 text-sm bg-red-500/10 border border-red-500/30 rounded-lg p-3">{error}</div>
    {/if}

    <div class="flex gap-3 pt-2">
      <button
        onclick={handleCreate}
        class="flex-1 px-6 py-3 bg-green-600 hover:bg-green-700 text-white font-medium rounded-lg transition-colors">
        Create &amp; {startNow ? 'Start' : 'Save'}
      </button>
      <button onclick={() => onClose?.()} class="px-6 py-3 bg-gray-700 hover:bg-gray-600 text-gray-300 font-medium rounded-lg transition-colors">Cancel</button>
    </div>
  </div>

{:else if step === 3}
  <!--Step 3: Creating / success-->
  <div class="text-center py-12 space-y-4">
    {#if success}
      <div class="text-green-400 text-5xl mb-4">&#10003;</div>
      <h2 class="text-xl font-bold text-white">Server Created!</h2>
      <p class="text-gray-400 text-sm">{success}</p>
      <p class="text-gray-500 text-xs">Redirecting to dashboard...</p>
    {:else}
      <div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-gray-400 mb-4"></div>
      <h2 class="text-xl font-bold text-white">Creating Server...</h2>
      <p class="text-gray-400 text-sm">Pulling image and starting container. This may take a minute.</p>
      {#if error}
        <div class="text-red-400 text-sm bg-red-500/10 border border-red-500/30 rounded-lg p-3 mt-4">{error}</div>
      {/if}
    {/if}
  </div>
{/if}
