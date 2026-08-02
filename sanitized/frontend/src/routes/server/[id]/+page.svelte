<script>
  import { onMount, untrack } from 'svelte';
  let { data } = $props();
  let role = data.role;  
  
  //Make the server data mutable so we can update it with background polling
  let server = $state(data.server); 
  //Dynamically calculate RAM percentage safely
  let ramPercentage = $derived(server.ram_allocated > 0 ? Math.round((server.ram_used / server.ram_allocated) * 100) : 0);
  //$derived so WS effect re-runs only on actual status transitions,
  //not every poll that reassigns server with the same status string.
  let serverStatus = $derived(server.status);

  //Sync route data into local state. untrack the read of server.id so
  //assigning server doesn't re-trigger this effect (infinite loop).
  $effect(() => {
      const incoming = data.server;
      const prevId = untrack(() => server.id);
      server = incoming;
      if (prevId !== incoming.id) logs = [];
  });

  //UI States
  let isStarting = $state(false);
  let isStopping = $state(false);
  let isRestarting = $state(false);
  let isRedeploying = $state(false); //NEW: Redeployment locking state
  let isCheckingUpdate = $state(false);
  /** @type {null | boolean | 'error'} */
  let updateAvailable = $state(null);
  let activeTab = $state('console'); 

  //Console States
  let logs = $state([]);
  let terminalDiv = $state();
  let commandInput = $state("");
  let isSendingCommand = $state(false);
  let isPaused = $state(false);

  //Mod Manager States
  let installedMods = $state([]);
  let searchResults = $state([]);
  let searchQuery = $state("");
  let isSearching = $state(false);

  //Define the connection address in this dictionary
  const customDomains = {
      'jellyfin': 'https://<DOMAIN>',
      'wizarr': 'https://<DOMAIN>',
      'open-webui': 'https://<DOMAIN>',
      'llama-swap': 'N/A',
      'adguardhome': 'https://<DOMAIN>',
      'lunkbot': 'N/A'
    }

  let connectionAddress = $derived(
      customDomains[server.id] || `<DOMAIN>:${server.client_port}`
  );

  //Bedrock (UDP) port — if the recipe exposes a /udp port, show it for Geyser servers
  let bedrockPort = $derived.by(() => {
    if (server.game_type !== 'minecraft') return null;
    const ports = server.ports || {};
    for (const [k, v] of Object.entries(ports)) {
      if (k.endsWith('/udp')) return v;
    }
    return null;
  });

  //==========================================
  //BACKGROUND LOOPS & EFFECTS
  //==========================================

  //1. Auto-scroll Terminal (respects pause: if user scrolled up, don't yank)
  $effect(() => {
    if (logs.length > 0 && terminalDiv && !isPaused) {
      terminalDiv.scrollTop = terminalDiv.scrollHeight;
    }
  });

  //2. The Self-Healing WebSocket Manager - optimized to reduce connection overhead
  //Tracks serverStatus ($derived) so start/stop transitions re-trigger this
  //effect — connect when newly running, disconnect when newly stopped.
  $effect(() => {
    //Read the derived status so the effect re-runs on real transitions.
    const status = serverStatus;
    
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    
    let ws = null;
    let isIntentionalClose = false;
    let reconnectTimer = null;
    let isConnecting = false;
    const currentId = data.server.id;

    function connect() {
        if (isConnecting || ws?.readyState === WebSocket.CONNECTING) return;
        if (status !== 'running' && status !== 'restarting') {
            if (ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING) {
                isIntentionalClose = true;
                ws.close();
                ws = null;
                isIntentionalClose = false;
            }
            return;
        }

        isConnecting = true;
        ws = new WebSocket(`${protocol}//${host}/api/servers/${currentId}/logs`);

        ws.onmessage = (event) => {
            logs = [...logs, event.data];
            if (logs.length > 200) logs = logs.slice(logs.length - 200);
        };

        ws.onclose = () => {
            isConnecting = false;
            if (!isIntentionalClose && (status === 'running' || status === 'restarting')) {
                logs = [...logs, "[System] Connection lost. Reconnecting..."];
                //Exponential backoff: 1s, 2s, 4s, max 8s
                reconnectTimer = setTimeout(connect, Math.min(1000 * Math.pow(2, Math.floor(Math.random() * 3)), 8000));
            }
        };

        ws.onerror = () => {
            ws?.close();
        };
    }

    //Connect immediately if server is currently running
    if (status === 'running' || status === 'restarting') {
        connect();
    }

    return () => {
        isIntentionalClose = true;
        if (reconnectTimer) clearTimeout(reconnectTimer);
        if (ws) ws.close();
        ws = null;
        isConnecting = false;
    };
  });

  //3. Live Resource Polling (CPU/RAM) - recursive setTimeout so a slow
  //fetch never overlaps with the next tick (was NS_BINDING_ABORTED).
  //untrack server.status so assigning the poll result doesn't re-run this.
  $effect(() => {
    const currentId = data.server.id;
    let alive = true;
    let timer = null;

    async function tick() {
        if (!alive) return;
        const status = untrack(() => server.status);
        if (status === 'running' || status === 'restarting') {
            try {
                const response = await fetch(`/api/servers/${currentId}`);
                if (response.ok) {
                    const fresh = await response.json();
                    //Merge to avoid replacing object identity on every poll
                    //(was re-triggering the WS effect).
                    server = { ...server, ...fresh };
                }
            } catch (error) {
                console.error("Background stat poll failed:", error);
            }
        }
        timer = setTimeout(tick, 5000);
    }
    tick();

    return () => {
        alive = false;
        if (timer) clearTimeout(timer);
    };
  });

  //4. Reload Mod List when tab opens
  $effect(() => {
      if (activeTab === 'mods') {
          loadInstalledMods();
      }
  });


  //==========================================
  //BUTTON ACTIONS
  //==========================================

  //Re-fetch server state without reloading the page (avoids NS_BINDING_ABORTED
  //on in-flight polls and skips the full SvelteKit navigation round-trip)
  async function refreshServer() {
      try {
          const res = await fetch(`/api/servers/${server.id}`, { credentials: 'same-origin' });
          if (res.ok) server = await res.json();
      } catch (err) {
          console.error("Refresh failed:", err);
      }
  }

  async function handleAction(action) {
    if (action === 'start') isStarting = true;
    if (action === 'stop') isStopping = true;
    if (action === 'restart') isRestarting = true;

    try {
        const response = await fetch(`/api/servers/${server.id}/${action}`, { 
            method: 'POST',
            credentials: 'same-origin' 
        });
        
        if (!response.ok) throw new Error(`Failed to ${action}: ${response.statusText}`);
        
        //Immediate refresh so the UI reflects the new state, then a delayed
        //one for the container status to actually change in Docker.
        refreshServer();
        setTimeout(refreshServer, 2000);
        //Tell the sidebar to re-fetch now — don't wait for the 45s poll.
        window.dispatchEvent(new CustomEvent('lunk:fleet-refresh'));
    } catch (error) {
        console.error(`Failed to ${action}:`, error);
    } finally {
        isStarting = false; isStopping = false; isRestarting = false;
    }
  }

  //NEW: The Redeployment Sequence
  async function handleRedeploy() {
    if (!confirm(`WARNING: This will forcefully destroy and rebuild the ${server.id} container from scratch using the latest recipes.py configuration. Continue?`)) {
        return;
    }

    isRedeploying = true;
    try {
        const response = await fetch(`/api/redeploy/${server.id}`, {
            method: 'POST',
            credentials: 'same-origin'
        });
        
        const responseData = await response.json();
        
        if (response.ok) {
            alert(responseData.message || "Server successfully rebuilt!");
            setTimeout(refreshServer, 1000);
        } else {
            //FastAPI throws standard detail dictionaries on HTTP exceptions
            alert(`Error rebuilding container: ${responseData.detail || "Unknown API error."}`);
        }
    } catch (error) {
        console.error("Redeployment failed:", error);
        alert("A network error occurred while trying to rebuild the server.");
    } finally {
        isRedeploying = false;
    }
  }

  async function checkForUpdate() {
    isCheckingUpdate = true;
    updateAvailable = null;
    try {
        const response = await fetch(`/api/servers/${server.id}/update-check`, {
            credentials: 'same-origin'
        });
        const data = await response.json();
        if (data.error) {
            updateAvailable = 'error';
        } else {
            updateAvailable = data.update_available;
            if (data.update_available) {
                alert(`Update available for ${server.image}! Click "Force Rebuild" to pull and apply.`);
            }
        }
    } catch {
        updateAvailable = 'error';
    } finally {
        isCheckingUpdate = false;
    }
  }

  async function handleDelete() {
    if (!confirm(`Permanently delete "${server.id}"? This stops the container, removes it from Docker, and deletes the recipe. Data on disk is NOT deleted. Continue?`)) {
        return;
    }
    isDeleting = true;
    try {
        const res = await fetch(`/api/servers/${server.id}`, {
            method: 'DELETE',
            credentials: 'same-origin'
        });
        const data = await res.json();
        if (res.ok) {
            window.location.href = '/';
        } else {
            alert(data.detail || 'Delete failed');
        }
    } catch (e) {
        alert('Network error: ' + e.message);
    } finally {
        isDeleting = false;
    }
  }

  async function handleCommandSubmit(e) {
      e.preventDefault(); 
      if (!commandInput.trim() || isSendingCommand) return;
      
      isSendingCommand = true;
      const cmd = commandInput;
      commandInput = "";

      try {
          const response = await fetch(`/api/servers/${server.id}/command`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ command: cmd })
          });
          if (!response.ok) commandInput = cmd; 
      } catch (err) {
          console.error("Network error:", err);
      } finally {
          isSendingCommand = false;
      }
  }

  //==========================================
  //MOD MANAGER FUNCTIONS
  //==========================================

  async function loadInstalledMods() {
      try {
          const res = await fetch(`/api/servers/${server.id}/mods`);
          if (res.ok) {
              const data = await res.json();
              installedMods = data.mods;
              refreshTogglesFromMods();
          }
      } catch (err) {
          console.error("Failed to load mods:", err);
      }
  }

  async function searchModrinth(e) {
    e.preventDefault();
    if (!searchQuery.trim()) return;
      
    isSearching = true;
    try {
        let facetArray = [
            ["categories:fabric"], 
            ["project_type:mod"] 
        ];

        if (server.game_version && server.game_version !== 'unknown' && server.game_version !== 'latest') {
            facetArray.push([`versions:${server.game_version}`]);
        }

        const facets = encodeURIComponent(JSON.stringify(facetArray));
        const safeQuery = encodeURIComponent(searchQuery);

        const targetUrl = `/api/modrinth/search?query=${safeQuery}&limit=15&facets=${facets}`;
          
        console.log("Querying Python Proxy:", targetUrl); 

        const res = await fetch(targetUrl);
          
        if (!res.ok) throw new Error(`HTTP Error: ${res.status}`);
          
        const data = await res.json();
        searchResults = data.hits || [];
          
    } catch (err) {
        console.error("Modrinth proxy search failed:", err);
        alert("Failed to connect to backend proxy. Check browser console.");
    } finally {
        isSearching = false;
    }
}

  async function installMod(projectSlug) {
      try {
          const loaders = encodeURIComponent('["fabric"]');
          let queryParams = `?loaders=${loaders}`;
          
          if (server.game_version && server.game_version !== 'unknown') {
              const gameVersions = encodeURIComponent(`["${server.game_version}"]`);
              queryParams += `&game_versions=${gameVersions}`;
          }

          const verRes = await fetch(`https://api.modrinth.com/v2/project/${projectSlug}/version${queryParams}`);
          
          if (!verRes.ok) throw new Error("Failed to fetch versions from Modrinth");
          
          let versions = await verRes.json();
          
          //--- THE BULLETPROOF FILTERS ---
          versions = versions.filter(v => v.version_type === 'release');
          
          if (server.game_version && server.game_version !== 'unknown') {
              versions = versions.filter(v => v.game_versions.includes(server.game_version));
          }
          //------------------------------------------

          if (versions.length === 0) {
              return alert(`No stable Fabric release found for version ${server.game_version || 'this version'}.`);
          }
          
          const latestFile = versions[0].files.find(f => f.primary) || versions[0].files[0];
          
          const installRes = await fetch(`/api/servers/${server.id}/mods/install`, {
              method: 'POST',
              credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                  download_url: latestFile.url,
                  filename: latestFile.filename
              })
          });

          if (installRes.ok) {
              alert(`${latestFile.filename} installed! Restart server to apply.`);
              loadInstalledMods();
          } else {
              alert("Install failed. Check backend console.");
          }
      } catch (err) {
          console.error("Install sequence failed:", err);
          alert("An error occurred during installation.");
      }
  }

  async function deleteMod(filename) {
      if (!confirm(`Delete ${filename}?`)) return;
      
      try {
          const res = await fetch(`/api/servers/${server.id}/mods/${filename}`, { method: 'DELETE' });
          if (res.ok) loadInstalledMods();
      } catch (err) {
          console.error("Delete failed:", err);
      }
  }

  //==========================================
  //GEYSER + OPTIMIZATION TOGGLES (checkbox system)
  //==========================================

  let isInstallingGeyser = $state(false);
  let isTogglingOpt = $state(false);
  let geyserChecked = $state(false);
  let optimizationChecked = $state(false);
  let geyserPort = $state(19132);

  //Auto-check/uncheck based on installed mods. Called after loadInstalledMods.
  function refreshTogglesFromMods() {
    const names = (installedMods || []).map(m => m.toLowerCase().replace(/[-_]/g, ''));
    const has = (slug) => names.some(n => slug.replace(/[-_]/g, '').includes(slug));
    //Geyser needs: geyser + floodgate (fabric-api optional)
    const hasGeyser = has('geyser') && has('floodgate');
    //Optimization needs: lithium + ferritecore + krypton + servercore
    const hasOpt = has('lithium') && has('ferritecore') && has('krypton') && has('servercore');
    //Sync geyserPort from the recipe's existing UDP port so the field shows
    //the real value when Geyser is already installed.
    const ports = server.ports || {};
    const udpPort = Object.entries(ports).find(([k]) => k.endsWith('/udp'));
    if (udpPort) geyserPort = udpPort[1];
    //Auto-uncheck if any required mod is missing
    if (geyserChecked && !hasGeyser) geyserChecked = false;
    if (optimizationChecked && !hasOpt) optimizationChecked = false;
    //Auto-check if all present and not already checked
    if (!geyserChecked && hasGeyser) geyserChecked = true;
    if (!optimizationChecked && hasOpt) optimizationChecked = true;
  }

  async function toggleGeyser(checked) {
    if (checked) {
      isInstallingGeyser = true;
      try {
        const res = await fetch(`/api/servers/${server.id}/geyser-setup?game_version=${encodeURIComponent(server.game_version || '')}&bedrock_port=${geyserPort}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
          geyserChecked = true;
          await loadInstalledMods();
          await refreshServer(); //pick up new UDP port → bedrock IP display
        } else {
          alert(data.detail || 'Geyser setup failed.');
          geyserChecked = false;
        }
      } catch (err) {
        alert('Geyser setup failed — check console.');
        geyserChecked = false;
      }
      isInstallingGeyser = false;
    } else {
      //Uncheck → remove geyser mods
      try {
        await fetch(`/api/servers/${server.id}/remove-mods-by-slug`, {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ slugs: ['geyser', 'floodgate'] })
        });
        geyserChecked = false;
        await loadInstalledMods();
        await refreshServer();
      } catch (err) { geyserChecked = true; }
    }
  }

  async function toggleOptimization(checked) {
    if (checked) {
      isTogglingOpt = true;
      try {
        const res = await fetch(`/api/servers/${server.id}/optimization-setup?game_version=${encodeURIComponent(server.game_version || '')}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
          optimizationChecked = true;
          loadInstalledMods();
        } else {
          alert(data.detail || 'Optimization install failed.');
          optimizationChecked = false;
        }
      } catch (err) {
        alert('Optimization install failed — check console.');
        optimizationChecked = false;
      }
      isTogglingOpt = false;
    } else {
      try {
        await fetch(`/api/servers/${server.id}/remove-mods-by-slug`, {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ slugs: ['lithium', 'ferrite-core', 'krypton', 'servercore'] })
        });
        optimizationChecked = false;
        loadInstalledMods();
      } catch (err) { optimizationChecked = true; }
    }
  }

  //==========================================
  //FACTORIO MOD PORTAL FUNCTIONS
  //==========================================

  let factorioSearchResults = $state([]);
  let factorioSearchQuery = $state("");

  async function searchFactorioMods(e) {
    e.preventDefault();
    if (!factorioSearchQuery.trim()) return;
    isSearching = true;
    try {
        const res = await fetch(`/api/factorio/mods/search?query=${encodeURIComponent(factorioSearchQuery)}&limit=15&factorio_version=${encodeURIComponent(server.game_version || '')}`);
        if (!res.ok) throw new Error(`HTTP Error: ${res.status}`);
        const data = await res.json();
        factorioSearchResults = data.results || [];
    } catch (err) {
        console.error("Factorio search failed:", err);
        alert("Failed to search Factorio mod portal.");
    } finally {
        isSearching = false;
    }
  }

  async function installFactorioMod(mod) {
      try {
          const installRes = await fetch(`/api/servers/${server.id}/factorio/mods/install`, {
              method: 'POST',
              credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                  download_url: mod.download_url,
                  filename: mod.file_name
              })
          });
          if (installRes.ok) {
              alert(`${mod.file_name} installed! Restart server to apply.`);
              loadInstalledMods();
          } else {
              const err = await installRes.text();
              alert(`Install failed: ${err}`);
          }
      } catch (err) {
          console.error("Factorio install failed:", err);
          alert("An error occurred during installation.");
      }
  }

  //==========================================
  //BACKUP MANAGER STATES & FUNCTIONS
  //==========================================
  
  let backupList = $state([]);
  let isBackingUp = $state(false);
  let isRestoring = $state(false);

  //Reload Backup List when tab opens
  $effect(() => {
      if (activeTab === 'backups') {
          fetchBackups();
      }
  });

  async function fetchBackups() {
      try {
          const res = await fetch(`/api/servers/${server.id}/backups`, { credentials: 'same-origin' });
          if (res.ok) {
              const data = await res.json();
              backupList = data.backups || [];
          }
      } catch (err) {
          console.error("Failed to fetch backups:", err);
      }
  }

  async function handleManualBackup() {
      isBackingUp = true;
      try {
          const res = await fetch(`/api/servers/${server.id}/backup`, {
              method: 'POST',
              credentials: 'same-origin'
          });
          const data = await res.json();
          if (res.ok) {
              alert(data.message);
              fetchBackups();
          } else {
              alert(`Error: ${data.detail}`);
          }
      } catch (err) {
          console.error("Backup failed:", err);
      } finally {
          isBackingUp = false;
      }
  }

  async function handleRestoreBackup(filename, type) {
      if (!confirm(`WARNING: This will power down ${server.id}, overwrite all current data with ${filename}, and restart the server. Continue?`)) return;
      
      isRestoring = true;
      try {
          const res = await fetch(`/api/servers/${server.id}/backups/${encodeURIComponent(filename)}/restore`, {
              method: 'POST',
              credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ backup_type: type })
          });
          
          //Read it as raw text first so Svelte doesn't crash on HTML!
          const rawText = await res.text();
          let data;
          
          try {
              data = JSON.parse(rawText);
          } catch (e) {
              alert(`Fatal Server Crash! The backend did not return JSON. Raw response:\n\n${rawText.substring(0, 150)}...`);
              return;
          }
          
          if (res.ok) {
              alert("Server successfully restored and restarted!");
              setTimeout(refreshServer, 1000);
          } else {
              alert(`Restore failed: ${typeof data.detail === 'object' ? JSON.stringify(data.detail) : data.detail}`);
          }
      } catch (err) {
          console.error("Restore failed:", err);
          alert("Network error: Could not connect to the backend.");
      } finally {
          isRestoring = false;
      }
  }

  async function handleDeleteBackup(filename, type) {
      if (!confirm(`Delete backup ${filename}? This cannot be undone.`)) return;
      try {
          //THE FIX: URL Encode both parameters here too
          const res = await fetch(`/api/servers/${server.id}/backups/${encodeURIComponent(filename)}?backup_type=${encodeURIComponent(type)}`, {
              method: 'DELETE',
              credentials: 'same-origin'
          });
          if (res.ok) fetchBackups();
      } catch (err) {
          console.error("Delete failed:", err);
      }
  }

  //==========================================
  //SETTINGS MANAGER STATES & FUNCTIONS
  //==========================================

  //Fetch settings when tab opens, file changes, or server changes (navigation).
  //data.server.id is reactive → refetch on server switch. untrack inside
  //fetchSettings prevents the 5s poll from re-triggering.
  $effect(() => {
      if (activeTab === 'settings') {
          data.server.id; // reactive dep: refetch on navigation
          fetchSettings();
      }
  });

  let settingsContent = $state("");
  let currentFile = $state("");
  let currentFilename = $state("");
  let availableFiles = $state([]);
  let isSavingSettings = $state(false);
  let settingsSupported = $state(true);
  let settingsMode = $state('auto'); // 'auto' (smart-gui) or 'raw'
  let factorioPresets = $state([]);
  let selectedPreset = $state('');
  let isApplyingPreset = $state(false);

  async function fetchSettings() {
      try {
          const sid = untrack(() => server.id);
          //If we have a file selected from the dropdown, append it to the URL
          const url = currentFile
              ? `/api/servers/${sid}/settings?file_key=${encodeURIComponent(currentFile)}`
              : `/api/servers/${sid}/settings`;

          const res = await fetch(url, { credentials: 'same-origin' });
          const data = await res.json();

          if (data.status === 'unsupported') {
              settingsSupported = false;
          } else {
              settingsSupported = true;
              settingsContent = data.content;
              availableFiles = data.available_files || [];
              if (data.current_file) currentFile = data.current_file;
              if (data.current_filename) currentFilename = data.current_filename;
              //Load presets when editing map-gen-settings for a Factorio server
              if (currentFilename.includes('map-gen-settings') && factorioPresets.length === 0) {
                  try {
                      const pres = await fetch('/api/factorio/map-presets');
                      if (pres.ok) factorioPresets = await pres.json();
                  } catch {}
              }
          }
      } catch (err) {
          console.error("Failed to fetch settings:", err);
      }
  }

  async function applyPreset() {
      if (!selectedPreset) return;
      isApplyingPreset = true;
      try {
          const sid = untrack(() => server.id);
          const res = await fetch(`/api/servers/${sid}/factorio/apply-preset`, {
              method: 'POST',
              credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ preset: selectedPreset })
          });
          if (res.ok) {
              await fetchSettings();
          } else {
              const err = await res.text();
              alert(`Failed: ${err}`);
          }
      } catch (err) {
          alert('Error applying preset.');
      } finally {
          isApplyingPreset = false;
      }
  }

  let isRegenerating = $state(false);
  async function regenerateMap() {
      const sid = untrack(() => server.id);
      if (!confirm('Delete all existing saves and generate a fresh map on next restart? This cannot be undone.')) return;
      isRegenerating = true;
      try {
          const res = await fetch(`/api/servers/${sid}/factorio/regenerate-map`, {
              method: 'POST', credentials: 'same-origin'
          });
          const d = await res.json();
          if (res.ok) {
              alert(d.message);
              await handleAction('restart');
          } else {
              alert(d.detail || 'Failed');
          }
      } catch (e) {
          alert('Error: ' + String(e));
      } finally {
          isRegenerating = false;
      }
  }

  async function saveSettings() {
      isSavingSettings = true;
      try {
          const sid = untrack(() => server.id);
          const res = await fetch(`/api/servers/${sid}/settings?file_key=${encodeURIComponent(currentFile)}`, {
              method: 'POST',
              credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ content: settingsContent })
          });
          const data = await res.json();
          if (res.ok) {
              alert(data.message);
              setTimeout(refreshServer, 1000);
          } else {
              alert(`Error: ${typeof data.detail === 'object' ? JSON.stringify(data.detail) : data.detail}`);
          }
      } catch (err) {
          console.error("Failed to save settings:", err);
      } finally {
          isSavingSettings = false;
      }
  }

  //==========================================
  //SMART-GUI SETTINGS PARSER
  //==========================================

  //Player-list files: JSON arrays of {uuid, name} (+ level/bypassesPlayerLimit for ops).
  const PLAYER_LIST_FILES = ['whitelist.json', 'banned-players.json', 'ops.json'];

  //Known enum fields: key's last path segment → valid values.
  const ENUM_FIELDS = {
      'allow_commands': ['true', 'false', 'admins-only']
  };

  //Ore percentage presets (display values; raw = preset/100).
  const ORE_PRESETS = [25, 33, 50, 75, 100, 150, 200, 250, 300, 350, 400, 450, 500];

  function isPlayerList(filename) {
      return PLAYER_LIST_FILES.includes(filename?.toLowerCase());
  }

  //Editable player list state
  let playerList = $state([]);
  let newPlayerName = $state("");
  let isAddingPlayer = $state(false);
  let playerAddError = $state("");

  //Detect format. Returns 'player-list' | 'json' | 'properties' | null.
  function detectFormat(content, filename) {
      const fname = filename?.toLowerCase() || '';
      if (isPlayerList(fname)) return 'player-list';
      const trimmed = content.trim();
      //JSON: starts with { or [
      if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
          try { JSON.parse(trimmed); return 'json'; } catch {}
      }
      //Properties/INI: check by extension or scan for key=value lines
      if (fname.endsWith('.properties') || fname.endsWith('.ini')) return 'properties';
      //Content heuristic: scan all lines, not just the first (comments and
      //blank lines lead server.properties).
      const lines = trimmed.split('\n');
      let kvCount = 0;
      for (const line of lines) {
          const t = line.trim();
          if (!t || t.startsWith('#') || t.startsWith('//')) continue;
          if (/^[^\s=]+=/.test(t)) kvCount++;
      }
      if (kvCount > 0) return 'properties';
      return null;
  }

  //Parse content into flat key/value pairs for the form editor.
  function parseToFields(content, format) {
      if (format === 'json') {
          try {
              const obj = JSON.parse(content);
              return flattenJson(obj);
          } catch { return []; }
      }
      if (format === 'properties') {
          return content.split('\n').map((line, i) => {
              const t = line.trim();
              if (!t || t.startsWith('#') || t.startsWith('//')) return null;
              const eq = t.indexOf('=');
              if (eq < 0) return null;
              return { key: t.slice(0, eq).trim(), value: t.slice(eq + 1).trim(), path: i };
          }).filter(Boolean);
      }
      return [];
  }

  //Flatten nested JSON into dot-path entries: {key, value, section}.
  //A: skips _comment_* keys, pairs each as a description on the following field.
  //B: derives section from first path segment for collapsible grouping.
  function flattenJson(obj, prefix = '', comments = {}) {
      let out = [];
      let pendingComment = null;
      for (const [k, v] of Object.entries(obj)) {
          //Factorio uses both _comment_field (prefix) and _field_comment (suffix).
          if (k === '_comment' || k.startsWith('_comment') || k.endsWith('_comment')) {
              if (Array.isArray(v)) pendingComment = v.join(' ');
              else if (typeof v === 'string') pendingComment = v;
              continue;
          }
          const path = prefix ? `${prefix}.${k}` : k;
          const section = path.includes('.') ? path.split('.')[0] : '';
          if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
              out = out.concat(flattenJson(v, path));
          } else {
              out.push({
                  key: path,
                  value: v,
                  raw: Array.isArray(v) ? JSON.stringify(v) : String(v),
                  section,
                  description: pendingComment || ''
              });
              pendingComment = null;
          }
      }
      return out;
  }

  //Detect field type: 'enum' | 'boolean' | 'number' | 'ore-resource' | 'json-array' | 'string'.
  function fieldType(field) {
      const lastSeg = field.key.split('.').pop();
      if (ENUM_FIELDS[lastSeg]) return 'enum';
      if (typeof field.value === 'boolean') return 'boolean';
      //Ore resource: lives under autoplace_controls, has frequency/size/richness.
      if (field.key.startsWith('autoplace_controls.') &&
          ['frequency', 'size', 'richness'].includes(lastSeg)) return 'ore-resource';
      if (typeof field.value === 'number') return 'number';
      if (Array.isArray(field.value)) return 'json-array';
      if (typeof field.value === 'string') {
          if (field.value !== '' && !isNaN(Number(field.value))) return 'number';
          return 'string';
      }
      return 'string';
  }

  //The editable field state (rebuilt when settingsContent changes in smart mode)
  let settingsFields = $state([]);

  //Sync settingsContent → settingsFields / playerList when in smart mode
  $effect(() => {
      if (settingsMode !== 'auto') return;
      const fname = currentFilename?.toLowerCase() || '';
      const format = detectFormat(settingsContent, fname);

      if (format === 'player-list') {
          try {
              const arr = JSON.parse(settingsContent);
              playerList = arr.map(p => ({ ...p }));
          } catch { playerList = []; }
          settingsFields = [];
          return;
      }
      playerList = [];

      if (format === 'json' || format === 'properties') {
          settingsFields = parseToFields(settingsContent, format).map(f => {
              const type = fieldType(f);
              //Ore fields display as percentage (raw × 100); everything else
              //edits the raw value as a string.
              const edited = type === 'ore-resource'
                  ? String(Math.round(Number(f.raw ?? f.value) * 100))
                  : (f.raw ?? String(f.value));
              return { ...f, type, _edited: edited };
          });
      } else {
          settingsFields = [];
      }
  });

  //Serialize settingsFields / playerList back into settingsContent before saving
  function syncFieldsToContent() {
      const fname = currentFilename?.toLowerCase() || '';
      const format = detectFormat(settingsContent, fname);
      if (!format) return;

      if (format === 'player-list') {
          settingsContent = JSON.stringify(playerList, null, 2);
          return;
      }

      if (format === 'json') {
          const obj = {};
          for (const f of settingsFields) {
              let val;
              if (f.type === 'enum') val = f._edited;
              else if (f.type === 'boolean') val = f._edited === 'true' || f._edited === true;
              else if (f.type === 'ore-resource') val = Number(f._edited) / 100;
              else if (f.type === 'number') val = Number(f._edited);
              else if (f.type === 'json-array') { try { val = JSON.parse(f._edited); } catch { val = f._edited; } }
              else val = f._edited;
              setNestedPath(obj, f.key, val);
          }
          settingsContent = JSON.stringify(obj, null, 2);
      } else if (format === 'properties') {
          settingsContent = settingsFields.map(f => `${f.key}=${f._edited}`).join('\n') + '\n';
      }
  }

  //Lookup UUID via backend proxy to Mojang API, then add to list
  async function addPlayer() {
      const name = newPlayerName.trim();
      if (!name) return;
      isAddingPlayer = true;
      playerAddError = "";
      try {
          const res = await fetch(`/api/minecraft/uuid/${encodeURIComponent(name)}`, { credentials: 'same-origin' });
          const data = await res.json();
          if (data.status !== 'success') {
              playerAddError = data.message || "Lookup failed.";
              return;
          }
          //Prevent duplicate UUIDs
          if (playerList.some(p => p.uuid === data.uuid)) {
              playerAddError = `${data.name} is already in the list.`;
              return;
          }
          const entry = { uuid: data.uuid, name: data.name };
          const fname = currentFilename?.toLowerCase() || '';
          if (fname === 'ops.json') {
              entry.level = 4;
              entry.bypassesPlayerLimit = false;
          }
          playerList = [...playerList, entry];
          newPlayerName = "";
          syncFieldsToContent();
      } catch (err) {
          playerAddError = "Network error during lookup.";
      } finally {
          isAddingPlayer = false;
      }
  }

  function removePlayer(uuid) {
      playerList = playerList.filter(p => p.uuid !== uuid);
      syncFieldsToContent();
  }

  //Group flat fields into sections by their first dot-path segment.
  //Fields with no section (top-level keys) go in the first group.
  function groupBySection(fields) {
      const groups = [];
      let current = { section: '', fields: [] };
      for (const f of fields) {
          if (f.section !== current.section) {
              if (current.fields.length) groups.push(current);
              current = { section: f.section, fields: [] };
          }
          current.fields.push(f);
      }
      if (current.fields.length) groups.push(current);
      return groups;
  }

  //Group autoplace_controls.* fields into rows by resource name.
  //Each row: { resource, attrs: { frequency, size, richness } }
  //The cell objects are the same live field refs so edits flow back.
  function buildOreTable(fields) {
      const rows = {};
      const order = ['frequency', 'size', 'richness'];
      for (const f of fields) {
          const parts = f.key.split('.'); //autoplace_controls.coal.frequency
          const resource = parts[1];
          if (!rows[resource]) rows[resource] = { resource, attrs: {} };
          rows[resource].attrs[parts[2]] = f;
      }
      return Object.values(rows);
  }

  function setNestedPath(obj, path, value) {
      const parts = path.split('.');
      let cur = obj;
      for (let i = 0; i < parts.length - 1; i++) {
          if (!cur[parts[i]]) cur[parts[i]] = {};
          cur = cur[parts[i]];
      }
      cur[parts[parts.length - 1]] = value;
  }

  //Wrapper that syncs fields → content before POSTing
  async function saveSettingsSmart() {
      if (settingsMode === 'auto' && settingsFields.length > 0) {
          syncFieldsToContent();
      }
      await saveSettings();
  }

  //1. HARDWARE STATES (Fail-Safe Defaults)
  let hardwareData = $state({});
    
  //The Buffer MUST have guaranteed string defaults so Svelte never binds to null
  let editBuffer = $state({ 
      ram_limit: "", 
      game_version: "", //Swapped cf_modpack for game_version
      description: "",
      game_type: "" 
  });
    
  let isEditing = $state(false);
  let isRebuilding = $state(false);
  let isDeleting = $state(false);

  //Version dropdown data for settings tab
  let availableVersions = $state([]);

  //2. LIFECYCLE
  $effect(() => {
      if (activeTab === 'hardware') {
          fetchHardware();
      }
      if (activeTab === 'settings') {
          loadAvailableVersions();
      }
  });

  //3. FETCH LOGIC
  async function loadAvailableVersions() {
    if (availableVersions.length > 0) return;
    const gt = (hardwareData?.game_type || server.game_type || '').toLowerCase();
    try {
      const endpoint = gt === 'factorio' ? '/api/factorio/versions' : '/api/minecraft/versions';
      const res = await fetch(endpoint);
      if (res.ok) {
        const data = await res.json();
        if (gt === 'factorio') {
          const stable = data.stable ? [`Stable (${data.stable})|stable`] : [];
          const exp = data.experimental ? [`Experimental (${data.experimental})|latest`] : [];
          const rest = (data.all_versions || []).map(v => `${v}|${v}`);
          availableVersions = [...stable, ...exp, ...rest];
        } else {
          availableVersions = (data.versions || []).map(v => `${v}|${v}`);
        }
      }
    } catch (e) {}
  }
  async function fetchHardware() {
      try {
          const res = await fetch(`/api/servers/${server.id}/hardware`, { credentials: 'same-origin' });
            
          if (!res.ok) {
              console.error("API Error:", res.status);
              return; 
          }

          const data = await res.json();
            
          if (!data || typeof data !== 'object') {
              console.error("Invalid API Response");
              return;
          }

          //Update Master Record
          hardwareData = data; 
            
          //THE SMART MERGE: Only update buffer if user isn't typing.
          //We use the Nullish Coalescing Operator (??) to ensure we NEVER set a field to null.
          if (!isEditing) {
              editBuffer = {
                  ram_limit: data.ram_limit ?? "",
                  game_version: data.game_version ?? "", //Swapped cf_modpack for game_version
                  description: data.description ?? "",
                  game_type: data.game_type ?? ""
              };
          }
      } catch (err) {
          console.error("Fetch failed:", err);
      }
  }

  //4. SAVE LOGIC
  async function saveHardware() {
      if (!confirm("This will destroy and rebuild the container. Continue?")) return;
        
      isRebuilding = true;
        
      //Build the payload cleanly
      const payload = {
          ram_limit: editBuffer.ram_limit,
          game_version: editBuffer.game_version, //Swapped cf_modpack for game_version
          description: editBuffer.description
      };

      try {
          const res = await fetch(`/api/servers/${server.id}/hardware`, {
              method: 'POST',
              credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
          });
            
          const data = await res.json();
            
          if (res.ok) {
              isEditing = false; //Release the lock
              alert(data.message || "Settings Applied!");
              setTimeout(refreshServer, 2000);
          } else {
              alert(`Error: ${data.detail || 'Unknown error'}`);
          }
      } catch (err) {
          alert("Network error while saving.");
          console.error(err);
      } finally {
          isRebuilding = false;
      }
  }

  async function resetHardware() {
      if (!confirm("This will delete all custom hardware settings and rebuild the container using the base recipe defaults. Continue?")) return;
      
      isRebuilding = true;
      try {
          const res = await fetch(`/api/servers/${server.id}/hardware`, {
              method: 'DELETE',
              credentials: 'same-origin'
          });
          
          const data = await res.json();
          if (res.ok) {
              isEditing = false;
              alert(data.message || "Reset successful!");
              setTimeout(refreshServer, 2000);
          } else {
              alert(`Error: ${data.detail || 'Unknown error'}`);
          }
      } catch (err) {
          alert("Network error while resetting.");
          console.error(err);
      } finally {
          isRebuilding = false;
      }
  }
</script>

<div class="max-w-7xl mx-auto space-y-6">
  
  <header class="flex flex-col md:flex-row items-start justify-between md:items-center gap-4 mb-6">
    <div>
      <div class="flex items-center gap-3">
        <h1 class="text-3xl font-bold tracking-tight text-gray-100 capitalize">
          {server.id.replace(/[-_]/g, ' ')}
        </h1>
        <span class="px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider rounded-full 
          {['running', 'restarting'].includes(server.status) 
            ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' 
            : 'bg-red-500/20 text-red-400 border border-red-500/30'}">
          {server.status}
        </span>
      </div>
      <div class="text-sm text-gray-500 mt-2 font-mono flex gap-4">
        <span>ID: {server.id}</span>
        <span>Port: Dynamic</span>
        <span>Uptime: {server.uptime || 'Offline'}</span>
        <span class="italic">{server.description || ''}</span>
      </div>
    </div>

    <div class="flex flex-wrap gap-2 w-full md:w-auto">
      {#if ['stopped', 'exited', 'created', 'dead', 'loading'].includes(server.status)}
        <button 
          onclick={() => handleAction('start')} 
          disabled={isStarting || isStopping || isRestarting || isRedeploying}
          class="px-6 py-2 bg-[#d97706] hover:bg-[#b45309] text-white font-medium rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
          {isStarting ? 'Starting...' : 'Start'}
        </button>
        
        {#if role === 'admin'}
          <button 
            onclick={handleRedeploy}
            disabled={isStarting || isStopping || isRestarting || isRedeploying}
            class="px-6 py-2 bg-red-950 hover:bg-red-900 text-red-200 border border-red-800 font-medium rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
            {isRedeploying ? 'Rebuilding...' : 'Force Rebuild'}
          </button>
          {#if server._user_created}
            <button
              onclick={handleDelete}
              disabled={isDeleting}
              class="px-6 py-2 bg-red-900 hover:bg-red-800 text-red-100 border border-red-700 font-medium rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
              {isDeleting ? 'Removing...' : 'Delete'}
            </button>
          {/if}
        {/if}

      {:else}
        {#if role === 'admin'}
        <button 
          onclick={() => handleAction('restart')} 
          disabled={isStarting || isStopping || isRestarting || isRedeploying}
          class="px-6 py-2 bg-[#d97706] hover:bg-[#b45309] text-white font-medium rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
          {isRestarting ? 'Restarting...' : 'Restart'}
        </button>
        <button 
          onclick={() => handleAction('stop')} 
          disabled={isStarting || isStopping || isRestarting || isRedeploying}
          class="px-6 py-2 bg-[#dc2626] hover:bg-[#b91c1c] text-white font-medium rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
          {isStopping ? 'Stopping...' : 'Stop'}
        </button>
        {:else}
          <button disabled class="px-6 py-2 bg-[#d97706] hover:bg-[#b45309] text-white font-medium rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"> Off (Admin) </button>
          <button disabled class="px-6 py-2 bg-[#dc2626] hover:bg-[#b91c1c] text-white font-medium rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"> Restart (Admin) </button>
        {/if}
      {/if}
    </div> 
  </header>

  <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
    
    <div class="bg-[#1f2937] border border-gray-700 rounded-lg p-5">
      <div class="text-gray-400 text-sm font-medium mb-3">Instance Memory</div>
      <div class="flex justify-between items-end mb-3">
        <div class="text-3xl font-bold text-gray-100">
          {server.ram_used} <span class="text-gray-500 text-xl font-normal">/ {server.ram_allocated} GB</span>
        </div>
        <div class="text-xs text-gray-400 font-mono mb-1">{ramPercentage}% Used</div>
      </div>
      <div class="w-full bg-gray-800 rounded-full h-2">
        <div class="bg-[#10b981] h-2 rounded-full transition-all duration-500" style="width: {Math.min(ramPercentage, 100)}%"></div>
      </div>
    </div>

    <div class="bg-[#1f2937] border border-gray-700 rounded-lg p-5">
      <div class="text-gray-400 text-sm font-medium mb-3">Instance CPU Load</div>
      <div class="text-3xl font-bold text-gray-100 mb-4">{server.cpu_load}%</div>
      <div class="w-full bg-gray-800 rounded-full h-2">
        <div class="bg-[#3b82f6] h-2 rounded-full transition-all duration-500" style="width: {Math.min(server.cpu_load, 100)}%"></div>
      </div>
    </div>

  </div>

  <div class="flex overflow-x-auto whitespace-nowrap scrollbar-hide border-b border-gray-700 mb-6 pb-2 gap-x-6">
    <button 
      onclick={() => activeTab = 'console'}
      class="flex-shrink-0 pb-2 font-medium transition-colors text-sm uppercase tracking-wider {activeTab === 'console' ? 'text-gray-100 border-b-2 border-gray-300' : 'text-gray-500 hover:text-gray-300'}">
      Live Console
    </button>
   
    <button 
      onclick={() => activeTab = 'backups'}
      class="flex-shrink-0 pb-2 font-medium transition-colors text-sm uppercase tracking-wider {activeTab === 'backups' ? 'text-gray-100 border-b-2 border-gray-300' : 'text-gray-500 hover:text-gray-300'}">
      Backups
    </button>

    {#if role === 'admin'}
    <button 
      onclick={() => activeTab = 'settings'}
      class="flex-shrink-0 pb-2 font-medium transition-colors text-sm uppercase tracking-wider {activeTab === 'settings' ? 'text-gray-100 border-b-2 border-gray-300' : 'text-gray-500 hover:text-gray-300'}">
      Settings
    </button>

    <button 
      onclick={() => activeTab = 'hardware'}
      class="flex-shrink-0 pb-2 font-medium transition-colors text-sm uppercase tracking-wider {activeTab === 'hardware' ? 'text-gray-100 border-b-2 border-gray-300' : 'text-gray-500 hover:text-gray-300'}">
      Hardware
    </button>
    {/if}

    {#if server.game_type === 'minecraft' || server.game_type === 'factorio' || (server._user_created && (server.id?.includes('minecraft') || server.id?.includes('factorio')))}
      <button 
        onclick={() => activeTab = 'mods'}
        class="flex-shrink-0 pb-2 font-medium transition-colors text-sm uppercase tracking-wider {activeTab === 'mods' ? 'text-gray-100 border-b-2 border-gray-300' : 'text-gray-500 hover:text-gray-300'}">
        Mod Manager
      </button>
    {/if}
  </div>

  <style>
    /* Hide scrollbar for Chrome/Safari/Edge */
    .scrollbar-hide::-webkit-scrollbar { display: none; }
    .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
  </style>

  {#if activeTab === 'console'}
    <div class="bg-[#0b0f19] border border-gray-700 rounded-lg shadow-inner flex flex-col overflow-hidden relative">
      {#if isPaused}
        <button onclick={() => { isPaused = false; if (terminalDiv) terminalDiv.scrollTop = terminalDiv.scrollHeight; }}
          class="absolute top-2 right-2 z-10 px-3 py-1 bg-amber-600 hover:bg-amber-500 text-white text-xs font-medium rounded shadow-lg flex items-center gap-1">
          ⏸ Paused — Jump to Latest ↓
        </button>
      {/if}
      <div bind:this={terminalDiv}
           onscroll={(e) => {
             const el = e.currentTarget;
             const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
             isPaused = !atBottom;
           }}
           class="p-4 font-mono text-[13px] h-[300px] overflow-y-auto overflow-x-auto text-gray-300 whitespace-pre-wrap break-all leading-relaxed">
        {#if logs.length === 0}
          <div class="text-gray-600 italic">Waiting for container output...</div>
        {/if}
        {#each logs as line}
          <div>{line}</div>
        {/each}
      </div>
      
      {#if role === 'admin'}
      <form onsubmit={handleCommandSubmit} class="border-t border-gray-800 bg-[#111827] p-2 flex gap-2">
        <span class="text-gray-500 font-mono py-2 pl-2">&gt;</span>
        <input 
          type="text" 
          bind:value={commandInput} 
          disabled={!['running', 'restarting'].includes(server.status) || isSendingCommand} 
          placeholder={['running', 'restarting'].includes(server.status) ? "Enter server command..." : "Server offline..."} 
          class="flex-1 bg-transparent text-gray-200 font-mono text-sm focus:outline-none placeholder-gray-600" 
        />
        <button 
          type="submit" 
          disabled={!commandInput.trim() || !['running', 'restarting'].includes(server.status)} 
          class="px-4 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-200 text-sm font-medium rounded transition-colors disabled:opacity-50">
          Send
        </button>
      </form>
      {:else}
      <form onsubmit={handleCommandSubmit} class="border-t border-gray-800 bg-[#111827] p-2 flex gap-2">
        <span class="text-gray-500 font-mono py-2 pl-2">&gt;</span>
        <input 
          type="text" 
          bind:value={commandInput} 
          disabled 
          placeholder="Only for admins" 
          class="flex-1 bg-transparent text-gray-200 font-mono text-sm focus:outline-none placeholder-gray-600" 
        />
        <button 
          type="submit" 
          disabled 
          class="px-4 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-200 text-sm font-medium rounded transition-colors disabled:opacity-50">
          Send
        </button>
      </form>
      {/if}
    </div>
  {/if}

  {#if activeTab === 'settings'}
    <div class="bg-[#1f2937] p-5 rounded-lg border border-gray-700">
      <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3 mb-4">
        <h3 class="font-bold text-gray-200">Configuration Editor</h3>
        <div class="flex items-center gap-4">
          {#if role === 'admin' && settingsSupported && settingsFields.length > 0}
            <div class="flex bg-[#111827] rounded border border-gray-700 overflow-hidden">
              <button onclick={() => settingsMode = 'auto'}
                class="px-3 py-1.5 text-xs font-medium uppercase tracking-wider transition-colors {settingsMode === 'auto' ? 'bg-emerald-600 text-white' : 'text-gray-400 hover:text-gray-200'}">
                Smart
              </button>
              <button onclick={() => settingsMode = 'raw'}
                class="px-3 py-1.5 text-xs font-medium uppercase tracking-wider transition-colors {settingsMode === 'raw' ? 'bg-emerald-600 text-white' : 'text-gray-400 hover:text-gray-200'}">
                Raw
              </button>
            </div>
          {/if}
          {#if role === 'admin' && settingsSupported}
            <button 
            onclick={saveSettingsSmart} 
            disabled={isSavingSettings}
            class="bg-[#10b981] hover:bg-[#059669] text-white px-6 py-2 rounded font-medium disabled:opacity-50 transition-colors">
            {isSavingSettings ? 'Saving & Restarting...' : 'Save Changes'}
          </button>
          {/if}
        </div>
      </div>

      {#if !settingsSupported}
        <div class="p-8 text-center text-gray-500 border border-gray-700 rounded bg-[#111827] italic">
          This server does not support dashboard configuration editing.
        </div>
      {:else}
      
        {#if availableFiles.length > 1}
          <div class="mb-4 flex items-center gap-3 bg-[#111827] p-3 rounded border border-gray-700">
            <label class="text-xs font-bold text-gray-400 uppercase tracking-wider">Select Config File:</label>
            <select 
              bind:value={currentFile} 
              onchange={fetchSettings}
              class="flex-1 bg-[#0b0f19] border border-gray-600 text-gray-300 rounded px-3 py-1.5 focus:outline-none focus:border-emerald-500 font-mono text-sm"
            >
              {#each availableFiles as file}
                <option value={file}>{file}</option>
              {/each}
            </select>
          </div>
        {/if}

        {#if settingsMode === 'auto' && (settingsFields.length > 0 || isPlayerList(currentFilename))}
          {#if detectFormat(settingsContent, currentFilename) === 'player-list'}
            <!--Smart GUI: player list editor (whitelist / ops / banned-players) -->
            <div class="space-y-2">
              {#if role === 'admin'}
                <div class="flex items-center gap-2 bg-[#111827] p-3 rounded border border-gray-700 mb-3">
                  <input type="text" bind:value={newPlayerName}
                    onkeydown={(e) => { if (e.key === 'Enter' && !isAddingPlayer) addPlayer(); }}
                    placeholder="Enter Minecraft username..."
                    class="flex-1 bg-[#0b0f19] border border-gray-600 text-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:border-emerald-500"
                    disabled={isAddingPlayer} />
                  <button onclick={addPlayer} disabled={isAddingPlayer || !newPlayerName.trim()}
                    class="bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded text-sm font-medium disabled:opacity-50 transition-colors whitespace-nowrap">
                    {isAddingPlayer ? 'Looking up...' : 'Add Player'}
                  </button>
                </div>
                {#if playerAddError}
                  <div class="text-sm text-red-400 bg-red-950/40 border border-red-800/50 rounded px-3 py-2 mb-3">
                    {playerAddError}
                  </div>
                {/if}
              {/if}

              {#if playerList.length === 0}
                <div class="text-center text-gray-500 italic py-8">
                  No players in this list.
                </div>
              {:else}
                {#each playerList as player, i}
                  <div class="flex items-center gap-3 bg-[#111827] p-2.5 rounded border border-gray-700/50">
                    <div class="flex-1">
                      <span class="text-sm font-medium text-gray-200">{player.name}</span>
                      <span class="text-xs text-gray-500 ml-2 font-mono">{player.uuid}</span>
                      {#if player.level !== undefined}
                        <span class="text-xs text-emerald-400 ml-2">Level {player.level}</span>
                      {/if}
                    </div>
                    {#if role === 'admin'}
                      <button onclick={() => removePlayer(player.uuid)}
                        class="text-red-400 hover:text-red-300 text-sm px-2 py-1 rounded hover:bg-red-950/40 transition-colors">
                        Remove
                      </button>
                    {/if}
                  </div>
                {/each}
              {/if}
            </div>
          {:else}
            <!--Map-gen preset picker (Factorio only, map-gen-settings.json) -->
            {#if currentFilename.includes('map-gen-settings') && factorioPresets.length > 0}
              <div class="mb-4 flex flex-col sm:flex-row sm:items-center gap-2 bg-[#111827] p-3 rounded border border-gray-700">
                <label class="text-xs font-bold text-gray-400 uppercase tracking-wider flex-shrink-0">Map Gen Preset:</label>
                <select bind:value={selectedPreset}
                  class="flex-1 bg-[#0b0f19] border border-gray-600 text-gray-300 rounded px-3 py-1.5 focus:outline-none focus:border-emerald-500 text-sm">
                  <option value="">— Custom (no preset) —</option>
                  {#each factorioPresets as preset}
                    <option value={preset.id}>{preset.label}</option>
                  {/each}
                </select>
                {#if role === 'admin'}
                  <button onclick={applyPreset} disabled={!selectedPreset || isApplyingPreset}
                    class="bg-[#d97706] hover:bg-[#b45309] text-white px-4 py-1.5 rounded text-sm font-medium disabled:opacity-50 whitespace-nowrap">
                    {isApplyingPreset ? '...' : 'Apply'}
                  </button>
                  <button onclick={regenerateMap} disabled={isRegenerating}
                    class="bg-red-700 hover:bg-red-600 text-white px-4 py-1.5 rounded text-sm font-medium disabled:opacity-50 whitespace-nowrap">
                    {isRegenerating ? '...' : 'Regenerate Map'}
                  </button>
                {/if}
              </div>
            {/if}
            <!--Smart GUI: sectioned key/value form editor -->
            {@const grouped = groupBySection(settingsFields)}
            <div class="space-y-3 max-h-[500px] overflow-y-auto custom-scrollbar pr-2">
              {#each grouped as group}
                {#if group.section === 'autoplace_controls'}
                  <!--Ore resource table: rows=resources, cols=frequency/size/richness -->
                  {@const oreRows = buildOreTable(group.fields)}
                  <div class="text-xs font-bold text-gray-500 uppercase tracking-wider pt-2 pb-1 border-b border-gray-700/50">
                    autoplace_controls
                  </div>
                  <div class="overflow-x-auto">
                    <datalist id="ore-ticks">
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
                        {#each oreRows as row}
                          <tr class="border-b border-gray-800/50">
                            <td class="py-1.5 pr-2 font-mono text-gray-300 whitespace-nowrap">{row.resource}</td>
                            {#each ['frequency', 'size', 'richness'] as attr}
                              {@const cell = row.attrs[attr]}
                              <td class="py-1.5 px-1 text-center">
                                {#if cell}
                                  <div class="flex items-center gap-1 justify-center">
                                    <input type="range" min="25" max="500" step="1"
                                      value={cell._edited}
                                      oninput={(e) => cell._edited = e.currentTarget.value}
                                      class="w-16 accent-emerald-500"
                                      list="ore-ticks"
                                      disabled={role !== 'admin'} />
                                    <input type="number" min="25" max="500" step="1"
                                      value={cell._edited}
                                      oninput={(e) => cell._edited = e.currentTarget.value}
                                      class="w-14 bg-[#0b0f19] border border-gray-600 text-gray-300 rounded px-1.5 py-1 text-xs focus:outline-none focus:border-emerald-500"
                                      disabled={role !== 'admin'} />
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
                {:else}
                  {#if group.section}
                    <div class="text-xs font-bold text-gray-500 uppercase tracking-wider pt-2 pb-1 border-b border-gray-700/50">
                      {group.section}
                    </div>
                  {/if}
                  {#each group.fields as field}
                    <div class="bg-[#111827] p-2.5 rounded border border-gray-700/50">
                      {#if field.description}
                        <div class="text-[10px] text-gray-500 mb-1 leading-tight">{field.description}</div>
                      {/if}
                      <div class="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3">
                        <label class="text-xs font-mono text-gray-400 sm:w-1/3 break-all flex-shrink-0">{field.key.split('.').pop()}</label>
                        <div class="flex-1">
                          {#if field.type === 'enum'}
                            <select bind:value={field._edited}
                              class="w-full bg-[#0b0f19] border border-gray-600 text-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:border-emerald-500"
                              disabled={role !== 'admin'}>
                              {#each ENUM_FIELDS[field.key.split('.').pop()] as opt}
                                <option value={opt}>{opt}</option>
                              {/each}
                            </select>
                          {:else if field.type === 'boolean'}
                            <select bind:value={field._edited}
                              class="w-full bg-[#0b0f19] border border-gray-600 text-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:border-emerald-500"
                              disabled={role !== 'admin'}>
                              <option value="true">true</option>
                              <option value="false">false</option>
                            </select>
                          {:else if field.type === 'number'}
                            <input type="number" bind:value={field._edited}
                              class="w-full bg-[#0b0f19] border border-gray-600 text-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:border-emerald-500"
                              disabled={role !== 'admin'} />
                          {:else if field.type === 'json-array'}
                            <input type="text" bind:value={field._edited}
                              class="w-full bg-[#0b0f19] border border-gray-600 text-gray-300 rounded px-3 py-1.5 text-sm font-mono focus:outline-none focus:border-emerald-500"
                              disabled={role !== 'admin'}
                              placeholder='["value1", "value2"]' />
                            <span class="text-[10px] text-gray-500">JSON array</span>
                          {:else}
                            <input type="text" bind:value={field._edited}
                              class="w-full bg-[#0b0f19] border border-gray-600 text-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:border-emerald-500"
                              disabled={role !== 'admin'} />
                          {/if}
                        </div>
                      </div>
                    </div>
                  {/each}
                {/if}
              {/each}
            </div>
          {/if}
        {:else}
          <!--Raw mode: plain textarea (fallback for unparseable files too) -->
          <textarea
          bind:value={settingsContent}
          disabled={role !== 'admin'}
          class="w-full h-[500px] bg-[#0b0f19] border border-gray-700 text-gray-300 font-mono text-sm p-4 rounded focus:outline-none focus:border-emerald-500 custom-scrollbar"
          spellcheck="false"
          ></textarea>
        {/if}
      {/if}
    </div>
  {/if}

  {#if activeTab === 'hardware'}
    <div class="bg-[#1f2937] p-5 rounded-lg border border-gray-700">
      <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3 mb-6">
  <h3 class="font-bold text-gray-200">Hardware & Container Overrides</h3>
  
  {#if role === 'admin'}
    <div class="flex gap-3">
      <button 
        onclick={resetHardware} 
        disabled={isRebuilding}
        class="bg-red-900/50 hover:bg-red-700 text-red-200 border border-red-800 px-4 py-2 rounded font-medium disabled:opacity-50 transition-colors text-sm">
        Reset to Defaults
      </button>

      <button 
        onclick={saveHardware} 
        disabled={isRebuilding}
        class="bg-purple-600 hover:bg-purple-500 text-white px-6 py-2 rounded font-medium disabled:opacity-50 transition-colors text-sm">
        {isRebuilding ? 'Rebuilding...' : 'Apply & Rebuild'}
      </button>
    </div>
  {/if}
</div>

      <div class="space-y-6">
        <div class="bg-[#111827] p-4 rounded border border-gray-700">
          <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-2">Memory (RAM) Allocation</label>
          <p class="text-xs text-gray-500 mb-3">Docker syntax required (e.g., '2g', '4g', '8192m'). Exceeding host capacity will crash the server.</p>
          <input 
            type="text" 
            bind:value={editBuffer.ram_limit} 
            disabled={role !== 'admin'}
            oninput={() => isEditing = true}
            class="w-full md:w-1/3 bg-[#0b0f19] border border-gray-600 text-gray-300 rounded px-3 py-2 focus:outline-none focus:border-purple-500 font-mono" 
          />
        </div>

        <div class="bg-[#111827] p-4 rounded border border-gray-700">
          <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-2">Description</label>
          <input 
            type="text" 
            bind:value={editBuffer.description} 
            oninput={() => isEditing = true}
            placeholder="Server description..."
            class="w-full bg-[#0b0f19] border border-gray-600 text-gray-300 rounded px-3 py-2 focus:border-purple-500" 
          />
        </div>

        {#if (hardwareData?.game_type || '').toLowerCase().includes('minecraft') || (hardwareData?.game_type || '').toLowerCase().includes('factorio')}
          <div class="bg-[#111827] p-4 rounded border border-gray-700">
            <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-2">Game Version</label>
            <select
              bind:value={editBuffer.game_version}
              onchange={() => isEditing = true}
              class="w-full bg-[#0b0f19] border border-gray-600 text-gray-300 rounded px-3 py-2 focus:border-purple-500">
              {#if availableVersions.length === 0}
                <option value={editBuffer.game_version}>{editBuffer.game_version || 'Loading...'}</option>
              {:else}
                {#each availableVersions as entry}
                  {@const [label, val] = entry.split('|')}
                  <option value={val}>{label}</option>
                {/each}
              {/if}
            </select>
            </div>
        {/if}
      </div>
      
    </div>
  {/if}

  {#if activeTab === 'backups'}
    <div class="bg-[#1f2937] p-5 rounded-lg border border-gray-700">
      
      <div class="flex justify-between items-center mb-6">
        <h3 class="font-bold text-gray-200">Server Snapshots</h3>
        {#if role === 'admin'}
          <button 
            onclick={handleManualBackup} 
            disabled={isBackingUp || isRestoring}
            class="bg-[#10b981] hover:bg-[#059669] text-white px-4 py-2 rounded text-sm font-medium transition-colors disabled:opacity-50">
            {isBackingUp ? 'Zipping Data...' : '+ Create Manual Backup'}
          </button>
        {/if}
      </div>

      <div class="bg-[#111827] rounded border border-gray-700 overflow-x-auto w-full">
        
        <table class="w-full text-left text-sm text-gray-300 min-w-[700px] whitespace-nowrap">
          <thead class="bg-gray-800 text-xs uppercase text-gray-400 border-b border-gray-700">
            <tr>
              <th class="px-4 py-3">Timestamp</th>
              <th class="px-4 py-3">Filename</th>
              <th class="px-4 py-3">Type</th>
              <th class="px-4 py-3">Size</th>
              {#if role === 'admin'}<th class="px-4 py-3 text-right">Actions</th>{/if}
            </tr>
          </thead>
          <tbody>
            {#if backupList.length === 0}
              <tr>
                <td colspan="5" class="px-4 py-8 text-center text-gray-500 italic">No backups found for this server.</td>
              </tr>
            {/if}
            {#each backupList as backup}
              <tr class="border-b border-gray-800 hover:bg-gray-800/50 transition-colors">
                <td class="px-4 py-3 font-mono text-xs">{backup.timestamp}</td>
                <td class="px-4 py-3 truncate max-w-[200px]" title={backup.filename}>{backup.filename}</td>
                <td class="px-4 py-3">
                  <span class="px-2 py-1 rounded text-[10px] uppercase tracking-wider {backup.type === 'Lunkserver Backup' ? 'bg-blue-900/30 text-blue-400' : 'bg-purple-900/30 text-purple-400'}">
                    {backup.type}
                  </span>
                </td>
                <td class="px-4 py-3 font-mono text-xs">{backup.size_mb} MB</td>
                
                {#if role === 'admin'}
                <td class="px-4 py-3 text-right space-x-2">
                  <button 
                    onclick={() => handleRestoreBackup(backup.filename, backup.type)}
                    disabled={isRestoring || isBackingUp}
                    class="text-emerald-400 hover:text-emerald-300 font-medium disabled:opacity-50">
                    Restore
                  </button>
                  <button 
                    onclick={() => handleDeleteBackup(backup.filename, backup.type)}
                    disabled={isRestoring || isBackingUp}
                    class="text-red-400 hover:text-red-300 font-medium disabled:opacity-50">
                    Delete
                  </button>
                </td>
                {/if}


              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      
    </div>
  {/if}

  {#if activeTab === 'mods'}
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      
      <div class="bg-[#1f2937] p-5 rounded-lg border border-gray-700">
        {#if server.game_type === 'factorio'}
        <h3 class="font-bold text-gray-200 mb-4">Search Factorio Mod Portal</h3>
        <form onsubmit={searchFactorioMods} class="flex flex-col sm:flex-row gap-2 mb-4 w-full">
          <input type="text" bind:value={factorioSearchQuery} placeholder="Search mods (e.g. Krastorio)..." class="flex-1 min-w-0 bg-[#111827] border border-gray-600 text-gray-200 px-3 py-2 rounded focus:outline-none focus:border-emerald-500" />
          {#if role === "admin"}
          <button type="submit" disabled={isSearching} class="w-full sm:w-auto bg-[#d97706] hover:bg-[#b45309] text-white px-4 py-2 rounded font-medium disabled:opacity-50">
            {isSearching ? '...' : 'Search'}
          </button>
          {:else}
          <button disabled class="bg-[#d97706] hover:bg-[#b45309] text-white px-4 py-2 rounded font-medium disabled:opacity-50">
            <span class="italic">Only for admins</span>
          </button>
          {/if}
        </form>
        
        <div class="flex flex-col gap-3 h-[260px] overflow-y-auto pr-2 custom-scrollbar">
          {#each factorioSearchResults as mod}
            <div class="bg-[#111827] p-3 rounded border border-gray-700 flex justify-between items-center">
              <div class="min-w-0 flex-1 pr-3">
                <div class="text-gray-200 font-medium">{mod.title}</div>
                <div class="text-xs text-gray-400">By {mod.owner} · v{mod.version} · Factorio {mod.factorio_version}</div>
              </div>
              {#if role === 'admin'}
              <button onclick={() => installFactorioMod(mod)} class="text-xs bg-[#10b981] hover:bg-[#059669] text-white px-3 py-1.5 rounded transition-colors flex-shrink-0">
                Install
              </button>
              {/if}
            </div>
          {/each}
          
          {#if factorioSearchResults.length === 0 && !isSearching}
            <div class="text-gray-500 text-sm italic">No results yet.</div>
          {/if}
        </div>
        {:else}
        <h3 class="font-bold text-gray-200 mb-4">Search Modrinth</h3>
        <form onsubmit={searchModrinth} class="flex flex-col sm:flex-row gap-2 mb-4 w-full">
          <input type="text" bind:value={searchQuery} placeholder="Search for a mod..." class="flex-1 min-w-0 bg-[#111827] border border-gray-600 text-gray-200 px-3 py-2 rounded focus:outline-none focus:border-emerald-500" />
          {#if role === "admin"}
          <button type="submit" disabled={isSearching} class="w-full sm:w-auto bg-[#d97706] hover:bg-[#b45309] text-white px-4 py-2 rounded font-medium disabled:opacity-50">
            {isSearching ? '...' : 'Search'}
          </button>
          {:else}
          <button disabled class="bg-[#d97706] hover:bg-[#b45309] text-white px-4 py-2 rounded font-medium disabled:opacity-50">
            <span class="italic">Only for admins</span>
          </button>
          {/if}
        </form>
        
        {#if server.game_type === 'minecraft' && role === 'admin'}
        <div class="space-y-2 mb-3">
          <label class="flex items-center gap-2 cursor-pointer bg-blue-500/10 border border-blue-500/30 rounded-lg px-3 py-2">
            <input type="checkbox" checked={geyserChecked} disabled={isInstallingGeyser}
              onchange={(e) => toggleGeyser(e.currentTarget.checked)} class="rounded" />
            <div class="flex-1">
              <span class="text-sm text-blue-300 font-medium">Geyser + Floodgate</span>
              <span class="text-xs text-gray-500 ml-1">Bedrock crossplay</span>
            </div>
            <input type="number" value={geyserPort} min={1024} max={65535} disabled={isInstallingGeyser || geyserChecked}
              onchange={(e) => geyserPort = parseInt(e.currentTarget.value) || 19132}
              class="w-20 bg-[#111827] border border-gray-600 text-gray-200 text-sm px-2 py-1 rounded text-right disabled:opacity-60"
              title="Bedrock UDP port (locked while installed)" />
            <span class="text-xs text-gray-500">/udp</span>
            {#if isInstallingGeyser}<span class="text-xs text-gray-500">...</span>{/if}
          </label>
          <label class="flex items-center gap-2 cursor-pointer bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-3 py-2">
            <input type="checkbox" checked={optimizationChecked} disabled={isTogglingOpt}
              onchange={(e) => toggleOptimization(e.currentTarget.checked)} class="rounded" />
            <div class="flex-1">
              <span class="text-sm text-emerald-300 font-medium">Optimization Mods</span>
              <span class="text-xs text-gray-500 ml-1">Lithium, FerriteCore, Krypton, ServerCore</span>
            </div>
            {#if isTogglingOpt}<span class="text-xs text-gray-500">...</span>{/if}
          </label>
        </div>
        {/if}
        
        <div class="flex flex-col gap-3 h-[260px] overflow-y-auto pr-2 custom-scrollbar">
          {#each searchResults as mod}
            <div class="bg-[#111827] p-3 rounded border border-gray-700 flex justify-between items-center">
              <div>
                <div class="text-gray-200 font-medium">{mod.title}</div>
                <div class="text-xs text-gray-400">By {mod.author}</div>
              </div>
              <button onclick={() => installMod(mod.slug)} class="text-xs bg-[#10b981] hover:bg-[#059669] text-white px-3 py-1.5 rounded transition-colors">
                Install
              </button>
            </div>
          {/each}
          
          {#if searchResults.length === 0 && !isSearching}
            <div class="text-gray-500 text-sm italic">No results yet.</div>
          {/if}
        </div>
        {/if}
      </div>

      <div class="bg-[#1f2937] p-5 rounded-lg border border-gray-700 lg:h-[400px] overflow-y-auto custom-scrollbar">
        <h3 class="font-bold text-gray-200 mb-4 flex justify-between items-center">
          Installed Mods
          <button onclick={loadInstalledMods} disabled={server.status !== 'running'} class="text-xs font-normal text-gray-400 hover:text-gray-200 disabled:opacity-50">↻ Refresh</button>
        </h3>
        
        <div class="bg-[#1f2937] p-5 rounded-lg border border-gray-700 lg:h-[400px] overflow-y-auto custom-scrollbar">
         
      
      {#if installedMods.length === 0}
        <div class="text-gray-500 text-sm italic">No mods found.</div>
      {:else}
        <div class="flex flex-col gap-2">
          {#each installedMods as modJar}
            <div class="bg-[#111827] px-3 py-2 rounded border border-gray-700 flex justify-between items-center group">
              <span class="text-sm text-gray-300 truncate pr-4" title={modJar}>{modJar}</span>
              {#if role === 'admin'}
              <button onclick={() => deleteMod(modJar)} class="text-red-400 hover:text-red-300 opacity-0 group-hover:opacity-100 transition-opacity p-1">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
              </button>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </div>
      </div>
    </div>
  {/if}

  <div class="bg-[#1f2937] border border-gray-700 rounded-lg p-5 mt-6">
    <div class="text-gray-400 text-sm font-medium mb-4">Game Metrics</div>
    <div class="grid grid-cols-2 gap-4">
      
      <div class="bg-[#111827] p-4 rounded border border-gray-700/50 min-w-0">
        <div class="text-[10px] text-gray-500 font-bold uppercase tracking-wider mb-1 flex items-center justify-between">
          <span>Image</span>
          {#if updateAvailable === true}
            <span class="text-amber-400">● Update available</span>
          {:else if updateAvailable === false}
            <span class="text-emerald-400">● Up to date</span>
          {/if}
        </div>
        <div class="text-gray-200 font-mono text-sm break-words">{server.image || 'Not yet installed'}</div>
        {#if role === 'admin' && server.image}
          <button
            onclick={checkForUpdate}
            disabled={isCheckingUpdate}
            class="mt-2 text-xs px-3 py-1 bg-gray-700 hover:bg-gray-600 text-gray-200 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
            {isCheckingUpdate ? 'Checking...' : 'Check for Update'}
          </button>
        {/if}
      </div>
      
      <div class="bg-[#111827] p-4 rounded border border-gray-700/50 min-w-0">
        <div class="text-[10px] text-gray-500 font-bold uppercase tracking-wider mb-1">Container ID</div>
        <div class="text-gray-200 font-mono text-sm break-words">
          {server.container_id ? server.container_id.substring(0, 12) : 'Awaiting Boot...'}
        </div>
      </div>

      <div class="bg-[#111827] p-4 rounded border border-gray-700/50 min-w-0">
        <div class="text-[10px] text-gray-500 font-bold uppercase tracking-wider mb-1">Server IP Address</div>
        <div class="text-gray-200 font-mono text-sm break-words">
          {connectionAddress}
        </div>
        {#if bedrockPort}
        <div class="mt-2 pt-2 border-t border-gray-700/30">
          <div class="text-[10px] text-blue-400 font-bold uppercase tracking-wider mb-1">Bedrock (Geyser)</div>
          <div class="text-blue-300 font-mono text-sm break-words">
            <DOMAIN>:{bedrockPort}
          </div>
        </div>
        {/if}
      </div>

      <div class="bg-[#111827] p-4 rounded border border-gray-700/50 min-w-0">
        <div class="text-[10px] text-gray-500 font-bold uppercase tracking-wider mb-1">Disk Usage</div>
        <div class="text-gray-200 font-mono text-sm break-words">
          {(server.disk_gb ?? 0).toFixed(2)} GB
        </div>
      </div>

    </div>
  </div>

</div>

<style>
  /* Custom dark scrollbar for the UI elements */
  .custom-scrollbar::-webkit-scrollbar {
    width: 6px;
  }
  .custom-scrollbar::-webkit-scrollbar-track {
    background: #1f2937; 
    border-radius: 4px;
  }
  .custom-scrollbar::-webkit-scrollbar-thumb {
    background: #4b5563; 
    border-radius: 4px;
  }
  .custom-scrollbar::-webkit-scrollbar-thumb:hover {
    background: #6b7280; 
  }
</style>
