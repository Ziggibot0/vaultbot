const { Plugin, Setting, ItemView, PluginSettingTab, Notice, Modal, MarkdownRenderer } = require('obsidian');
const { spawn } = require('child_process');
const path = require('path');

class VaultBotPlugin extends Plugin {
	async onload() {
		this.settings = {
			// Use 127.0.0.1, NOT localhost: the backend (uvicorn) binds to
			// 127.0.0.1 (IPv4 only). On Windows, 'localhost' resolves to ::1
			// (IPv6) first, and fetch() to [::1]:8000 gets ERR_CONNECTION_REFUSED
			// even though the backend is up on IPv4 — which made the liveness
			// probe + restart button intermittently fail.
			backendUrl: 'http://127.0.0.1:8000',
			autoStartBackend: true,
			autoStartMcpServer: true,
			researchBackend: 'freesearch',
			tavilyApiKey: '',
			safeMode: true,
			allowWebResearch: true,
			bsDetectorMessages: true,
		// -- Talk Mode (JARVIS-style spoken conversations) --
		// Off by default; opt in via the settings toggle. The Call button only
		// appears in the sidebar when talkMode is true. STT/TTS run in the
		// browser (Web Speech API) -- no server, no blocked DLLs.
		talkMode: false,
		// STT/TTS model selections (browser voice/lang ids). Empty = not set;
		// picked from dropdowns. Mirrored into the backend registry pot.
		sttModel: '',
		ttsModel: '',
		// Speak each sentence as it streams (chunked TTS) -- real-time feel.
		ttsStream: true,
		// Auto-listen after the bot finishes speaking, for back-and-forth.
		ttsAutoListen: false,
		};
		this.backendStarting = false;
		this.mcpProcess = null;
		// Shared in-flight boot promise: prevents multiple callers from
		// each opening their own /health + / fetch loop while the backend
		// is still booting (which floods the dev console with
		// ERR_CONNECTION_REFUSED). All callers should await
		// this.onceBackendReady() instead of polling independently.
		this._backendReadyPromise = null;
		await this.loadSettings();

		// Ensure Obsidian ignores VaultBot's internal directories. The backend
		// writes frequently (session logs, conversation state, FAISS index) and
		// every file event triggers Obsidian's metadata cache + graph refresh.
		// Without this, Obsidian bogs down when VaultBot is actively working.
		this._ensureIgnoredDirs();

		this.addCommand({
			id: 'open-vaultbot-sidebar',
			name: 'Open VaultBot Sidebar',
			callback: () => {
				this.openSidebar();
			}
		});

		this.addCommand({
			id: 'show-setup-instructions',
			name: 'Show setup instructions',
			callback: () => {
				this._showSetupNeededModal();
			}
		});

		this.addRibbonIcon('bot', 'VaultBot', () => {
			this.openSidebar();
		});

		this.addSettingTab(new VaultBotSettingTab(this.app, this));

		const backendUrl = this.settings.backendUrl;
		this.registerView(
			'vaultbot-sidebar',
			(leaf) => new VaultBotSidebarView(leaf, backendUrl, this)
		);

		// First-run health gate: if the vault lives inside a cloud-sync
		// folder (OneDrive/Dropbox/iCloud/Google Drive), show a blocking
		// modal BEFORE starting the backend. Sync services corrupt the
		// SQLite + FAISS database files VaultBot writes. This used to be a
		// buried README footnote; promoting it to an in-product guard means
		// the user finds out before their first chat silently corrupts.
		this._checkSyncedFolder();

		if (this.settings.autoStartBackend) {
			// Wait a moment for Obsidian to settle, then try a single start.
			// Delayed slightly more if the sync-folder modal is showing so
			// the backend doesn't start behind a blocking dialog.
			const delay = this._syncWarningShown ? 4000 : 2000;
			setTimeout(() => this.startBackendIfNeeded(), delay);
		}
		// The MCP server is part of the plugin — it just works when Obsidian
		// opens. It waits for the backend to be ready, then spawns and writes
		// MCP client configs so VS Code Copilot Chat / Claude auto-discover it.
		setTimeout(() => this.startMcpServerIfNeeded(), 6000);

		// Belt-and-suspenders: fire-and-forget a /shutdown beacon the moment
		// the Obsidian window starts closing. navigator.sendBeacon is built
		// for exactly this — it delivers the POST during teardown without
		// needing a response and isn't cancelled like fetch when the renderer
		// is destroyed. The backend's /shutdown endpoint self-terminates via
		// os._exit, so no response is needed. This survives the case where
		// onunload's async fetch is torn down before it completes.
		this._beforeUnloadHandler = (e) => {
			try {
				// sendBeacon can't set custom headers, so the auth token goes
				// as a ?token= query param (the backend's _AuthMiddleware
				// accepts this on /shutdown only).
				const token = this._getAuthToken();
				const url = token
					? this.settings.backendUrl + '/shutdown?token=' + encodeURIComponent(token)
					: this.settings.backendUrl + '/shutdown';
				navigator.sendBeacon(url, new Blob([''], {type: 'text/plain'}));
			} catch (err) {}
		};
		window.addEventListener('beforeunload', this._beforeUnloadHandler);
	}

	async onunload() {
		console.log('Unloading VaultBot plugin');
		// Remove the beforeunload listener so a manual plugin disable doesn't
		// double-fire shutdown (stopBackend covers that case).
		if (this._beforeUnloadHandler) {
			window.removeEventListener('beforeunload', this._beforeUnloadHandler);
			this._beforeUnloadHandler = null;
		}
		this.stopMcpServer();
		// When reloading (not unloading), keep the backend alive so the
		// new plugin instance reconnects to the existing backend instead
		// of spawning a new one. This makes plugin reload ~2s instead of
		// ~8s (no backend shutdown + restart cycle).
		if (this._isReloading) {
			console.log('Plugin reload in progress — keeping backend alive');
		} else {
			await this.stopBackend();
		}
	}

	// Reload the plugin itself (disable + re-enable) without killing the
	// backend. This picks up changes to main.js/styles.css without you
	// having to manually toggle the plugin in Settings. Triggered by a
	// WebSocket message from the backend (type: 'reload_plugin').
	async reloadSelf() {
		const app = this.app;
		console.log('Reloading VaultBot plugin...');
		try { new Notice('Reloading VaultBot plugin...'); } catch (e) {}
		this._isReloading = true;

		// Schedule the re-enable from a setTimeout so it survives the
		// plugin instance being destroyed during disablePlugin. The
		// callback runs in the global event loop, not tied to this
		// plugin instance's lifecycle.
		setTimeout(async () => {
			try {
				await app.plugins.enablePlugin('vaultbot');
				console.log('VaultBot plugin reloaded successfully');
				try { new Notice('VaultBot plugin reloaded.'); } catch (e) {}
			} catch (e) {
				console.error('Failed to re-enable plugin:', e);
				try { new Notice('Plugin reload failed — re-enable manually in Settings > Community plugins'); } catch (e2) {}
			}
		}, 500);

		// Disable the plugin. onunload() sees _isReloading=true and
		// skips stopBackend(), so the backend stays running.
		try {
			await app.plugins.disablePlugin('vaultbot');
		} catch (e) {
			console.error('Failed to disable plugin for reload:', e);
			try { new Notice('Plugin reload failed: ' + (e.message || e)); } catch (e2) {}
		}
	}

	async loadSettings() {
		const saved = await this.loadData() || {};
		this.settings = Object.assign({}, this.settings, saved);
		// Migrate any saved 'localhost' URL to 127.0.0.1 so the plugin talks
		// to the backend over IPv4 (which is what uvicorn binds to). Without
		// this, a saved 'http://localhost:8000' keeps causing intermittent
		// ERR_CONNECTION_REFUSED on Windows because localhost resolves to
		// ::1 (IPv6) first and the backend isn't listening there.
		try {
			if (typeof this.settings.backendUrl === 'string' &&
				this.settings.backendUrl.includes('://localhost')) {
				this.settings.backendUrl = this.settings.backendUrl.replace('://localhost', '://127.0.0.1');
				await this.saveSettings();
			}
		} catch (e) { /* non-fatal */ }
	}

	async saveSettings() {
		await this.saveData(this.settings);
	}

	_ensureIgnoredDirs() {
		// Add VaultBot's internal directories to Obsidian's userIgnoreFilters
		// so Obsidian's file watcher doesn't fire on every backend file write.
		// This is the root-cause fix for "Obsidian bogs down when VaultBot is
		// cooking" — the backend writes session logs, conversation state, and
		// FAISS index files many times per second, and each event triggers
		// Obsidian's metadata cache update.
		const required = ['vaultbot/vaultbot_backend/', '.venv/', 'vaultbot/vaultbot_backend/vaultbot_index/'];
		try {
			const current = this.app.vault.getConfig('userIgnoreFilters') || [];
			let changed = false;
			const updated = [...current];
			for (const dir of required) {
				if (!updated.includes(dir)) {
					updated.push(dir);
					changed = true;
				}
			}
			if (changed) {
				this.app.vault.setConfig('userIgnoreFilters', updated);
				console.log('VaultBot: added internal dirs to userIgnoreFilters', updated);
			}
		} catch (e) {
			// Non-fatal: if the Obsidian API doesn't support this, just skip.
			console.warn('VaultBot: could not update userIgnoreFilters', e);
		}
	}

	async openSidebar() {
		let leaf = this.app.workspace.getRightLeaf(false);
		await leaf.setViewState({
			type: 'vaultbot-sidebar',
			state: {}
		});
		this.app.workspace.revealLeaf(leaf);
	}

	async isBackendRunning() {
		// Probe liveness. Use GET throughout: the backend registers /health as
		// a GET handler, and FastAPI returns 405 Method Not Allowed for HEAD on
		// GET-only routes — which previously made this poll always report "down"
		// even when the backend was healthy, causing endless respawn loops.
		// /health returns a small JSON dict; / returns a small marker dict, so
		// GET is cheap on both.
		//
		// NOTE: any failed fetch here is logged by Chromium's network layer
		// to the dev console as ERR_CONNECTION_REFUSED, regardless of the
		// try/catch. That's expected while the backend is booting. To avoid
		// spamming the console, callers should go through onceBackendReady()
		// rather than repeatedly invoking this directly during boot.
		try {
			const response = await fetch(this.settings.backendUrl + '/health', { method: 'GET' });
			if (response.ok || response.status === 200) return true;
		} catch (e) {}
		// Older backends without /health, or transient 5xx: fall back to GET /.
		try {
			const r2 = await fetch(this.settings.backendUrl + '/', { method: 'GET' });
			return r2.status === 200;
		} catch (e2) {
			return false;
		}
	}

	// Single-flight ready promise. The first caller to ask during a boot
	// starts ONE poll loop; everyone else (sidebar ensureConnection,
	// refreshModels, the MCP starter) awaits the same promise. This is what
	// stops the dev-console ERR_CONNECTION_REFUSED flood on startup.
	onceBackendReady(timeoutMs = 30000, intervalMs = 500) {
		if (this._backendReadyPromise) return this._backendReadyPromise;
		this._backendReadyPromise = (async () => {
			const start = Date.now();
			// Determine the PID file path once for the whole poll loop.
			let vaultRoot;
			if (this.app.vault.adapter.getBasePath) {
				vaultRoot = this.app.vault.adapter.getBasePath();
			} else {
				vaultRoot = this.app.vault.configDir.replace(/[\\/]\.obsidian[\\/]?$|^/, '');
			}
			const pidFile = path.join(vaultRoot, 'vaultbot', 'vaultbot_backend', 'vaultbot.pid');
			const fs = require('fs');
			while (Date.now() - start < timeoutMs) {
				// Only probe with fetch if the PID file exists — the backend
				// writes it early in startup. If the file is absent, the
				// backend hasn't started yet, so a fetch would just generate
				// ERR_CONNECTION_REFUSED console spam. Wait for the file.
				if (fs.existsSync(pidFile)) {
					if (await this.isBackendRunning()) return true;
				}
				await new Promise(r => setTimeout(r, intervalMs));
			}
			return await this.isBackendRunning();
		})();
		// Drop the cached promise once settled so a later boot (after the
		// backend dies and is restarted) gets a fresh poll.
		this._backendReadyPromise.finally(() => { this._backendReadyPromise = null; });
		return this._backendReadyPromise;
	}

	async fetchModels() {
		try {
			const response = await fetch(this.settings.backendUrl + '/models');
			if (!response.ok) return {models: [], current: ''};
			const data = await response.json();
			// Normalize: the backend now returns enriched objects
			// {name, vision, instruct}, but old backends may return plain
			// strings. We normalize both to objects so the frontend has
			// one shape to render.
			let models = Array.isArray(data.models) ? data.models : [];
			models = models.map(m => {
				if (typeof m === 'string') return {name: m, vision: false, instruct: true};
				return m;
			});
			return {models, current: data.current || ''};
		} catch (e) {
			return {models: [], current: ''};
		}
	}


	// Fetch the context-window size (in tokens) for a model. Auto-detects
	// from the active backend (Ollama /api/show or OpenAI-compatible known-
	// models table). Returns 32768 as a safe fallback on any failure.
	async fetchContextWindow(model) {
		try {
			const url = this.settings.backendUrl + '/model_context_window'
				+ (model ? ('?model=' + encodeURIComponent(model)) : '');
			const response = await fetch(url);
			if (!response.ok) return 32768;
			const data = await response.json();
			return data.context_window || 32768;
		} catch (e) {
			return 32768;
		}
	}

	// Read the synthesis-LLM backend config (Ollama local vs an OpenAI-compatible
	// API key). Lets the settings panel show which backend is active and whether
	// it's reachable, so a weak-laptop user can confirm their API key is wired up.
	// Probe whether the active chat model can see images. The GUI calls this
	// before ingest (and can call it on first chat) so it can alert the user
	// in plain language if their model is text-only and they need to pick a
	// vision model to read textbook pages. Human-centered: the alert lands in
	// the chat where they already are, not buried in a settings panel.
	async fetchVisionCheck() {
		try {
			const response = await fetch(this.settings.backendUrl + '/llm/vision_check');
			if (!response.ok) return null;
			return await response.json();
		} catch (e) {
			return null;
		}
	}

	// ── Provider + Model Registry helpers (the interchangeable "pot") ─────
	// One combined list of providers + models feeds all three role dropdowns
	// (big/small/vision). Every model — local Ollama, OpenRouter, OpenAI — is
	// in the same pot and can be mapped into any role with one call.
	async fetchProviders() {
		try {
			const r = await fetch(this.settings.backendUrl + '/llm/providers');
			if (!r.ok) return null;
			return await r.json();   // {providers, known, roles}
		} catch (e) { return null; }
	}
	async addProviderCfg({id, type, baseUrl, apiKey, label}) {
		try {
			const body = {id, type, base_url: baseUrl};
			if (apiKey !== undefined) body.api_key = apiKey;
			if (label !== undefined) body.label = label;
			const r = await fetch(this.settings.backendUrl + '/llm/providers', {
				method: 'POST', headers: {'Content-Type': 'application/json'},
				body: JSON.stringify(body)});
			return await r.json();
		} catch (e) { return null; }
	}
	async removeProviderCfg(providerId) {
		try {
			const r = await fetch(this.settings.backendUrl + '/llm/providers/' + encodeURIComponent(providerId), {method: 'DELETE'});
			return await r.json();
		} catch (e) { return null; }
	}
	async fetchAllModels() {
		// The whole pot, grouped by provider, with role assignments.
		try {
			const r = await fetch(this.settings.backendUrl + '/llm/models/all');
			if (!r.ok) return null;
			return await r.json();   // {models, roles:{big,small,vision}}
		} catch (e) { return null; }
	}
	async addModelCfg({id, model, provider, vision, instruct, label}) {
		try {
			const body = {model, provider, vision: !!vision, instruct: instruct !== false};
			if (id) body.id = id;
			if (label !== undefined) body.label = label;
			const r = await fetch(this.settings.backendUrl + '/llm/models', {
				method: 'POST', headers: {'Content-Type': 'application/json'},
				body: JSON.stringify(body)});
			return await r.json();
		} catch (e) { return null; }
	}
	async removeModelCfg(modelId) {
		try {
			const r = await fetch(this.settings.backendUrl + '/llm/models/' + encodeURIComponent(modelId), {method: 'DELETE'});
			return await r.json();
		} catch (e) { return null; }
	}
	async setRoleCfg(role, modelId) {
		// One interchange call: map any model in the pot into any role.
		try {
			const r = await fetch(this.settings.backendUrl + '/llm/role', {
				method: 'POST', headers: {'Content-Type': 'application/json'},
				body: JSON.stringify({role, model_id: modelId})});
			return await r.json();
		} catch (e) { return null; }
	}
	async fetchProviderLiveModels(providerId) {
		try {
			const r = await fetch(this.settings.backendUrl + '/llm/providers/' + encodeURIComponent(providerId) + '/live_models');
			if (!r.ok) return {models: []};
			return await r.json();
		} catch (e) { return {models: []}; }
	}

	// ── Tournament helpers ─────────────────────────────────────────────
	async fetchTournamentModels() {
		try {
			const r = await fetch(this.settings.backendUrl + '/tournament/models');
			if (!r.ok) return null;
			return await r.json();
		} catch (e) { return null; }
	}
	async fetchTournamentBenchmarks(role) {
		try {
			const r = await fetch(this.settings.backendUrl + '/tournament/benchmarks?role=' + encodeURIComponent(role));
			if (!r.ok) return null;
			return await r.json();
		} catch (e) { return null; }
	}
	async runTournament(modelIds, role) {
		try {
			const r = await fetch(this.settings.backendUrl + '/tournament/run', {
				method: 'POST', headers: {'Content-Type': 'application/json'},
				body: JSON.stringify({model_ids: modelIds, role: role})});
			return await r.json();
		} catch (e) { return {status: 'error', detail: String(e)}; }
	}

	// Push research-backend settings (Tavily key + backend choice) to the
	// running backend so they take effect immediately, no restart needed.
	async pushResearchConfig() {
		try {
			await fetch(this.settings.backendUrl + '/config', {
				method: 'POST',
				headers: {'Content-Type': 'application/json'},
				body: JSON.stringify({
					tavily_api_key: this.settings.tavilyApiKey || '',
					research_backend: this.settings.researchBackend || 'tavily'
				})
			});
		} catch (e) {
			console.warn('VaultBot: could not push research config', e);
		}
	}

	// ─────────────────────────────────────────────────────────────────────
	// Cross-platform venv path helpers.
	// Windows: .venv/Scripts/{pythonw.exe,python.exe}
	// macOS/Linux: .venv/bin/python
	// `.venv` is hidden in Obsidian's file explorer (dots filtered).
	// ─────────────────────────────────────────────────────────────────────
	_venvBinDir() {
		return process.platform === 'win32' ? 'Scripts' : 'bin';
	}

	_venvPythonExe(vaultRoot) {
		const bin = this._venvBinDir();
		const candidates = process.platform === 'win32'
			? [path.join(vaultRoot, '.venv', bin, 'pythonw.exe'),
			   path.join(vaultRoot, '.venv', bin, 'python.exe')]
			: [path.join(vaultRoot, '.venv', bin, 'python')];
		const fs = require('fs');
		return candidates.find(p => fs.existsSync(p)) || candidates[0];
	}

	// Check whether the vault root is inside a known cloud-sync folder.
	// If so, show a blocking modal warning about database corruption.
	// Sets this._syncWarningShown so onload can delay backend start until
	// the user has acknowledged the modal.
	_checkSyncedFolder() {
		try {
			let vaultRoot;
			if (this.app.vault.adapter.getBasePath) {
				vaultRoot = this.app.vault.adapter.getBasePath();
			} else {
				vaultRoot = this.app.vault.configDir.replace(/[\\/]\.obsidian[\\/]?$/, '');
			}
			if (!vaultRoot) return;
			const p = vaultRoot.toLowerCase().replace(/\\/g, '/');
			// Match on path segments to avoid false positives like
			// "OneDriveBackup". The leading+trailing slashes ensure we're
			// matching a real folder segment, not a substring.
			const syncMarkers = [
				'/onedrive/', '/dropbox/', '/icloud~', '/icloud drive/',
				'/google drive/', '/googledrive/'
			];
			const inSync = syncMarkers.some(m => p.includes(m));
			if (inSync) {
				this._syncWarningShown = true;
				this._showSyncedFolderModal(vaultRoot);
			}
		} catch (e) {
			console.warn('VaultBot: could not check sync folder', e);
		}
	}

	_showSyncedFolderModal(vaultRoot) {
		try { new Notice('VaultBot: sync-folder warning — see the dialog.'); } catch (e) {}
		const modal = new Modal(this.app);
		modal.titleEl.setText('Your vault is in a sync folder');
		modal.titleEl.style.color = 'var(--text-error)';

		const desc = modal.contentEl.createEl('p');
		desc.setText(
			'Your vault folder is inside a cloud-sync service (OneDrive, ' +
			'Dropbox, iCloud, or Google Drive). VaultBot creates database ' +
			'files (a search index and conversation history) that get ' +
			'corrupted when two devices try to sync them at the same time.'
		);
		desc.style.opacity = '0.85';
		desc.style.lineHeight = '1.5';

		const pathEl = modal.contentEl.createEl('p');
		pathEl.setText('Your vault is at: ' + vaultRoot);
		pathEl.style.fontSize = '0.85em';
		pathEl.style.opacity = '0.7';
		pathEl.style.wordBreak = 'break-all';

		const rec = modal.contentEl.createEl('p');
		rec.setText(
			'To keep your data safe, move your vault folder to a plain ' +
			'local folder (like Documents/VaultBot), then re-open it in ' +
			'Obsidian. You don\'t need to reinstall — just move the folder.'
		);
		rec.style.opacity = '0.85';
		rec.style.lineHeight = '1.5';

		const btnRow = modal.contentEl.createDiv();
		btnRow.style.marginTop = '16px';
		btnRow.style.display = 'flex';
		btnRow.style.gap = '8px';

		// "I understand, keep going" — lets the user proceed at their own
		// risk. We don't hard-block because some users use a single-device
		// sync with no conflict risk and would be annoyed by a dead-end.
		const proceedBtn = btnRow.createEl('button', {text: 'I understand, keep going'});
		proceedBtn.addEventListener('click', () => {
			modal.close();
		});

		const closeBtn = btnRow.createEl('button', {text: 'Close', cls: 'mod-cta'});
		closeBtn.addEventListener('click', () => modal.close());
		modal.open();
	}

	// Show a friendly setup wizard when the venv or backend code is
	// missing. Instead of just showing a one-liner command, this calls
	// /preflight (which doesn't need the backend) to detect WHAT is
	// missing — Python, Ollama, the venv, or just the backend code — and
	// shows a checklist with per-item "Get X" download buttons so the user
	// knows exactly what to install and where to get it. The one-liner
	// is still shown at the bottom for the "just run it" path.
	_showSetupNeededModal() {
		try { new Notice('VaultBot needs setup. Check the instructions.'); } catch (e) {}
		const modal = new Modal(this.app);
		modal.titleEl.setText('Welcome to VaultBot');
		const isWin = process.platform === 'win32';
		const cmd = isWin
			? 'irm https://github.com/ziggibot-uni/vaultbot/raw/main/vaultbot/setup.ps1 | iex'
			: 'curl -fsSL https://github.com/ziggibot-uni/vaultbot/raw/main/vaultbot/setup.sh | bash';

		// ── Checklist: what's missing? ──────────────────────────────────
		// /preflight checks Python + Ollama presence, port, and sync folder
		// without needing the backend running. We call it to build a
		// tailored checklist so the user sees "Install Python" only if
		// Python is actually missing, not a generic wall of steps.
		const checkSection = modal.contentEl.createEl('div');
		checkSection.createEl('p', {
			text: 'Let me check what you need...'}).style.opacity = '0.85';

		const checklistEl = checkSection.createDiv({cls: 'vaultbot-setup-checklist'});

		const runPreflight = async () => {
			checklistEl.empty();
			checklistEl.createEl('div', {
				text: 'Checking...', cls: 'vaultbot-setup-checking'});
			try {
				// /preflight runs on the backend even when the backend
				// isn't fully started — it only does environment checks.
				// If the backend isn't up at all (no venv), the fetch fails
				// and we fall back to the generic one-liner instructions.
				const resp = await fetch(this.settings.backendUrl + '/preflight');
				if (!resp.ok) throw new Error('preflight failed');
				const data = await resp.json();
				const problems = data.problems || [];
				checklistEl.empty();
				if (problems.length === 0) {
					checklistEl.createEl('div', {
						text: 'Everything looks installed. Try restarting the backend (click Restart in the sidebar).',
						cls: 'vaultbot-setup-ok'});
				} else {
					for (const p of problems) {
						const item = checklistEl.createDiv({
							cls: 'vaultbot-setup-item'});
						item.createEl('span', {
							cls: 'vaultbot-setup-item-icon',
							text: p.severity === 'broken' ? '⚠️' : '⚙️'});
						const textCol = item.createDiv({
							cls: 'vaultbot-setup-item-text'});
						textCol.createEl('div', {
							text: p.user_message,
							cls: 'vaultbot-setup-item-msg'});
						if (p.remedy_hint) {
							textCol.createEl('div', {
								text: p.remedy_hint,
								cls: 'vaultbot-setup-item-remedy'});
						}
						// Per-item action button (download link, etc.).
						if (p.action === 'open_download_python') {
							const btn = item.createEl('button', {
								text: 'Get Python', cls: 'mod-cta'});
							btn.addEventListener('click', () => {
								window.open('https://python.org/downloads', '_blank');
							});
						} else if (p.action === 'open_download_ollama') {
							const btn = item.createEl('button', {
								text: 'Get Ollama', cls: 'mod-cta'});
							btn.addEventListener('click', () => {
								window.open('https://ollama.com', '_blank');
							});
						} else if (p.action === 'finish_setup') {
							// The venv/backend code is missing — the
							// one-liner below is the fix for this.
						}
					}
				}
			} catch (e) {
				// Backend not reachable (no venv) — show the generic
				// one-liner instructions. This is the expected path for a
				// first install where nothing exists yet.
				checklistEl.empty();
				checklistEl.createEl('div', {
					text: 'VaultBot isn\'t installed yet. That\'s OK — it only takes one command.',
					cls: 'vaultbot-setup-ok'});
			}
		};
		runPreflight();

		// ── One-liner: the "just run it" path ───────────────────────────
		const divider = modal.contentEl.createEl('hr');
		divider.style.margin = '16px 0';
		divider.style.border = 'none';
		divider.style.borderTop = '1px solid var(--background-modifier-border)';

		const oneLinerDesc = modal.contentEl.createEl('p');
		oneLinerDesc.setText(
			'Open a terminal and paste this one line — the installer asks ' +
			'your name, downloads everything, and opens Obsidian for you.'
		);
		oneLinerDesc.style.opacity = '0.85';

		const codeEl = modal.contentEl.createEl('pre');
		codeEl.setText(cmd);
		codeEl.style.background = 'var(--background-secondary)';
		codeEl.style.padding = '12px';
		codeEl.style.borderRadius = '6px';
		codeEl.style.overflowX = 'auto';
		codeEl.style.fontSize = '13px';
		codeEl.style.userSelect = 'all';

		const btnRow = modal.contentEl.createDiv();
		btnRow.style.marginTop = '16px';
		btnRow.style.display = 'flex';
		btnRow.style.gap = '8px';
		const copyBtn = btnRow.createEl('button', {text: 'Copy command', cls: 'mod-cta'});
		copyBtn.addEventListener('click', () => {
			try {
				navigator.clipboard.writeText(cmd);
				copyBtn.setText('Copied!');
				setTimeout(() => copyBtn.setText('Copy command'), 2000);
			} catch (e) {}
		});
		const closeBtn = btnRow.createEl('button', {text: 'Close'});
		closeBtn.addEventListener('click', () => modal.close());
		modal.open();
	}

	// Show a modal listing recommended models to download. Each model has
	// a "Download" button that calls /models/pull, which runs `ollama pull`
	// in the background and streams progress over the WebSocket. The modal
	// shows a live progress bar for each downloading model. When the pull
	// completes, the model list is refreshed so the new model appears in
	// the dropdown. This is the zero-terminal way to get a model installed.
	// `onDone` is called after a successful pull so the caller (e.g.
	// refreshModels) can update the dropdown.
	async _showDownloadModelModal(onDone) {
		const modal = new Modal(this.app);
		modal.titleEl.setText('Download a model');
		const desc = modal.contentEl.createEl('p');
		desc.setText(
			'VaultBot needs an AI model to think with. Pick one below — ' +
			'it downloads once and you\'re set. No terminal needed.');
		desc.style.opacity = '0.85';
		desc.style.lineHeight = '1.5';

		let models = [];
		try {
			const resp = await fetch(this.settings.backendUrl + '/models/recommended');
			if (resp.ok) models = (await resp.json()).models || [];
		} catch (e) { /* backend may be down; show a fallback */ }

		if (!models.length) {
			// Fallback if the endpoint is unreachable.
			models = [
				{name: 'qwen3.6:latest', label: 'Qwen 3.6 (recommended)',
				 desc: 'Balanced text model.', size: '~2 GB'},
				{name: 'nomic-embed-text', label: 'Nomic Embed (for search)',
				 desc: 'Embedding model — needed for vault search.', size: '~270 MB'},
			];
		}

		const listEl = modal.contentEl.createDiv({cls: 'vaultbot-pull-list'});
		for (const m of models) {
			const item = listEl.createDiv({cls: 'vaultbot-pull-item'});
			item.createEl('div', {cls: 'vaultbot-pull-label', text: m.label || m.name});
			if (m.desc) item.createEl('div', {
				cls: 'vaultbot-pull-desc', text: m.desc});
			if (m.size) item.createEl('div', {
				cls: 'vaultbot-pull-size', text: m.size});
			const progressEl = item.createEl('progress', {
				attr: {max: '100', value: '0'}});
			progressEl.style.width = '100%';
			progressEl.style.display = 'none';
			const statusEl = item.createEl('div', {
				cls: 'vaultbot-pull-status', text: ''});
			const btn = item.createEl('button', {
				text: 'Download', cls: 'mod-cta'});
			btn.addEventListener('click', async () => {
				btn.setAttribute('disabled', 'disabled');
				btn.setText('Downloading...');
				progressEl.style.display = '';
				try {
					await fetch(this.settings.backendUrl + '/models/pull', {
						method: 'POST',
						headers: {'Content-Type': 'application/json'},
						body: JSON.stringify({model: m.name})
					});
				} catch (e) {
					statusEl.setText('Download failed: ' + (e.message || e));
					btn.removeAttribute('disabled');
					btn.setText('Download');
					progressEl.style.display = 'none';
					return;
				}
				// The actual progress comes over the WebSocket. We listen
				// for model_pull_progress + model_pull_done events by
				// patching the WS onmessage temporarily. This is simpler
				// than threading a callback through the view hierarchy.
				// The progress events are also handled by the sidebar's
				// WS handler, but we need a local listener here too so
				// this modal updates even if the sidebar isn't open.
				// We poll the model list instead — simpler + robust.
				const poll = window.setInterval(async () => {
					try {
						const r = await fetch(this.settings.backendUrl + '/models');
						const d = await r.json();
						const names = (d.models || []).map(
							x => typeof x === 'string' ? x : x.name);
						if (names.includes(m.name)) {
							window.clearInterval(poll);
							progressEl.value = 100;
							statusEl.setText('Done!');
							btn.setText('Installed ✓');
							if (onDone) try { onDone(); } catch (e) {}
							// Don't auto-close — let the user see success
							// and close manually, or download another.
						}
					} catch (e) {}
				}, 3000);
			});
		}
		const closeBtn = modal.contentEl.createEl('button', {text: 'Close'});
		closeBtn.style.marginTop = '16px';
		closeBtn.addEventListener('click', () => modal.close());
		modal.open();
	}

	async startMcpServerIfNeeded() {
		if (this.mcpProcess) return;
		try {
			// Use the single-flight ready promise instead of probing directly
			// — avoids ERR_CONNECTION_REFUSED console spam while the backend
			// is still booting.
			const running = await this.onceBackendReady();
			if (!running) {
				// Backend not ready yet; retry shortly.
				setTimeout(() => this.startMcpServerIfNeeded(), 5000);
				return;
			}
			let vaultRoot;
			if (this.app.vault.adapter.getBasePath) {
				vaultRoot = this.app.vault.adapter.getBasePath();
			} else {
				vaultRoot = this.app.vault.configDir.replace(/[\\/]\.obsidian[\\/]?$/, '');
			}
			const mcpPy = path.join(vaultRoot, 'vaultbot', 'vaultbot_backend', 'mcp_server.py');
			const fs = require('fs');
			const mcpPythonExe = this._venvPythonExe(vaultRoot);
			if (!fs.existsSync(mcpPythonExe) || !fs.existsSync(mcpPy)) {
				// Not set up yet -- don't crash, just skip silently. The backend
				// start path will show the setup modal if the venv is missing.
				return;
			}
			// Point the MCP server at this backend and spawn it detached.
			const env = Object.assign({}, process.env, {
				VAULTBOT_BACKEND_URL: this.settings.backendUrl
			});
			this.mcpProcess = spawn(mcpPythonExe, [mcpPy], {
				cwd: vaultRoot,
				detached: true,
				windowsHide: true,
				stdio: ['ignore', 'ignore', 'ignore'],
				env: env
			});
			this.mcpProcess.unref();
			console.log('VaultBot MCP server spawned (PID ' + this.mcpProcess.pid + ')');
			// Write an MCP client config so MCP-aware clients discover the tool.
			this.writeMcpClientConfig(vaultRoot, mcpPythonExe, mcpPy);
		} catch (e) {
			console.error('VaultBot MCP server spawn error:', e);
		}
	}

	stopMcpServer() {
		if (this.mcpProcess) {
			try { this.mcpProcess.kill(); } catch (e) {}
			this.mcpProcess = null;
		}
	}

	// Read the 256-bit shared-secret auth token that the backend generated
	// on first run. Returns '' if the token file doesn't exist or can't be
	// read (e.g. backend hasn't started yet). The token is sent as the
	// X-VaultBot-Token header on fetch() calls and as a ?token= query param
	// on sendBeacon() calls (sendBeacon can't set custom headers).
	_getAuthToken() {
		try {
			const fs = require('fs');
			const path = require('path');
			let vaultRoot;
			if (this.app.vault.adapter.getBasePath) {
				vaultRoot = this.app.vault.adapter.getBasePath();
			} else {
				vaultRoot = this.app.vault.configDir.replace(/[\\/]\.obsidian[\\/]?$/, '');
			}
			const tokenPath = path.join(vaultRoot, 'vaultbot', 'vaultbot_backend', '.vaultbot_auth_token');
			return fs.readFileSync(tokenPath, 'utf-8').trim();
		} catch (e) {
			return '';
		}
	}

	// Kill the backend process when Obsidian closes so nothing is left
	// running in the background. The backend writes its PID to
	// vaultbot_backend/vaultbot.pid on startup; we read that and taskkill it.
	// PRIMARY path: POST /shutdown — the backend self-terminates, which is
	// more reliable than taskkill fired from an Obsidian process that is
	// itself being torn down (onunload may race with window destruction and
	// taskkill may not complete). FALLBACK: if the HTTP call fails or the
	// backend doesn't die in time, taskkill the PID from the pid file.
	async stopBackend() {
		const fs = require('fs');
		const path = require('path');
		let vaultRoot;
		if (this.app.vault.adapter.getBasePath) {
			vaultRoot = this.app.vault.adapter.getBasePath();
		} else {
			vaultRoot = this.app.vault.configDir.replace(/[\\/]\.obsidian[\\/]?$/, '');
		}
		const pidFile = path.join(vaultRoot, 'vaultbot', 'vaultbot_backend', 'vaultbot.pid');

		// 1) Ask the backend to self-terminate. Best-effort, short timeout.
		try {
			const controller = new AbortController();
			const to = setTimeout(() => controller.abort(), 3000);
			const token = this._getAuthToken();
			const headers = {};
			if (token) headers['X-VaultBot-Token'] = token;
			await fetch(this.settings.backendUrl + '/shutdown', {
				method: 'POST',
				signal: controller.signal,
				headers,
			});
			clearTimeout(to);
		} catch (e) {
			// Expected if the backend is already gone or unreachable.
		}

		// 2) Wait briefly for the process to actually exit. Check the PID
		//    file instead of probing with fetch — the backend deletes
		//    vaultbot.pid on graceful shutdown (release_lock), so the file
		//    disappearing is a reliable signal. Using fetch here generates
		//    ERR_CONNECTION_REFUSED console spam that worries users.
		const waitMs = 1500;
		const start = Date.now();
		while (Date.now() - start < waitMs) {
			if (!fs.existsSync(pidFile)) break;
			await new Promise(r => setTimeout(r, 150));
		}

		if (fs.existsSync(pidFile)) {
			try {
				const pid = fs.readFileSync(pidFile, 'utf-8').trim();
				if (pid) {
					console.log('VaultBot: stopping backend PID ' + pid);
					try {
						require('child_process').execSync('taskkill /PID ' + pid + ' /T /F', {stdio: 'ignore'});
					} catch (e) {
						console.log('VaultBot: backend process may have already exited:', e.message);
					}
				}
				try { fs.unlinkSync(pidFile); } catch (e) {}
			} catch (e) {
				console.error('VaultBot: error reading pid file:', e);
			}
		}
	}

	// Restart the backend: stop it (self-shutdown + taskkill fallback) then
	// start it fresh. This is the one-click way for a non-tech user to pick
	// up code changes without typing anything. It reuses stopBackend() +
	// startBackendIfNeeded() so all the existing PID/log/handle logic
	// applies. `onProgress` is an optional callback that receives status
	// strings so a UI can show what's happening.
	async restartBackend(onProgress) {
		const notify = (msg) => {
			try { new Notice(msg); } catch (e) {}
			if (typeof onProgress === 'function') {
				try { onProgress(msg); } catch (e) {}
			}
		};
		notify('Restarting VaultBot backend...');
		// stopBackend() waits for the process to exit + taskkills the PID as
		// a hard fallback. Safe to call even if the backend is already down.
		await this.stopBackend();
		// Give the OS a moment to fully release the port (Windows sometimes
		// holds it briefly after the process exits).
		await new Promise(r => setTimeout(r, 1000));
		// startBackendIfNeeded() bails early if it thinks a backend is
		// already running, so make sure the running-check returns false by
		// the time we call it. If something is still up, warn + abort.
		if (await this.isBackendRunning()) {
			notify('Restart failed: backend still running after shutdown. Try again.');
			return false;
		}
		await this.startBackendIfNeeded();
		return await this.isBackendRunning();
	}

	// ─────────────────────────────────────────────────────────────────────
	// Check the latest version available on GitHub without applying it.
	// Fetches the repo's manifest.json from the raw GitHub URL and compares
	// its version field against the locally-installed manifest. Resolves to
	// {latest, current, updateAvailable} on success or {error} on failure.
	// ─────────────────────────────────────────────────────────────────────
	async checkLatestVersion(ref) {
		const fs = require('fs');
		const refSpec = (ref && String(ref).trim()) || 'main';
		let currentVersion = '?';
		try {
			const vaultRoot = this.app.vault.adapter.getBasePath
				? this.app.vault.adapter.getBasePath()
				: this.app.vault.configDir.replace(/[\\/]\.obsidian[\\/]?$/, '');
			const manPath = path.join(vaultRoot, '.obsidian', 'plugins', 'vaultbot', 'manifest.json');
			if (fs.existsSync(manPath)) {
				currentVersion = JSON.parse(fs.readFileSync(manPath, 'utf8')).version || '?';
			}
		} catch (e) {}
		// Use the GitHub API (works for branches + tags) to get the manifest.
		const apiUrl = `https://raw.githubusercontent.com/ziggibot-uni/vaultbot/${encodeURIComponent(refSpec)}/.obsidian/plugins/vaultbot/manifest.json`;
		try {
			const resp = await fetch(apiUrl, { cache: 'no-store' });
			if (!resp.ok) return { error: `GitHub returned ${resp.status}`, current: currentVersion };
			const manifest = await resp.json();
			const latest = manifest.version || '?';
			return {
				latest,
				current: currentVersion,
				updateAvailable: latest !== '?' && latest !== currentVersion
			};
		} catch (e) {
			return { error: e && e.message ? e.message : String(e), current: currentVersion };
		}
	}

	// ─────────────────────────────────────────────────────────────────────
	// Self-updater: pull the latest CODE from GitHub and apply it over the
	// live vault, WITHOUT touching any user state.
	//
	// What gets updated (code only):
	//   - vaultbot/vaultbot_backend/**/*.py  (the backend engine)
	//   - .obsidian/plugins/vaultbot/main.js   (this plugin file)
	//   - .obsidian/plugins/vaultbot/manifest.json
	//   - .obsidian/plugins/vaultbot/styles.css
	//
	// What is PRESERVED (never overwritten):
	//   - .obsidian/plugins/vaultbot/data.json (your keys, model, etc.)
	//   - every .md doc in the vault (notes, chat logs, research, textbooks)
	//   - vaultbot_backend/*_log.json, sessions/, checkpoints/,
	//     vaultbot_index/, *.log, *.pid,
	//     trash/, __pycache__/  — all runtime state stays put
	//
	// The backend is stopped first (Windows locks .py files while running),
	// the tarball is downloaded to a temp dir, code paths are extracted to a
	// staging dir, then copied over the live files. data.json is backed up
	// before the plugin files are touched and restored after, as a belt-and-
	// braces guard against the repo's default data.json sneaking in.
	//
	// `onProgress(statusString)` is optional; the UI uses it for a live line.
	// Resolves to {ok:true, version} on success or {ok:false, error} on failure.
	// ─────────────────────────────────────────────────────────────────────
	async performSelfUpdate(onProgress, ref) {
		const fs = require('fs');
		const os = require('os');
		const { execFile, execFileSync } = require('child_process');
		const notify = (msg) => {
			try { new Notice(msg); } catch (e) {}
			if (typeof onProgress === 'function') {
				try { onProgress(msg); } catch (e) {}
			}
		};

		let vaultRoot;
		if (this.app.vault.adapter.getBasePath) {
			vaultRoot = this.app.vault.adapter.getBasePath();
		} else {
			vaultRoot = this.app.vault.configDir.replace(/[\\/]\.obsidian[\\/]?$/, '');
		}

		const refSpec = (ref && String(ref).trim()) || 'main';
		// GitHub serves a tarball for any branch/tag/commit ref. The archive
		// prefix is always "<repo>-<ref-with-slashes-collapsed>/" — but the
		// safest way to find the prefix is to list the archive and read the
		// first path component. We do that after download.
		const tarballUrl = `https://github.com/ziggibot-uni/vaultbot/archive/refs/heads/${encodeURIComponent(refSpec)}.tar.gz`;
		// NOTE: for tags use .../archive/refs/tags/<ref>.tar.gz. We try heads
		// first; if GitHub 404s, retry as a tag so users can pin a release.

		const pluginDir = path.join(vaultRoot, '.obsidian', 'plugins', 'vaultbot');
		const backendDir = path.join(vaultRoot, 'vaultbot', 'vaultbot_backend');
		const dataJsonPath = path.join(pluginDir, 'data.json');
		const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vaultbot-update-'));
		const stagingDir = path.join(tmpDir, 'staging');
		const tarballPath = path.join(tmpDir, 'update.tar.gz');
		fs.mkdirSync(stagingDir, { recursive: true });

		const cleanup = () => {
			try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (e) {}
		};

		try {
			notify(`Stopping VaultBot before update…`);
			// Stop MCP + backend so Windows releases file locks on .py files.
			try { this.stopMcpServer(); } catch (e) {}
			await this.stopBackend();
			// Best-effort: make sure nothing is still up holding locks.
			await new Promise(r => setTimeout(r, 800));

			notify(`Downloading update from GitHub (${refSpec})…`);
			// Use curl.exe explicitly: PowerShell aliases `curl` to
			// Invoke-WebRequest, but we spawn a real shell so we control the
			// binary. execFileSync throws on non-zero exit, which a 404 is.
			let lastErr = null;
			for (const attempt of [tarballUrl, `https://github.com/ziggibot-uni/vaultbot/archive/refs/tags/${encodeURIComponent(refSpec)}.tar.gz`]) {
				try {
					execFileSync('curl.exe', ['-sL', '-o', tarballPath, attempt], { stdio: 'ignore' });
					if (fs.existsSync(tarballPath) && fs.statSync(tarballPath).size > 1000) {
						lastErr = null;
						break;
					}
					lastErr = lastErr || new Error('Tarball empty or missing for ' + attempt);
				} catch (e) {
					lastErr = e;
				}
			}
			if (lastErr) throw new Error('Could not download update from GitHub: ' + lastErr.message);

			notify(`Extracting update…`);
			// Extract ONLY code paths into staging. Exclusions guard against
			// the repo's tracked-but-state files (logs, *.json state, pid,
			// sessions, checkpoints, indexes, models, trash, pycache) ever
			// landing in the live vault. We also exclude data.json so the
			// repo's default plugin settings never clobber the user's keys.
			//
			// tar --exclude globs match against the FULL archive path
			// (including the vaultbot-main/ prefix), so we anchor patterns
			// with `*/`. We extract the whole archive minus exclusions into
			// staging, then copy only the two code trees we care about.
			const extractArgs = [
				'-xzf', tarballPath,
				'-C', stagingDir,
				'--exclude=*/.obsidian/plugins/vaultbot/data.json',
				'--exclude=*/vaultbot/vaultbot_backend/*.log',
				'--exclude=*/vaultbot/vaultbot_backend/*_log.json',
				'--exclude=*/vaultbot/vaultbot_backend/calibration_log.json',
				'--exclude=*/vaultbot/vaultbot_backend/claim_verification_log.json',
				'--exclude=*/vaultbot/vaultbot_backend/consolidation_log.json',
				'--exclude=*/vaultbot/vaultbot_backend/embedding_drift.json',
				'--exclude=*/vaultbot/vaultbot_backend/procedure_failure_log.json',
				'--exclude=*/vaultbot/vaultbot_backend/rag_eval_log.json',
				'--exclude=*/vaultbot/vaultbot_backend/touch_counts.json',
				'--exclude=*/vaultbot/vaultbot_backend/vaultbot.pid',
				'--exclude=*/vaultbot/vaultbot_backend/sessions',
				'--exclude=*/vaultbot/vaultbot_backend/sessions/*',
				'--exclude=*/vaultbot/vaultbot_backend/checkpoints',
				'--exclude=*/vaultbot/vaultbot_backend/checkpoints/*',
				'--exclude=*/vaultbot/vaultbot_backend/vaultbot_index',
				'--exclude=*/vaultbot/vaultbot_backend/vaultbot_index/*',
				'--exclude=*/vaultbot/vaultbot_backend/trash',
				'--exclude=*/vaultbot/vaultbot_backend/trash/*',
				'--exclude=*/vaultbot/vaultbot_backend/__pycache__',
				'--exclude=*/vaultbot/vaultbot_backend/__pycache__/*',
				'--exclude=*/vaultbot/vaultbot_backend/*/__pycache__',
				'--exclude=*/vaultbot/vaultbot_backend/*/__pycache__/*',
				'--exclude=*/vaultbot/vaultbot_backend/**/*.pyc'
			];
			execFileSync('tar.exe', extractArgs, { stdio: 'ignore' });

			// Find the single top-level archive prefix (e.g. "vaultbot-main").
			const entries = fs.readdirSync(stagingDir, { withFileTypes: true })
				.filter(d => d.isDirectory());
			if (entries.length !== 1) {
				throw new Error('Unexpected archive layout: expected one top-level folder, found ' + entries.length);
			}
			const archiveRoot = path.join(stagingDir, entries[0].name);

			notify(`Applying backend code…`);
			// Copy the backend code tree over the live one. We copy individual
			// tracked files rather than nuking the whole directory, so any
			// untracked local state files (sessions/, logs, models, etc.) that
			// the exclusions left untouched in the LIVE vault are preserved.
			const srcBackend = path.join(archiveRoot, 'vaultbot', 'vaultbot_backend');
			if (!fs.existsSync(srcBackend)) throw new Error('Archive has no vaultbot/vaultbot_backend/ folder.');
			await copyCodeTree(srcBackend, backendDir);

			notify(`Applying plugin files…`);
			// Back up the user's data.json + mcp.json first; restore after.
			// (data.json is excluded from extraction, but we defend in depth.)
			const backups = {};
			for (const name of ['data.json', 'mcp.json']) {
				const p = path.join(pluginDir, name);
				if (fs.existsSync(p)) {
					backups[name] = fs.readFileSync(p);
				}
			}
			const srcPlugin = path.join(archiveRoot, '.obsidian', 'plugins', 'vaultbot');
			if (!fs.existsSync(srcPlugin)) throw new Error('Archive has no plugin folder.');
			// Copy only code files from the archive's plugin dir. Never copy
			// data.json even if it somehow survived (it shouldn't).
			for (const name of ['main.js', 'manifest.json', 'styles.css', 'mcp.json']) {
				const src = path.join(srcPlugin, name);
				if (!fs.existsSync(src)) continue;
				fs.copyFileSync(src, path.join(pluginDir, name));
			}
			// Restore the user's preserved files.
			for (const [name, buf] of Object.entries(backups)) {
				fs.writeFileSync(path.join(pluginDir, name), buf);
			}

			// Read the new version for reporting.
			let newVersion = '?';
			try {
				const man = JSON.parse(fs.readFileSync(path.join(pluginDir, 'manifest.json'), 'utf8'));
				newVersion = man.version || '?';
			} catch (e) {}

			// Re-run pip install in case the update added new dependencies.
			try {
				const venvPython = this._venvPythonExe(vaultRoot);
				const reqPath = path.join(vaultRoot, 'vaultbot', 'vaultbot_backend', 'requirements.txt');
				if (fs.existsSync(venvPython) && fs.existsSync(reqPath)) {
					notify('Checking for new dependencies...');
					const { execFileSync } = require('child_process');
					execFileSync(venvPython, ['-m', 'pip', 'install', '-r', reqPath, '--quiet'], {
						cwd: vaultRoot, stdio: 'ignore', timeout: 120000,
					});
					notify('Dependencies updated.');
				}
			} catch (e) {
				console.warn('VaultBot: pip install during update failed (non-fatal):', e);
				notify('Could not check new dependencies - will try starting anyway.');
			}

			notify(`Update applied (v${newVersion}). Restarting backend…`);
			// Bring the backend + MCP back up so the user is not left dark.
			await this.startBackendIfNeeded();
			if (this.settings.autoStartMcpServer) {
				try { this.startMcpServerIfNeeded(); } catch (e) {}
			}

			notify(`VaultBot updated to v${newVersion} and restarted.`);
			return { ok: true, version: newVersion };
		} catch (err) {
			notify('Update failed: ' + (err && err.message ? err.message : String(err)));
			console.error('VaultBot self-update error:', err);
			// Best-effort recovery: restart the backend if it's down, since
			// we stopped it at the top. Code files are only overwritten on
			// success, so a mid-flight failure leaves the old code intact.
			try {
				if (!await this.isBackendRunning()) await this.startBackendIfNeeded();
				if (this.settings.autoStartMcpServer) {
					try { this.startMcpServerIfNeeded(); } catch (e) {}
				}
			} catch (e) {}
			return { ok: false, error: err && err.message ? err.message : String(err) };
		} finally {
			cleanup();
		}

		// ── helper: copy a code tree, overwriting changed files but leaving
		// any untracked local files in the destination intact. Recursively
		// walks the source dir; for each file, mkdirp the relative parent in
		// the destination and copyFileSync over it. Does NOT delete files that
		// exist in dest but not src — that's intentional, so user state files
		// (logs, sessions, models, agent-authored custom tools) that aren't in
		// the repo are kept.
		//
		// Before overwriting a file that differs from the archive version,
		// the previous local copy is saved to .vaultbot-update-backup/ with a
		// timestamp, so user/bot modifications are never silently lost.
		async function copyCodeTree(src, dest) {
			const backupDir = path.join(dest, '.vaultbot-update-backup');
			const ts = new Date().toISOString().replace(/[:.]/g, '-');
			let backupCount = 0;
			const stack = [{ s: src, d: dest, rel: '' }];
			while (stack.length) {
				const { s, d, rel } = stack.pop();
				fs.mkdirSync(d, { recursive: true });
				for (const entry of fs.readdirSync(s, { withFileTypes: true })) {
					const sp = path.join(s, entry.name);
					const dp = path.join(d, entry.name);
					const relPath = rel ? rel + '/' + entry.name : entry.name;
					if (entry.isDirectory()) {
						// Skip __pycache__ + backup dirs everywhere.
						if (entry.name === '__pycache__' || entry.name === '.vaultbot-update-backup') continue;
						stack.push({ s: sp, d: dp, rel: relPath });
					} else if (entry.isFile()) {
						// Skip compiled bytecode and stale .bak files.
						if (entry.name.endsWith('.pyc') || entry.name.endsWith('.bak')) continue;
						// If the destination file exists and differs from the
						// archive version, back it up before overwriting so
						// user/bot modifications are preserved.
						if (fs.existsSync(dp)) {
							let same = false;
							try {
								const srcStat = fs.statSync(sp);
								const dstStat = fs.statSync(dp);
								if (srcStat.size === dstStat.size) {
									const srcBuf = fs.readFileSync(sp);
									const dstBuf = fs.readFileSync(dp);
									same = srcBuf.equals(dstBuf);
								}
							} catch (e) {}
							if (!same) {
								fs.mkdirSync(path.join(backupDir, ts), { recursive: true });
								const backupPath = path.join(backupDir, ts, relPath.replace(/[\\/]/g, '__'));
								fs.mkdirSync(path.dirname(backupPath), { recursive: true });
								fs.copyFileSync(dp, backupPath);
								backupCount++;
							}
						}
						fs.copyFileSync(sp, dp);
					}
				}
			}
		}
	}

	writeMcpClientConfig(vaultRoot, venvPython, mcpPy) {
		// Write MCP client configs so tools like VS Code Copilot Chat and
		// Claude Desktop auto-discover the vault_research tool with zero
		// manual setup. We write to every location each client reads from.
		const fs = require('fs');
		const serverEntry = {
			command: venvPython,
			args: [mcpPy],
			env: { VAULTBOT_BACKEND_URL: this.settings.backendUrl }
		};

		// 1) VS Code Copilot Chat reads <workspace>/.vscode/mcp.json.
		//    Schema: { "servers": { "name": { command, args, env } } }
		try {
			const vscodeDir = path.join(vaultRoot, '.vscode');
			if (!fs.existsSync(vscodeDir)) fs.mkdirSync(vscodeDir, { recursive: true });
			fs.writeFileSync(
				path.join(vscodeDir, 'mcp.json'),
				JSON.stringify({ servers: { vaultbot: serverEntry } }, null, 2),
				'utf8'
			);
		} catch (e) {
			console.warn('VaultBot: could not write .vscode/mcp.json', e);
		}

		// 2) Claude Desktop reads %APPDATA%\Claude\claude_desktop_config.json.
		//    Schema: { "mcpServers": { "name": { command, args, env } } }
		try {
			const appData = process.env.APPDATA || path.join(process.env.HOME || '', 'AppData', 'Roaming');
			const claudeDir = path.join(appData, 'Claude');
			const claudePath = path.join(claudeDir, 'claude_desktop_config.json');
			let existing = {};
			if (fs.existsSync(claudePath)) {
				try { existing = JSON.parse(fs.readFileSync(claudePath, 'utf8')) || {}; } catch (e) {}
			}
			if (!fs.existsSync(claudeDir)) fs.mkdirSync(claudeDir, { recursive: true });
			existing.mcpServers = Object.assign({}, existing.mcpServers || {}, { vaultbot: serverEntry });
			fs.writeFileSync(claudePath, JSON.stringify(existing, null, 2), 'utf8');
		} catch (e) {
			console.warn('VaultBot: could not write claude_desktop_config.json', e);
		}

		// 3) Keep a plugin-local copy for reference / manual clients.
		try {
			fs.writeFileSync(
				path.join(vaultRoot, '.obsidian', 'plugins', 'vaultbot', 'mcp.json'),
				JSON.stringify({ mcpServers: { vaultbot: serverEntry } }, null, 2),
				'utf8'
			);
		} catch (e) {
			console.warn('VaultBot: could not write plugin mcp.json', e);
		}
	}

	async waitForBackend(timeoutMs = 30000, intervalMs = 500) {
		const start = Date.now();
		while (Date.now() - start < timeoutMs) {
			if (await this.isBackendRunning()) return true;
			await new Promise(r => setTimeout(r, intervalMs));
		}
		return await this.isBackendRunning();
	}


	// Read the last N lines of backend.log for diagnostics.
	_readBackendLog(vaultRoot, maxLines = 50) {
		const fs = require('fs');
		const logFile = path.join(vaultRoot, 'vaultbot', 'vaultbot_backend', 'backend.log');
		try {
			if (!fs.existsSync(logFile)) return '';
			const content = fs.readFileSync(logFile, 'utf8');
			const lines = content.split('\n');
			return lines.slice(-maxLines).join('\n');
		} catch (e) {
			return '';
		}
	}

	// Diagnose backend startup failure by reading backend.log.
	// Returns {title, message, remedy, action} or null if no pattern matched.
	_diagnoseStartupFailure(vaultRoot) {
		const log = this._readBackendLog(vaultRoot);
		if (!log) return null;

		const patterns = [
			{
				test: /faiss|numpy.*multiarray|numpy\.core\.size|undefined symbol.*faiss/i,
				title: 'Library version mismatch',
				message: 'VaultBot\'s search index library (FAISS) and numpy don\'t match versions. This can happen after an update.',
				remedy: 'Click "Repair libraries" below to reinstall the matching libraries automatically.',
				action: 'repair_faiss',
			},
			{
				test: /ModuleNotFoundError|ImportError|No module named/i,
				title: 'Missing Python packages',
				message: 'Some Python packages VaultBot needs are not installed. This can happen after an update adds new dependencies.',
				remedy: 'Click "Install packages" below to get everything needed.',
				action: 'install_deps',
			},
			{
				test: /Ollama|connection refused.*11434|ollama.*not/i,
				title: 'Ollama is not running',
				message: 'VaultBot needs Ollama running for its embedding model. Ollama doesn\'t seem to be started right now.',
				remedy: 'Open the Ollama app (look for its icon in your system tray or Start menu), then click "Restart backend" below.',
				action: 'restart_backend',
			},
			{
				test: /Address already in use|port.*8000|bind.*8000/i,
				title: 'Port 8000 is in use',
				message: 'Another program is using VaultBot\'s port (8000). This is usually a stale VaultBot process.',
				remedy: 'Click "Restart backend" below to stop the stale process and start fresh.',
				action: 'restart_backend',
			},
			{
				test: /Permission denied|Access denied|WinError 5/i,
				title: 'Permission issue',
				message: 'VaultBot can\'t access some of its files. This can happen if an antivirus or sync tool is locking files.',
				remedy: 'Try restarting your computer, then reopen Obsidian. If it persists, try moving your vault to a plain local folder.',
				action: 'none',
			},
		];

		for (const p of patterns) {
			if (p.test.test(log)) return p;
		}
		return null;
	}

	// Show a diagnostic modal when the backend fails to start.
	_showStartupFailureModal(vaultRoot) {
		const diagnosis = this._diagnoseStartupFailure(vaultRoot);
		const modal = new Modal(this.app);
		modal.titleEl.setText('VaultBot couldn\'t start');
		modal.titleEl.style.color = 'var(--text-error)';

		if (diagnosis) {
			const desc = modal.contentEl.createEl('p');
			desc.setText(diagnosis.message);
			desc.style.opacity = '0.85';
			desc.style.lineHeight = '1.5';

			const remedy = modal.contentEl.createEl('p');
			remedy.setText(diagnosis.remedy);
			remedy.style.opacity = '0.7';
			remedy.style.fontSize = '0.9em';

			if (diagnosis.action === 'repair_faiss') {
				const btn = modal.contentEl.createEl('button', {text: 'Repair libraries', cls: 'mod-cta'});
				btn.addEventListener('click', async () => {
					modal.close();
					await this._repairFaiss(vaultRoot);
				});
			} else if (diagnosis.action === 'install_deps') {
				const btn = modal.contentEl.createEl('button', {text: 'Install packages', cls: 'mod-cta'});
				btn.addEventListener('click', async () => {
					modal.close();
					await this._installDeps(vaultRoot);
				});
			} else if (diagnosis.action === 'restart_backend') {
				const btn = modal.contentEl.createEl('button', {text: 'Restart backend', cls: 'mod-cta'});
				btn.addEventListener('click', async () => {
					modal.close();
					await this.restartBackend();
				});
			}
		} else {
			const desc = modal.contentEl.createEl('p');
			desc.setText('VaultBot\'s backend started but didn\'t respond in time. This can happen for various reasons.');
			desc.style.opacity = '0.85';
			desc.style.lineHeight = '1.5';

			const logTail = this._readBackendLog(vaultRoot, 10);
			if (logTail) {
				const logEl = modal.contentEl.createEl('pre');
				logEl.setText(logTail);
				logEl.style.background = 'var(--background-secondary)';
				logEl.style.padding = '8px';
				logEl.style.borderRadius = '4px';
				logEl.style.fontSize = '11px';
				logEl.style.maxHeight = '150px';
				logEl.style.overflow = 'auto';
				logEl.style.whiteSpace = 'pre-wrap';
				logEl.style.wordBreak = 'break-all';
			}

			const remedy = modal.contentEl.createEl('p');
			remedy.setText('Try clicking "Restart backend" below. If it keeps happening, use the "Show setup instructions" command to re-run the installer.');
			remedy.style.opacity = '0.7';
			remedy.style.fontSize = '0.9em';
		}

		const restartBtn = modal.contentEl.createEl('button', {text: 'Restart backend', cls: 'mod-cta'});
		restartBtn.style.marginTop = '12px';
		restartBtn.addEventListener('click', async () => {
			modal.close();
			await this.restartBackend();
		});

		modal.open();
	}

	// Repair faiss/numpy ABI mismatch by force-reinstalling both.
	async _repairFaiss(vaultRoot) {
		const { execFile } = require('child_process');
		const venvPython = this._venvPythonExe(vaultRoot);
		const notify = (msg) => { try { new Notice(msg); } catch (e) {} };
		notify('Repairing FAISS + numpy... this takes a minute.');
		try {
			execFile(venvPython, ['-m', 'pip', 'install', '--force-reinstall', 'faiss-cpu>=1.11.0', 'numpy>=2.0.0'], {
				cwd: vaultRoot, stdio: 'inherit',
			}, (err) => {
				if (err) {
					notify('Repair failed: ' + err.message + '. Try re-running the installer.');
				} else {
					notify('Libraries repaired. Restarting backend...');
					this.restartBackend();
				}
			});
		} catch (e) {
			notify('Repair failed: ' + e.message);
		}
	}

	// Re-run pip install to get any new dependencies.
	async _installDeps(vaultRoot) {
		const { execFile } = require('child_process');
		const venvPython = this._venvPythonExe(vaultRoot);
		const reqPath = path.join(vaultRoot, 'vaultbot', 'vaultbot_backend', 'requirements.txt');
		const notify = (msg) => { try { new Notice(msg); } catch (e) {} };
		notify('Installing missing packages... this can take a few minutes.');
		try {
			execFile(venvPython, ['-m', 'pip', 'install', '-r', reqPath], {
				cwd: vaultRoot, stdio: 'inherit',
			}, (err) => {
				if (err) {
					notify('Install failed: ' + err.message + '. Try re-running the installer.');
				} else {
					notify('Packages installed. Restarting backend...');
					this.restartBackend();
				}
			});
		} catch (e) {
			notify('Install failed: ' + e.message);
		}
	}

	async startBackendIfNeeded() {
		if (this.backendStarting) {
			new Notice('VaultBot backend is already starting...');
			return;
		}

		this.backendStarting = true;

		try {
			// Check if the backend is already running via the PID file first.
			// The backend writes vaultbot.pid on startup and deletes it on
			// graceful shutdown. If the file exists, the backend is likely up
			// — confirm with a fetch probe (which succeeds silently). If the
			// file does NOT exist, the backend is definitely down, so we skip
			// the fetch entirely and go straight to spawning. This avoids the
			// ERR_CONNECTION_REFUSED console spam that Chromium logs for every
			// failed fetch, regardless of try/catch.
			let vaultRoot;
			if (this.app.vault.adapter.getBasePath) {
				vaultRoot = this.app.vault.adapter.getBasePath();
			} else {
				vaultRoot = this.app.vault.configDir.replace(/[\\/].obsidian[\\/]?$|^/, '');
			}
			const pidFile = path.join(vaultRoot, 'vaultbot', 'vaultbot_backend', 'vaultbot.pid');
			const fs = require('fs');
			let running = false;
			if (fs.existsSync(pidFile)) {
				// PID file exists — backend may be running. Probe to confirm.
				running = await this.isBackendRunning();
			}
			if (running) {
				new Notice('VaultBot backend is already running.');
				return;
			}

			new Notice('Starting VaultBot backend...');

			const mainPy = path.join(vaultRoot, 'vaultbot', 'vaultbot_backend', 'main.py');
			const logFile = path.join(vaultRoot, 'vaultbot', 'vaultbot_backend', 'backend.log');

			const pythonExe = this._venvPythonExe(vaultRoot);
			if (!fs.existsSync(pythonExe) || !fs.existsSync(mainPy)) {
				// VaultBot isn't set up yet (no venv or no backend code). Don't
				// crash -- show a friendly modal with the one-liner install
				// command so the user knows exactly what to do.
				this._showSetupNeededModal();
				return;
			}

// Open the log file in append mode. The running backend inherits the
		// handle and keeps it open, so a second spawn attempt can hit EBUSY on
		// Windows. Fall back to a unique timestamped log file (or ignoring stdio)
		// so a busy log never crashes the plugin.
		let out = 'ignore';
		let err = 'ignore';
		let openedHandles = [];
		try {
			const fd = fs.openSync(logFile, 'a');
			out = fd;
			err = fd;
			openedHandles = [fd];
		} catch (e) {
			const stamp = new Date().toISOString().replace(/[:.]/g, '-');
			const altLog = path.join(vaultRoot, 'vaultbot', 'vaultbot_backend', `backend-${stamp}.log`);
			try {
				const fd = fs.openSync(altLog, 'a');
				out = fd;
				err = fd;
				openedHandles = [fd];
				console.warn('VaultBot: backend.log busy, using ' + altLog);
			} catch (e2) {
				console.warn('VaultBot: could not open any log file, stdio ignored', e2);
			}
		}

		const backendProcess = spawn(pythonExe, [mainPy], {
			cwd: vaultRoot,
			detached: true,
			windowsHide: true,
			stdio: ['ignore', out, err],
			env: Object.assign({}, process.env, {
				VAULTBOT_RESEARCH_BACKEND: this.settings.researchBackend || 'tavily',
					// Use the plugin's saved key if set; otherwise pass an empty
					// string and let the backend load it from .env via load_dotenv.
					// This fixes the case where the key was set in .env but not
					// in the plugin settings — the backend's load_dotenv('../.env')
					// will pick it up.
					TAVILY_API_KEY: this.settings.tavilyApiKey || process.env.TAVILY_API_KEY || '',
					// Pass the allowContributions setting so the submit_contribution
					// tool can check it without reading data.json directly.
					VAULTBOT_ALLOW_CONTRIBUTIONS: this.settings.allowContributions ? 'true' : 'false',
				VAULTBOT_SAFE_MODE: this.settings.safeMode !== false ? 'true' : 'false',
				VAULTBOT_ALLOW_WEB_RESEARCH: this.settings.allowWebResearch !== false ? 'true' : 'false',
				VAULTBOT_BS_DETECTOR_MESSAGES: this.settings.bsDetectorMessages !== false ? 'true' : 'false',
				})
		});
		backendProcess.unref();

		// Store the PID so stopBackend() can kill it on Obsidian close.
		this.backendPid = backendProcess.pid;

		openedHandles.forEach(fd => {
			try {
				fs.writeSync(fd, `\n[${new Date().toISOString()}] VaultBot backend spawned (PID ${backendProcess.pid})\n`);
			} catch (e) {}
			try { fs.closeSync(fd); } catch (e) {}
		});

			new Notice('VaultBot backend launched; waiting for it to be ready...');
			// Use the single-flight ready promise so concurrent callers
			// (sidebar ensureConnection, refreshModels) don't each open
			// their own fetch loop and flood the console.
			running = await this.onceBackendReady();
			if (!running) {
				// The backend may have bind-failed because port 8000 was still
				// in TIME_WAIT after a rapid plugin reload killed the previous
				// instance. Wait briefly for the OS to release the port, then
				// retry the whole spawn sequence once before giving up.
				new Notice('Backend did not come up; retrying in 3s...');
				await new Promise(r => setTimeout(r, 3000));
				if (await this.isBackendRunning()) {
					running = true;
				} else {
					this._showStartupFailureModal(vaultRoot);
				return;
				}
			}
			new Notice('VaultBot backend is ready.');
		} catch (err) {
			if (err && err.message && err.message.includes('did not respond')) {
				this._showStartupFailureModal(vaultRoot);
			} else {
				new Notice('Failed to start VaultBot backend: ' + err.message);
			}
			console.error('VaultBot backend spawn error:', err);
		} finally {
			this.backendStarting = false;
		}
	}
}


module.exports = VaultBotPlugin;


class VaultBotSettingTab extends PluginSettingTab {
	constructor(app, plugin) {
		super(app, plugin);
		this.plugin = plugin;
	}

	async display() {
		const {containerEl} = this;
		containerEl.empty();

		containerEl.createEl('h2', {text: 'VaultBot Settings'});

		// Backend URL moved to the Advanced disclosure at the bottom of
		// the settings tab — a non-tech user should never need to change it.
		// The config status panel below shows effective values instead.

		new Setting(containerEl)
			.setName('Auto-start backend')
			.setDesc('Start the VaultBot Python backend automatically when Obsidian opens')
			.addToggle(toggle => toggle
				.setValue(this.plugin.settings.autoStartBackend)
				.onChange(async (value) => {
					this.plugin.settings.autoStartBackend = value;
					await this.plugin.saveSettings();
				}));

		new Setting(containerEl)
			.setName('Auto-start MCP server')
			.setDesc('Start the VaultBot MCP server (vault_research tool) when Obsidian opens, so MCP clients like Copilot Chat get a research tool grounded in this vault')
			.addToggle(toggle => toggle
				.setValue(this.plugin.settings.autoStartMcpServer)
				.onChange(async (value) => {
					this.plugin.settings.autoStartMcpServer = value;
					await this.plugin.saveSettings();
					if (value) {
						this.plugin.startMcpServerIfNeeded();
					} else {
						this.plugin.stopMcpServer();
					}
				}));

		// ── AI Models & Providers (the interchangeable "pot") ────────────
		// One places to add provider connections (one-time API key) and
		// register models. Once a model is in the pot, ANY role (big / small /
		// vision) can use it — picked from the header dropdowns or here. Local
		// Ollama, OpenRouter, OpenAI, etc. all behave the same.
		containerEl.createEl('h3', {text: 'AI Models & Providers'});
		containerEl.createEl('div', {text:
			'Add a provider once (paste its API key), then add its models. ' +
			'Every model you add appears in the Big / Small / Vision pickers in ' +
			'the sidebar — assign any model to any role, across providers.',
			attr: {style: 'opacity:0.7;font-size:0.85em;margin:2px 0 10px 0;'}});

		const provStatusEl = containerEl.createEl('div', {attr: {style: 'opacity:0.75;font-size:0.85em;min-height:1.1em;margin-bottom:8px;'}});

		// Provider list (each shows label + base_url + whether a key is set).
		const provListEl = containerEl.createDiv({attr: {style: 'margin-bottom:8px;'}});

		// Add-provider form: a provider preset (or custom) + model id + key.
		const newProvRow = containerEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:4px;'}});
		newProvRow.createEl('span', {text: 'Provider', attr: {style: 'min-width:64px;font-size:0.85em;'}});
		const provPresetSel = newProvRow.createEl('select');
		provPresetSel.style.minWidth = '150px';

		const provUrlInput = newProvRow.createEl('input', {type: 'text', attr: {placeholder: 'base URL (auto-filled)', style: 'flex:1;min-width:170px;'}});
		const provKeyInput = newProvRow.createEl('input', {type: 'password', attr: {placeholder: 'API key (blank for local Ollama)', style: 'flex:1;min-width:170px;'}});
		const addProvBtn = newProvRow.createEl('button', {text: 'Add provider', cls: 'mod-cta'});

		// Add-model form: pick one of the configured providers, name the model.
		const newModelRow = containerEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:4px;'}});
		newModelRow.createEl('span', {text: 'Model', attr: {style: 'min-width:64px;font-size:0.85em;'}});
		const modelProvSel = newModelRow.createEl('select');
		modelProvSel.style.minWidth = '150px';
		// Live-model dropdown: populated from the provider's real /models list
		// (fetched on provider change). Picks a model WITHOUT typing its id.
		const liveModelSel = newModelRow.createEl('select');
		liveModelSel.style.minWidth = '170px';
		const modelIdInput = newModelRow.createEl('input', {type: 'text', attr: {placeholder: 'or type a model id', style: 'flex:1;min-width:120px;'}});
		const visionChk = newModelRow.createEl('input', {type: 'checkbox'});
		newModelRow.createEl('span', {text: 'vision', attr: {style: 'font-size:0.8em;opacity:0.7;'}});
		const testModelBtn = newModelRow.createEl('button', {text: 'Test'});
		const addModelBtn = newModelRow.createEl('button', {text: 'Add model', cls: 'mod-cta'});

		// Fetch the provider's live model list into the dropdown (called on
		// provider change + after adding a provider). Picks the first model.
		const loadLiveModels = async () => {
			const pid = modelProvSel.value;
			liveModelSel.empty();
			if (!pid) { liveModelSel.createEl('option', {text: '(add a provider first)', attr: {disabled: true}}); return; }
			liveModelSel.createEl('option', {text: 'loading…', attr: {disabled: true}});
			const res = await this.plugin.fetchProviderLiveModels(pid);
			liveModelSel.empty();
			const models = (res && Array.isArray(res.models)) ? res.models : [];
			if (!models.length) {
				liveModelSel.createEl('option', {text: res && res.detail ? `⚠ ${res.detail}` : '(none found — type manually)', attr: {disabled: true}});
				return;
			}
			models.forEach(m => {
				const name = typeof m === 'string' ? m : m.name;
				const opt = liveModelSel.createEl('option', {text: (m.vision ? '👁 ' : '') + name, attr: {value: name}});
				if (m.vision) opt.setAttribute('data-vision', '1');
			});
			// Auto-check vision if the selected model is vision-capable.
			const syncVision = () => {
				const opt = liveModelSel.options[liveModelSel.selectedIndex];
				visionChk.checked = !!(opt && opt.getAttribute('data-vision'));
			};
			liveModelSel.onchange = syncVision;
			syncVision();
		};
		modelProvSel.addEventListener('change', loadLiveModels);

		// Test-model: does this model actually respond on the endpoint? Shows
		// the model's own reply (e.g. the color it saw → proves vision works).
		testModelBtn.addEventListener('click', async () => {
			const providerId = modelProvSel.value;
			const modelId = modelIdInput.value.trim() || liveModelSel.value;
			if (!providerId || !modelId) { provStatusEl.setText('Pick a provider and a model first.'); return; }
			provStatusEl.setText(`Testing ${modelId}...`);
			const r = await fetch(this.plugin.settings.backendUrl + '/llm/test_model', {
				method: 'POST', headers: {'Content-Type': 'application/json'},
				body: JSON.stringify({provider_id: providerId, model: modelId, vision: visionChk.checked})});
			const data = await r.json().catch(() => ({}));
			if (data.ok) {
				provStatusEl.setText(`✓ ${modelId} works${visionChk.checked ? ' (vision confirmed)' : ''}: "${(data.response || '').slice(0, 40)}"`);
			} else {
				provStatusEl.setText(`✗ ${modelId}: ${data.error || data.detail || 'no response'}`);
			}
		});

		// The pot: one row per model with provider + role tags + remove button.
		const potListEl = containerEl.createDiv({attr: {style: 'margin-bottom:8px;'}});

		const refreshPotUI = async () => {
			const prov = await this.plugin.fetchProviders();
			const all = await this.plugin.fetchAllModels();
			// Rebuild provider preset dropdown from the backend's known presets.
			if (prov && prov.known) {
				const curPreset = provPresetSel.value;
				provPresetSel.empty();
				Object.entries(prov.known).forEach(([id, info]) => {
					provPresetSel.createEl('option', {text: info.label || id, attr: {value: id, 'data-url': info.base_url || ''}});
				});
				if (curPreset) provPresetSel.value = curPreset;
				// Fill the URL field from the selected preset.
				const fillUrl = () => {
					const opt = provPresetSel.options[provPresetSel.selectedIndex];
					if (opt && opt.getAttribute('data-url')) provUrlInput.value = opt.getAttribute('data-url');
				};
				fillUrl();
				provPresetSel.onchange = fillUrl;
			}
			// Existing providers list.
			provListEl.empty();
			const providers = (prov && Array.isArray(prov.providers)) ? prov.providers : [];
			if (!providers.length) {
				provListEl.createEl('div', {text: 'No providers yet — add one above (local Ollama is preset with no key).', attr: {style: 'opacity:0.65;font-size:0.8em;'}});
			}
			providers.forEach(p => {
				const row = provListEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:8px;margin:2px 0;'}});
				row.createEl('span', {text: `${p.label || p.id} — ${p.base_url}${p.has_key ? ' 🔑' : ''}`, attr: {style: 'flex:1;font-size:0.85em;'}});
				const rm = row.createEl('button', {text: 'Remove'});
				rm.addEventListener('click', async () => {
					await this.plugin.removeProviderCfg(p.id);
					new Notice(`Provider removed: ${p.label || p.id}`);
					refreshPotUI();
				});
			});
			// Model provider dropdown mirrors existing providers.
			modelProvSel.empty();
			providers.forEach(p => modelProvSel.createEl('option', {text: p.label || p.id, attr: {value: p.id}}));
			loadLiveModels();
			// The pot list.
			potListEl.empty();
			const models = (all && Array.isArray(all.models)) ? all.models : [];
			if (!models.length) {
				potListEl.createEl('div', {text: 'No models in the pot yet — add one above.', attr: {style: 'opacity:0.65;font-size:0.8em;'}});
			}
			models.forEach(m => {
				const row = potListEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:8px;margin:2px 0;'}});
				const tags = [];
				if (m.vision) tags.push('👁');
				if (m.roles && m.roles.length) tags.push('[' + m.roles.join(',') + ']');
				row.createEl('span', {text: `${tags.length ? tags.join(' ') + ' ' : ''}${m.model}`, attr: {style: 'flex:1;font-size:0.85em;'}, title: `${m.provider_label || m.provider}`});
				row.createEl('span', {text: m.provider_label || m.provider, attr: {style: 'font-size:0.75em;opacity:0.6;'}});
				const rm = row.createEl('button', {text: 'Remove'});
				rm.addEventListener('click', async () => {
					await this.plugin.removeModelCfg(m.id);
					new Notice(`Removed ${m.model}`);
					refreshPotUI();
				});
			});
			provStatusEl.setText(providers.length
				? `${providers.length} provider(s), ${models.length} model(s) in the pot.`
				: 'Add a provider to begin.');
			// ALSO refresh the tournament staging provider dropdown from the
			// SAME providers list (no duplicate fetch). This keeps the staging
			// section in sync with the main pot whenever providers change.
			if (typeof stagingProvSel !== 'undefined') {
				const stagingCur = stagingProvSel.value;
				stagingProvSel.empty();
				providers.forEach(p => stagingProvSel.createEl('option', {text: p.label || p.id, attr: {value: p.id}}));
				if (stagingCur && providers.find(p => p.id === stagingCur)) stagingProvSel.value = stagingCur;
			}
		};

		addProvBtn.addEventListener('click', async () => {
			provStatusEl.setText('Testing endpoint...');
			const opt = provPresetSel.options[provPresetSel.selectedIndex];
			const id = opt ? opt.value : 'custom';
			const url = provUrlInput.value.trim();
			const res = await this.plugin.addProviderCfg({
				id, type: (id.includes('ollama') ? 'ollama' : 'openai'),
				baseUrl: url, apiKey: provKeyInput.value.trim(),
				label: opt ? opt.text : id});
			if (res && res.status === 'ok') {
				provKeyInput.value = '';
				const n = res.probe ? res.probe.count : 0;
				new Notice(`Provider connected: ${opt ? opt.text : id} (${n} model${n === 1 ? '' : 's'} found)`);
				provStatusEl.setText(`✓ connected — ${n} model(s) available.`);
				refreshPotUI();
			} else {
				provStatusEl.setText(`✗ ${res && res.detail ? res.detail : 'endpoint test failed'} — not saved.`);
			}
		});

		addModelBtn.addEventListener('click', async () => {
			const providerId = modelProvSel.value;
			const modelId = modelIdInput.value.trim() || liveModelSel.value;
			if (!providerId) { provStatusEl.setText('Add a provider first.'); return; }
			if (!modelId) { provStatusEl.setText('Pick or type a model id.'); return; }
			provStatusEl.setText('Adding model...');
			const res = await this.plugin.addModelCfg({
				model: modelId, provider: providerId, vision: visionChk.checked});
			if (res && res.status === 'ok') {
				modelIdInput.value = '';
				visionChk.checked = false;
				new Notice(`Model added to the pot: ${modelId}`);
				refreshPotUI();
			} else {
				provStatusEl.setText('Failed — pick a provider and a model.');
			}
		});
		refreshPotUI();
		// ── Model Tournament ──────────────────────────────────────────
		// A separate "staging pot" for models you want to evaluate BEFORE
		// adding to the main pot. Add models by picking a provider (from
		// the SAME provider list you already configured above) and typing
		// the model name. Then run a tournament to see how they perform.
		containerEl.createEl('h3', {text: 'Model Tournament'});
		containerEl.createEl('div', {text:
			'Add models to the tournament staging pot below — these are ' +
			'separate from your main model pot. Test them against vaultbot-' +
			'specific benchmarks, then promote the winners to your main pot.',
			attr: {style: 'opacity:0.7;font-size:0.85em;margin:2px 0 10px 0;'}});

		// ── Add to staging: reuses the SAME provider dropdown data as the
		// main pot (no duplicate fetch). The provider list is already loaded
		// by refreshPotUI() in the AI Models & Providers section above.
		const stagingAddRow = containerEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:6px;'}});
		stagingAddRow.createEl('span', {text: 'Add model:', attr: {style: 'font-weight:600;font-size:0.85em;'}});
		const stagingProvSel = stagingAddRow.createEl('select');
		stagingProvSel.style.minWidth = '140px';
		// Live-model dropdown: populated from the provider's real /models list
		// so the user picks without typing.
		const stagingLiveSel = stagingAddRow.createEl('select');
		stagingLiveSel.style.minWidth = '160px';
		stagingLiveSel.createEl('option', {text: '(pick provider first)', attr: {disabled: true}});
		const stagingModelInput = stagingAddRow.createEl('input', {type: 'text', attr: {placeholder: 'or type model name', style: 'flex:1;min-width:140px;'}});
		const stagingAddBtn = stagingAddRow.createEl('button', {text: 'Add to staging', cls: 'mod-cta'});
		const stagingStatusEl = stagingAddRow.createEl('span', {attr: {style: 'opacity:0.7;font-size:0.8em;'}});

		// Load live models from the selected provider (same endpoint the
		// main pot's live-model dropdown uses).
		const loadStagingLiveModels = async () => {
			const pid = stagingProvSel.value;
			stagingLiveSel.empty();
			if (!pid) {
				stagingLiveSel.createEl('option', {text: '(pick provider first)', attr: {disabled: true}});
				return;
			}
			stagingLiveSel.createEl('option', {text: 'loading...', attr: {disabled: true}});
			const res = await this.plugin.fetchProviderLiveModels(pid);
			stagingLiveSel.empty();
			const models = (res && Array.isArray(res.models)) ? res.models : [];
			if (!models.length) {
				stagingLiveSel.createEl('option', {text: '(none found — type manually)', attr: {disabled: true}});
				return;
			}
			models.forEach(m => {
				const name = typeof m === 'string' ? m : m.name;
				stagingLiveSel.createEl('option', {text: name, attr: {value: name}});
			});
		};
		stagingProvSel.addEventListener('change', loadStagingLiveModels);

		// Staging pot list — checkboxes for each entry
		const stagingListEl = containerEl.createDiv({attr: {style: 'max-height:180px;overflow-y:auto;border:1px solid var(--background-modifier-border);border-radius:4px;padding:6px;margin-bottom:6px;'}});
		const stagingBtnRow = containerEl.createDiv({attr: {style: 'display:flex;gap:8px;margin-bottom:8px;'}});
		const stagingSelectAllBtn = stagingBtnRow.createEl('button', {text: 'Select all'});
		const stagingDeselectAllBtn = stagingBtnRow.createEl('button', {text: 'Deselect all'});
		const stagingClearBtn = stagingBtnRow.createEl('button', {text: 'Clear staging'});

		// Role selector + run
		const tourneyRoleRow = containerEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:8px;margin-bottom:8px;'}});
		tourneyRoleRow.createEl('span', {text: 'Role:', attr: {style: 'font-weight:600;'}});
		const tourneyRoleSel = tourneyRoleRow.createEl('select');
		tourneyRoleSel.createEl('option', {text: 'Big (reasoning, synthesis, planning)', attr: {value: 'big'}});
		tourneyRoleSel.createEl('option', {text: 'Small (classification, extraction, tagging)', attr: {value: 'small'}});

		const tourneyRunRow = containerEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:8px;margin-bottom:8px;'}});
		const tourneyRunBtn = tourneyRunRow.createEl('button', {text: 'Run Tournament', cls: 'mod-cta'});
		const tourneyStatusEl = tourneyRunRow.createEl('span', {attr: {style: 'opacity:0.7;font-size:0.85em;'}});

		// Results area
		const tourneyResultsEl = containerEl.createDiv({attr: {style: 'margin-top:8px;'}});

		// Refresh the staging list from the backend
		const refreshStagingList = async () => {
			stagingListEl.empty();
			stagingListEl.createEl('div', {text: 'Loading...', attr: {style: 'opacity:0.5;font-size:0.85em;padding:4px;'}});
			try {
				const r = await fetch(this.plugin.settings.backendUrl + '/tournament/staging');
				const data = await r.json();
				const entries = (data && Array.isArray(data.entries)) ? data.entries : [];
				stagingListEl.empty();
				if (!entries.length) {
					stagingListEl.createEl('div', {text: 'No models in staging. Add some above.', attr: {style: 'opacity:0.6;font-size:0.85em;padding:4px;'}});
					return;
				}
				// Fetch sizes in parallel
				let sizes = {};
				try {
					const sr = await fetch(this.plugin.settings.backendUrl + '/tournament/staging/sizes');
					const sd = await sr.json();
					sizes = sd.sizes || {};
				} catch (e) { /* sizes unavailable — show without */ }

				entries.forEach(e => {
					const row = stagingListEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:6px;padding:2px 0;'}});
					const cb = row.createEl('input', {type: 'checkbox', attr: {value: e.id, 'data-model': e.model, 'data-provider': e.provider}});
					const sizeStr = sizes[e.id] ? ` (${sizes[e.id]})` : '';
					row.createEl('label', {text: `${e.model}${sizeStr}`, attr: {style: 'font-size:0.85em;'}});
					row.createEl('span', {text: e.provider_label || e.provider, attr: {style: 'font-size:0.7em;opacity:0.5;'}});
					const rm = row.createEl('button', {text: '✕', attr: {style: 'font-size:0.7em;padding:0 4px;'}});
					rm.addEventListener('click', async () => {
						await fetch(this.plugin.settings.backendUrl + '/tournament/staging/' + encodeURIComponent(e.id), {method: 'DELETE'});
						refreshStagingList();
					});
				});
			} catch (e) {
				stagingListEl.empty();
				stagingListEl.createEl('div', {text: 'Backend offline — start the backend to manage staging.', attr: {style: 'opacity:0.5;font-size:0.85em;padding:4px;'}});
			}
		};

		// Add to staging
		stagingAddBtn.addEventListener('click', async () => {
			const provider = stagingProvSel.value;
			const model = stagingModelInput.value.trim() || stagingLiveSel.value;
			if (!provider) { stagingStatusEl.setText('Pick a provider.'); return; }
			if (!model) { stagingStatusEl.setText('Pick or type a model name.'); return; }
			stagingStatusEl.setText('Adding...');
			const r = await fetch(this.plugin.settings.backendUrl + '/tournament/staging', {
				method: 'POST', headers: {'Content-Type': 'application/json'},
				body: JSON.stringify({model, provider})});
			const data = await r.json();
			if (data.status === 'ok') {
				stagingModelInput.value = '';
				stagingStatusEl.setText('Added!');
				new Notice(`Added ${model} to tournament staging.`);
				refreshStagingList();
			} else {
				stagingStatusEl.setText('Failed: ' + (data.detail || 'error'));
			}
		});

		stagingSelectAllBtn.addEventListener('click', () => {
			stagingListEl.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = true);
		});
		stagingDeselectAllBtn.addEventListener('click', () => {
			stagingListEl.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = false);
		});
		stagingClearBtn.addEventListener('click', async () => {
			await fetch(this.plugin.settings.backendUrl + '/tournament/staging/clear', {method: 'POST'});
			refreshStagingList();
			new Notice('Tournament staging cleared.');
		});

		let tourneyRunning = false;
		tourneyRunBtn.addEventListener('click', async () => {
			if (tourneyRunning) return;
			const checked = stagingListEl.querySelectorAll('input[type=checkbox]:checked');
			const contestants = Array.from(checked).map(cb => ({
				model_id: cb.value,
				model_name: cb.getAttribute('data-model'),
				provider_id: cb.getAttribute('data-provider')
			}));
			if (!contestants.length) {
				tourneyStatusEl.setText('Select at least one model from staging.');
				return;
			}
			const role = tourneyRoleSel.value;
			tourneyRunning = true;
			tourneyRunBtn.setAttribute('disabled', 'disabled');
			tourneyRunBtn.setText('Running...');
			tourneyStatusEl.setText(`Testing ${contestants.length} model(s) on ${role} benchmarks...`);
			tourneyResultsEl.empty();
			tourneyResultsEl.createEl('div', {text: 'Running tournament — this may take a few minutes. Results will appear here when done.', attr: {style: 'opacity:0.7;font-size:0.85em;'}});

			try {
				const r = await fetch(this.plugin.settings.backendUrl + '/tournament/run', {
					method: 'POST', headers: {'Content-Type': 'application/json'},
					body: JSON.stringify({contestants, role})});
				const result = await r.json();
				tourneyResultsEl.empty();

				if (result.status !== 'ok') {
					tourneyResultsEl.createEl('div', {text: 'Tournament failed: ' + (result.detail || 'unknown error'), attr: {style: 'color:var(--text-error);'}});
					tourneyStatusEl.setText('Failed.');
					return;
				}

				tourneyStatusEl.setText(`Done in ${result.duration_s}s. Judge: ${result.judge_model}.`);

				// ── Results: bar chart visualization ──────────────────
				// Sort by combined score (accuracy 70% + speed 30%) descending
				const sorted = (result.models || []).slice().sort((a, b) => b.combined_score - a.combined_score);
				const maxCombined = sorted.length ? sorted[0].combined_score : 1;

				// Summary line
				const summaryRow = tourneyResultsEl.createDiv({attr: {style: 'display:flex;gap:12px;flex-wrap:wrap;margin-bottom:10px;font-size:0.8em;opacity:0.7;'}});
				summaryRow.createEl('span', {text: `🏆 ${sorted[0]?.model_name || '—'} wins`});
				summaryRow.createEl('span', {text: `📊 ${result.benchmarks?.length || 0} benchmarks`});
				summaryRow.createEl('span', {text: `⚖️ Score = 70% accuracy + 30% speed`});

				// Bar chart — one row per model
				const chartEl = tourneyResultsEl.createDiv({attr: {style: 'margin-bottom:10px;'}});
				sorted.forEach((m, i) => {
					const row = chartEl.createDiv({attr: {style: 'margin-bottom:6px;'}});
					// Model name + rank
					const labelRow = row.createDiv({attr: {style: 'display:flex;align-items:baseline;gap:6px;margin-bottom:2px;'}});
					const rank = labelRow.createEl('span', {text: `#${i + 1}`, attr: {style: 'font-weight:700;font-size:0.85em;color:var(--text-accent);min-width:24px;'}});
					labelRow.createEl('span', {text: m.model_name, attr: {style: 'font-weight:600;font-size:0.9em;'}});
					if (m.error) {
						labelRow.createEl('span', {text: '⚠ ' + m.error, attr: {style: 'color:var(--text-error);font-size:0.75em;'}});
					}
					// Score numbers
					const pct = (m.combined_score * 100).toFixed(0);
					const accPct = (m.overall_score * 100).toFixed(0);
					const timeStr = m.avg_latency_ms ? (m.avg_latency_ms / 1000).toFixed(1) + 's' : '—';
					labelRow.createEl('span', {text: `${pct}%`, attr: {style: 'font-weight:700;font-size:0.9em;margin-left:auto;'}});
					labelRow.createEl('span', {text: `acc ${accPct}% · ${timeStr}/q`, attr: {style: 'font-size:0.7em;opacity:0.6;'}});

					// Combined bar (full width background, filled by score)
					const barBg = row.createDiv({attr: {style: 'height:18px;background:var(--background-modifier-border);border-radius:3px;overflow:hidden;position:relative;'}});
					const barW = Math.max(2, (m.combined_score / (maxCombined || 1)) * 100);
					// Color: green for top, fading to yellow then red
					const hue = i === 0 ? 120 : Math.max(0, 120 - (i / Math.max(sorted.length - 1, 1)) * 120);
					const sat = i === 0 ? 70 : 50;
					const light = i === 0 ? 35 : 45;
					barBg.createDiv({attr: {style: `width:${barW}%;height:100%;background:hsl(${hue},${sat}%,${light}%);border-radius:3px;transition:width 0.3s;`}});

					// Accuracy sub-bar (thin line inside)
					const accW = Math.max(1, m.overall_score * 100);
					barBg.createDiv({attr: {style: `position:absolute;top:0;left:0;width:${accW}%;height:3px;background:var(--text-success);border-radius:0 2px 2px 0;opacity:0.8;`}});
				});

				// ── Per-benchmark heatmap ──────────────────────────────
				const detDisclosure = tourneyResultsEl.createEl('details', {attr: {style: 'margin-top:8px;'}});
				detDisclosure.createEl('summary', {text: 'Per-benchmark breakdown'});
				const detBody = detDisclosure.createEl('div', {attr: {style: 'margin-top:6px;overflow-x:auto;'}});

				// Build a compact heatmap: rows = benchmarks, cols = models
				const benchmarks = result.benchmarks || [];
				if (benchmarks.length && sorted.length) {
					const hmTable = detBody.createEl('table', {attr: {style: 'border-collapse:collapse;font-size:0.75em;width:100%;'}});
					const hmThead = hmTable.createEl('thead');
					const hmHr = hmThead.createEl('tr');
					hmHr.createEl('th', {text: 'Benchmark', attr: {style: 'text-align:left;padding:3px 6px;border-bottom:1px solid var(--background-modifier-border);'}});
					sorted.forEach(m => {
						hmHr.createEl('th', {text: m.model_name.split(':').pop().substring(0, 12), attr: {style: 'text-align:center;padding:3px 4px;border-bottom:1px solid var(--background-modifier-border);font-size:0.85em;'}});
					});
					const hmTbody = hmTable.createEl('tbody');
					benchmarks.forEach(b => {
						const bRow = hmTbody.createEl('tr');
						bRow.createEl('td', {text: b.name, attr: {style: 'padding:2px 6px;white-space:nowrap;'}});
						sorted.forEach(m => {
							const mb = (m.benchmarks || []).find(x => x.benchmark_id === b.id);
							const cell = bRow.createEl('td', {attr: {style: 'text-align:center;padding:2px 4px;'}});
							if (!mb) {
								cell.setText('—');
							} else if (mb.error) {
								cell.createEl('span', {text: '⚠', attr: {style: 'color:var(--text-error);', title: mb.error}});
							} else if (mb.passed) {
								cell.createEl('span', {text: '✓', attr: {style: 'color:var(--text-success);font-weight:700;'}});
							} else {
								cell.createEl('span', {text: '✗', attr: {style: 'color:var(--text-error);'}});
							}
							// Show latency in tiny text
							if (mb && !mb.error) {
								cell.createEl('div', {text: (mb.latency_ms / 1000).toFixed(1) + 's', attr: {style: 'font-size:0.65em;opacity:0.5;'}});
							}
						});
					});
				}

				new Notice(`Tournament complete: ${sorted.length} model(s) tested.`);
			} catch (e) {
				tourneyResultsEl.empty();
				tourneyResultsEl.createEl('div', {text: 'Error: ' + (e.message || e), attr: {style: 'color:var(--text-error);'}});
				tourneyStatusEl.setText('Error.');
			} finally {
				tourneyRunning = false;
				tourneyRunBtn.removeAttribute('disabled');
				tourneyRunBtn.setText('Run Tournament');
			}
		});

		// Staging provider dropdown is populated by refreshPotUI() above —
		// no standalone fetch needed (it would be a duplicate of the same
		// /llm/providers call that refreshPotUI already makes).
		refreshStagingList();

		// ── Configuration status panel ────────────────────────────────
		// Shows the effective value + source for each user-facing config key,
		// so the user can see which config source is "winning" (plugin panel
		// vs .env file) without grepping .env. Conflicts (panel and .env
		// disagree) are shown as warnings. Calls GET /config/effective.
		containerEl.createEl('h3', {text: 'Configuration Status'});
		const configStatusEl = containerEl.createEl('div',
			{cls: 'vaultbot-config-status'});
		const configRefreshBtn = containerEl.createEl('button',
			{text: 'Refresh config status'});
		configRefreshBtn.style.marginBottom = '8px';
		const refreshConfigStatus = async () => {
			configStatusEl.empty();
			configStatusEl.createEl('div', {
				text: 'Checking...', cls: 'vaultbot-config-checking'});
			try {
				const resp = await fetch(
					this.plugin.settings.backendUrl + '/config/effective');
				if (!resp.ok) throw new Error('backend unreachable');
				const data = await resp.json();
				const items = data.config || [];
				configStatusEl.empty();
				if (!items.length) {
					configStatusEl.createEl('div', {
						text: 'No config values found.'});
					return;
				}
				items.forEach(item => {
					const row = configStatusEl.createDiv({
						cls: 'vaultbot-config-row' + (
							item.conflict ? ' vaultbot-config-conflict' : '')});
					// Label + value (or "set"/"not set" for secrets).
					const label = row.createEl('span',
						{cls: 'vaultbot-config-label', text: item.label});
					const valText = item.is_secret
						? (item.has_value ? '(set)' : '(not set)')
						: (item.value || '(not set)');
					const val = row.createEl('span',
						{cls: 'vaultbot-config-value', text: valText});
					// Source badge: .env / runtime / default.
					const sourceLabel = item.source === 'env_file'
						? '.env'
						: item.source === 'runtime'
						? 'panel'
						: 'default';
					const source = row.createEl('span',
						{cls: 'vaultbot-config-source vaultbot-config-source-'
							+ item.source, text: sourceLabel});
					// Conflict warning.
					if (item.conflict) {
						const warn = row.createEl('span',
							{cls: 'vaultbot-config-warn',
							 text: '⚠ .env and panel disagree'});
						warn.title = 'The .env file and the settings panel ' +
							'have different values for this key. The ' +
							'panel value is in effect — edit .env to match ' +
							'if you want them consistent.';
					}
				});
			} catch (e) {
				configStatusEl.empty();
				configStatusEl.createEl('div', {
					text: 'Backend offline — start the backend to see config status.',
					cls: 'vaultbot-config-offline'});
			}
		};
		configRefreshBtn.addEventListener('click', () => refreshConfigStatus());
		refreshConfigStatus();

		// ── Talk Mode (JARVIS-style spoken conversations) ────────────────

		// STT/TTS are first-class roles in the SAME pot as the LLMs. The user

		// can point stt at any endpoint (OpenAI Whisper, Groq, a local server)

		// and tts at any endpoint (OpenAI voices, Edge TTS, a local server),

		// OR use the built-in browser (Web Speech API) / Edge TTS providers.

		// No pigeonholing — same add-provider + add-model flow as the LLMs.

		containerEl.createEl('h3', {text: 'Talk Mode'});

		containerEl.createEl('p', {text: 'When on, the Call button in the sidebar lets you have a real-time spoken conversation. VaultBot speaks as it types (in chunks), and you can talk back. This is the JARVIS mode.', attr: {style: 'opacity:0.7;font-size:0.85em;margin:4px 0 10px 0;'}});



		new Setting(containerEl)

			.setName('Enable talk mode')

			.setDesc('Show the Call button in the sidebar and allow spoken conversations.')

			.addToggle(toggle => toggle

				.setValue(this.plugin.settings.talkMode || false)

				.onChange(async (value) => {

					this.plugin.settings.talkMode = value;

					await this.plugin.saveSettings();

				}));



		new Setting(containerEl)

			.setName('TTS speaks as it types')

			.setDesc('Speak each sentence as it finishes, instead of waiting for the whole reply. Recommended for real-time feel.')

			.addToggle(toggle => toggle

				.setValue(this.plugin.settings.ttsStream !== false)

				.onChange(async (value) => {

					this.plugin.settings.ttsStream = value;

					await this.plugin.saveSettings();

				}));



		new Setting(containerEl)

			.setName('Auto-listen after reply')

			.setDesc('During a call, start listening again after VaultBot finishes speaking — for back-and-forth conversation.')

			.addToggle(toggle => toggle

				.setValue(this.plugin.settings.ttsAutoListen || false)

				.onChange(async (value) => {

					this.plugin.settings.ttsAutoListen = value;

					await this.plugin.saveSettings();

				}));



		// ── Speech Models (STT / TTS) — same pot as the LLMs ─────────────

		// STT and TTS draw from the same registry pot. The dropdown lists

		// every model that can serve that role (any openai-type provider,

		// edge-tts, browser). Pick any; it persists to providers.json like

		// the LLM roles. For edge-tts/openai we ALSO offer a voice picker

		// (from /tts/voices) so the user can pick the exact voice, which

		// becomes the model id for the tts role.

		containerEl.createEl('h3', {text: 'Speech Models (STT / TTS)'});

		containerEl.createEl('p', {text: 'STT and TTS use the same pot as your LLMs — add any provider (OpenAI, Groq, a local Whisper server, Edge TTS, the browser) and assign it to the role. Edge TTS + Browser are built in and free. The Call button appears in the sidebar once Talk Mode is on.', attr: {style: 'opacity:0.7;font-size:0.85em;margin:4px 0 10px 0;'}});



		// Fetch the full pot (all roles + models) once.

		const fetchPot = async () => {

			try {

				const res = await fetch(this.plugin.settings.backendUrl + '/llm/models/all');

				if (!res.ok) return null;

				const data = await res.json();

				return data;

			} catch (e) { return null; }

		};



		// STT role dropdown: any model that can serve STT.

		const sttSetting = new Setting(containerEl)

			.setName('Speech-to-text (STT) model')

			.setDesc('What transcribes your voice. Any OpenAI-compatible /v1/audio/transcriptions endpoint, or Browser (in-browser mic).');

		const sttSel = sttSetting.controlEl.createEl('select');

		sttSel.style.minWidth = '220px';

		sttSel.createEl('option', {text: '— not set —', attr: {value: ''}});



		// TTS role dropdown: any model that can serve TTS.

		const ttsSetting = new Setting(containerEl)

			.setName('Text-to-speech (TTS) model')

			.setDesc('What voice VaultBot speaks with. Any OpenAI-compatible /v1/audio/speech endpoint, Edge TTS (free), or Browser.');

		const ttsSel = ttsSetting.controlEl.createEl('select');

		ttsSel.style.minWidth = '220px';

		ttsSel.createEl('option', {text: '— not set —', attr: {value: ''}});



		// Voice picker for the TTS provider (edge-tts/openai). Lets the user

		// pick the exact voice, which becomes the model entry for the tts

		// role. Shown only when the provider has a voice list.

		let ttsVoiceRow = containerEl.createDiv({attr: {style: 'display:none;margin:4px 0 8px 24px;'}});

		ttsVoiceRow.createEl('span', {text: 'Voice: ', attr: {style: 'font-size:0.85em;opacity:0.8;'}});

		const ttsVoiceSel = ttsVoiceRow.createEl('select');

		ttsVoiceSel.style.minWidth = '200px';



		const setRole = async (role, modelId) => {

			try {

				await fetch(this.plugin.settings.backendUrl + '/llm/role', {

					method: 'POST', headers: {'Content-Type':'application/json'},

					body: JSON.stringify({role, model_id: modelId})

				});

			} catch (e) {}

		};

		const addModel = async (id, model, provider, label, kind) => {

			try {

				await fetch(this.plugin.settings.backendUrl + '/llm/models', {

					method: 'POST', headers: {'Content-Type':'application/json'},

					body: JSON.stringify({id, model, provider, instruct: true, label, kind: kind || 'llm'})

				});

			} catch (e) {}

		};



		// Populate the STT + TTS role dropdowns from the pot.

				const populateSpeechRoles = async () => {

			const data = await fetchPot();

			if (!data) { new Notice('Start the backend to pick speech models.'); return; }

			const roles = data.roles || {};

			const models = data.models || [];

			// Provider lookup for labels.

			const provRes = await fetch(this.plugin.settings.backendUrl + '/llm/providers');

			const provData = await provRes.json();

			const provLabel = {};

			for (const p of (provData.providers || [])) provLabel[p.id] = p.label || p.id;

			// STT dropdown — only kind === 'stt' models (its own pot).

			while (sttSel.options.length > 1) sttSel.remove(1);

			let sttCur = roles.stt || '';

			for (const m of models) {

				if (m.kind !== 'stt') continue;

				const opt = sttSel.createEl('option', {text: (provLabel[m.provider]||m.provider) + ' · ' + (m.label||m.model), attr: {value: m.id}});

				if (m.id === sttCur) opt.selected = true;

			}

			// TTS dropdown — only kind === 'tts' models (its own pot).

			while (ttsSel.options.length > 1) ttsSel.remove(1);

			let ttsCur = roles.tts || '';

			for (const m of models) {

				if (m.kind !== 'tts') continue;

				const opt = ttsSel.createEl('option', {text: (provLabel[m.provider]||m.provider) + ' · ' + (m.label||m.model), attr: {value: m.id}});

				if (m.id === ttsCur) opt.selected = true;

			}

			// If the current TTS model is edge-tts/openai, show the voice picker.

			await refreshTtsVoices(ttsSel.value);

		};

// Refresh the TTS voice picker for the selected TTS model's provider.

		const refreshTtsVoices = async (modelId) => {

			// Determine the provider type of the selected tts model.

			const data = await fetchPot();

			if (!data) { ttsVoiceRow.style.display = 'none'; return; }

			const m = (data.models || []).find(x => x.id === modelId);

			if (!m) { ttsVoiceRow.style.display = 'none'; return; }

			const provRes = await fetch(this.plugin.settings.backendUrl + '/llm/providers');

			const provData = await provRes.json();

			const ptype = (provData.providers.find(p => p.id === m.provider) || {}).type;

			if (ptype === 'edge-tts' || ptype === 'openai') {

				ttsVoiceRow.style.display = '';

				while (ttsVoiceSel.options.length > 0) ttsVoiceSel.remove(0);

				ttsVoiceSel.createEl('option', {text: 'loading voices…', attr: {value: '', disabled: true}});

				try {

					const vr = await fetch(this.plugin.settings.backendUrl + '/tts/voices');

					const vj = await vr.json();

					ttsVoiceSel.empty();

					const voices = vj.voices || [];

					// If the current model IS a voice, pre-select it.

					for (const v of voices) {

						const opt = ttsVoiceSel.createEl('option', {text: v.label || v.id, attr: {value: v.id}});

						if (v.id === m.model) opt.selected = true;

					}

					if (!voices.length) ttsVoiceSel.createEl('option', {text: '(no voices — check the provider)', attr: {value: ''}});

				} catch (e) {

					ttsVoiceSel.empty();

					ttsVoiceSel.createEl('option', {text: 'could not load voices', attr: {value: ''}});

				}

				// Changing the voice creates/updates a model entry + assigns the role.

				ttsVoiceSel.onchange = async () => {

					const voiceId = ttsVoiceSel.value;

					if (!voiceId) return;

					const entryId = m.provider + ':' + voiceId;

					await addModel(entryId, voiceId, m.provider, ttsVoiceSel.options[ttsVoiceSel.selectedIndex].text, 'tts');

					await setRole('tts', entryId);

					new Notice('TTS voice set to ' + voiceId);

					await populateSpeechRoles();

				};

			} else {

				ttsVoiceRow.style.display = 'none';

			}

		};



		sttSel.addEventListener('change', async () => {

			await setRole('stt', sttSel.value);

			new Notice('STT model set');

		});

		ttsSel.addEventListener('change', async () => {

			await setRole('tts', ttsSel.value);

			new Notice('TTS model set');

			await refreshTtsVoices(ttsSel.value);

		});



		populateSpeechRoles();


				// ── Advanced disclosure (Backend URL) ─────────────────────────
		// Backend URL is an internal setting most users never need. It's
		// behind a disclosure so a non-tech user isn't confronted with it,
		// but still accessible for the rare case it's needed. The placeholder
		// is 127.0.0.1 (not localhost — the code rewrites localhost to
		// 127.0.0.1 on load to avoid the IPv6/IPv4 resolution bug).
		const advDisclosure = containerEl.createEl('details',
			{cls: 'vaultbot-advanced'});
		advDisclosure.createEl('summary',
			{text: 'Advanced', cls: 'vaultbot-advanced-summary'});
		const advBody = advDisclosure.createEl('div');
		advBody.style.marginTop = '8px';
		new Setting(advBody)
			.setName('Backend URL')
			.setDesc('URL of the VaultBot backend API. Only change this if you ' +
				'run the backend on a different port or host.')
			.addText(text => text
				.setPlaceholder('http://127.0.0.1:8000')
				.setValue(this.plugin.settings.backendUrl)
				.onChange(async (value) => {
					this.plugin.settings.backendUrl = value;
					await this.plugin.saveSettings();
				}));

		containerEl.createEl('h3', {text: 'Research Backend'});

		new Setting(containerEl)
			.setName('Search backend')
			.setDesc('Tavily is the sole search backend (API-key\u2019d, reliable, no rate-limiting). Set your key below.')
			.addDropdown(dropdown => dropdown
				.addOption('tavily', 'Tavily')
				.setValue(this.plugin.settings.researchBackend || 'tavily')
				.onChange(async (value) => {
					this.plugin.settings.researchBackend = value;
					await this.plugin.saveSettings();
					await this.plugin.pushResearchConfig();
				}));

		const tavilySetting = new Setting(containerEl)
			.setName('Tavily API key')
			.setDesc('Free key from tavily.com. Stored in the plugin settings + written to the vault .env. Required for research.');
		const tavilyInput = tavilySetting.controlEl.createEl('input', {type: 'password', attr: {placeholder: 'tvly-...'}});
		tavilyInput.value = this.plugin.settings.tavilyApiKey || '';
		tavilyInput.style.minWidth = '220px';
		tavilyInput.addEventListener('change', async () => {
			this.plugin.settings.tavilyApiKey = tavilyInput.value.trim();
			await this.plugin.saveSettings();
			await this.plugin.pushResearchConfig();
			new Notice('Tavily API key saved.');
		});

		// Note: starting the backend is handled by the Restart button in the
		// VaultBot sidebar. There's no separate "Start backend now" button
		// here anymore — having two entry points could race each other
		// (one starts while the other restarts), leaving the backend in a
		// half-up state. Use the sidebar Restart button instead.

containerEl.createEl('h3', {text: 'Safety'});
		containerEl.createEl('div', {text:
			'Safe Mode prevents VaultBot from modifying its own code, creating ' +
			'new tools, executing arbitrary Python, restarting the backend, or ' +
			'deleting files. Turn it off only if you want the full self-improving ' +
			'agent experience (Developer Mode).',
			attr: {style: 'opacity:0.7;font-size:0.85em;margin:4px 0 10px 0;'}});

		new Setting(containerEl)
			.setName('Safe Mode')
			.setDesc('Block self-modification, code execution, and destructive operations')
			.addToggle(toggle => toggle
				.setValue(this.plugin.settings.safeMode !== false)
				.onChange(async (value) => {
					this.plugin.settings.safeMode = value;
					await this.plugin.saveSettings();
					new Notice(value
						? 'Safe Mode enabled — self-modification blocked. Restart backend to apply.'
						: 'Developer Mode enabled — full self-modification unlocked. Restart backend to apply.');
				}));

		new Setting(containerEl)
			.setName('Allow web research')
			.setDesc('Let VaultBot search the web when the vault doesn\'t have enough information')
			.addToggle(toggle => toggle
				.setValue(this.plugin.settings.allowWebResearch !== false)
				.onChange(async (value) => {
					this.plugin.settings.allowWebResearch = value;
					await this.plugin.saveSettings();
					new Notice(value
						? 'Web research enabled'
						: 'Web research disabled — VaultBot will only use your vault. Restart backend to apply.');
				}));

		new Setting(containerEl)
			.setName('BS Detector messages')
			.setDesc('When the Think procedure detects unverified premises, show the BS Detector warning directly in chat. Turn off to let VaultBot handle it more gently — the detector still runs in the backend, but the LLM decides how to tell you.')
			.addToggle(toggle => toggle
				.setValue(this.plugin.settings.bsDetectorMessages !== false)
				.onChange(async (value) => {
					this.plugin.settings.bsDetectorMessages = value;
					await this.plugin.saveSettings();
					new Notice(value
						? 'BS Detector messages shown in chat'
						: 'BS Detector runs silently — VaultBot will tell you gently');
				}));

		containerEl.createEl('h3', {text: 'Community contributions'});
		containerEl.createEl('div', {text:
			'Allow your VaultBot to submit improvements (bug fixes, new tools, ' +
			'documentation) to the upstream VaultBot repo as pull requests. ' +
			'Your notes, chat logs, and personal data are NEVER included \u2014 ' +
			'only code files. You also need a GITHUB_TOKEN in your .env file. ' +
			'See CONTRIBUTING.md for details.',
			attr: {style: 'opacity:0.7;font-size:0.85em;margin:4px 0 10px 0;'}});

		new Setting(containerEl)
			.setName('Allow contributions')
			.setDesc('Let your VaultBot submit pull requests to the VaultBot project')
			.addToggle(toggle => toggle
				.setValue(this.plugin.settings.allowContributions || false)
				.onChange(async (value) => {
					this.plugin.settings.allowContributions = value;
					await this.plugin.saveSettings();
					new Notice(value ? 'Contributions enabled' : 'Contributions disabled');
				}));

		containerEl.createEl('h3', {text: 'Updates'});

		// One-click self-updater. Pulls the latest CODE from GitHub and
		// applies it over the live vault. User state is never touched:
		//   - data.json (your keys/model) is preserved
		//   - all your .md notes, chat logs, research, textbooks stay put
		//   - backend runtime state (sessions, checkpoints, indexes, logs,
		//     models, pid) is left exactly as-is
		//   - custom tools your VaultBot created are preserved
		//   - any file the updater overwrites is backed up first to
		//     .vaultbot-update-backup/ so modifications are never lost
		// Only vaultbot_backend/*.py + the plugin's main.js/manifest/styles
		// are replaced. The backend is stopped first (Windows locks .py
		// files while running) and restarted automatically when done.
		containerEl.createEl('div', {text:
			'Update VaultBot to the latest version from GitHub. This replaces ' +
			'only the code (backend Python files + this plugin) \u2014 your notes, ' +
			'chat logs, API keys, model choice, custom tools your bot built, and ' +
			'all other settings are kept safe. Any file that differs from the ' +
			'update is backed up to .vaultbot-update-backup/ first, so nothing ' +
			'is ever lost. The backend restarts automatically when done.',
			attr: {style: 'opacity:0.7;font-size:0.85em;margin:4px 0 10px 0;'}});

		const updateRow = containerEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:8px;flex-wrap:wrap;'}});
		const refInput = updateRow.createEl('input', {type: 'text', attr: {
			placeholder: 'main (branch or tag)',
			style: 'flex:1;min-width:140px;'}});
		refInput.value = 'main';
		const checkBtn = updateRow.createEl('button', {text: 'Check for updates'});
		const updateBtn = updateRow.createEl('button', {text: 'Update from GitHub', cls: 'mod-cta'});
		const updateStatusEl = containerEl.createEl('div', {attr: {style: 'opacity:0.7;font-size:0.8em;min-height:1em;margin-top:6px;'}});

		// Show the currently-installed version from manifest.json.
		let installedVersion = '?';
		try {
			const fs = require('fs');
			const manPath = path.join(this.plugin.app.vault.adapter.getBasePath ? this.plugin.app.vault.adapter.getBasePath() : '', '.obsidian', 'plugins', 'vaultbot', 'manifest.json');
			if (fs.existsSync(manPath)) {
				installedVersion = JSON.parse(fs.readFileSync(manPath, 'utf8')).version || '?';
			}
		} catch (e) {}
		updateStatusEl.setText(`Installed version: ${installedVersion}`);

		// Check for updates: fetch the latest manifest from GitHub and
		// compare versions without applying anything.
		let checking = false;
		checkBtn.addEventListener('click', async () => {
			if (checking) return;
			checking = true;
			checkBtn.setAttribute('disabled', 'disabled');
			checkBtn.setText('Checking\u2026');
			try {
				const info = await this.plugin.checkLatestVersion(refInput.value.trim() || 'main');
				if (info.error) {
					updateStatusEl.setText(`Check failed: ${info.error}`);
				} else if (info.updateAvailable) {
					updateStatusEl.setText(`Update available: v${info.latest} (you have v${info.current}). Click "Update from GitHub" to apply.`);
					new Notice(`VaultBot v${info.latest} is available.`);
				} else {
					updateStatusEl.setText(`You're up to date: v${info.current}.`);
				}
			} catch (e) {
				updateStatusEl.setText('Check failed: ' + (e && e.message ? e.message : String(e)));
			} finally {
				checking = false;
				checkBtn.removeAttribute('disabled');
				checkBtn.setText('Check for updates');
			}
		});

		// Restore last version button: shown only after a failed update
		// when a backup exists. Calls POST /update/rollback, then restarts
		// the backend so the restored (pre-update) code runs. This surfaces
		// the existing .vaultbot-update-backup/ safety net to the user.
		const restoreBtn = updateRow.createEl('button', {
			text: 'Restore last version', cls: 'mod-cta'});
		restoreBtn.style.display = 'none';
		restoreBtn.title = 'Restore the code from before the last update attempt.';

		// Check if any backups exist; if so, show the restore button.
		const checkAndShowRestoreBtn = async () => {
			try {
				const resp = await fetch(
					this.plugin.settings.backendUrl + '/update/backups');
				if (resp.ok) {
					const data = await resp.json();
					if (data.backups && data.backups.length > 0) {
						restoreBtn.style.display = '';
						return;
					}
				}
			} catch (e) {}
			restoreBtn.style.display = 'none';
		};
		// Also check on initial load in case a previous update failed.
		checkAndShowRestoreBtn();

		restoreBtn.addEventListener('click', async () => {
			restoreBtn.setAttribute('disabled', 'disabled');
			restoreBtn.setText('Restoring...');
			try {
				const resp = await fetch(
					this.plugin.settings.backendUrl + '/update/rollback',
					{method: 'POST'});
				const data = await resp.json();
				if (data.status === 'ok') {
					updateStatusEl.setText(
						`Restored ${data.restored} file(s) from backup ` +
						`(timestamp: ${data.backup}). Restarting backend...`);
					new Notice(`Restored ${data.restored} file(s). Restarting...`);
					// Restart the backend so the restored code runs.
					await this.plugin.restartBackend();
					restoreBtn.style.display = 'none';
				} else if (data.status === 'no_backup') {
					updateStatusEl.setText('No backup found. Nothing to restore.');
					restoreBtn.style.display = 'none';
				} else {
					updateStatusEl.setText('Restore failed: ' + (data.error || 'unknown error'));
				}
			} catch (e) {
				updateStatusEl.setText('Restore failed: ' + (e.message || e));
			} finally {
				restoreBtn.removeAttribute('disabled');
				restoreBtn.setText('Restore last version');
			}
		});

		let updating = false;
		updateBtn.addEventListener('click', async () => {
			if (updating) return;
			updating = true;
			updateBtn.setAttribute('disabled', 'disabled');
			updateBtn.setText('Updating\u2026');
			try {
				const res = await this.plugin.performSelfUpdate((msg) => {
					updateStatusEl.setText(msg);
				}, refInput.value.trim() || 'main');
				if (res && res.ok) {
					updateStatusEl.setText(`Done. VaultBot is now v${res.version}.`);
					new Notice(`VaultBot updated to v${res.version}.`);
					// Hide the restore button on success — no rollback needed.
					restoreBtn.style.display = 'none';
				} else {
					updateStatusEl.setText(`Update failed: ${res && res.error ? res.error : 'unknown error'}`);
					// Show the restore button so the user can roll back to
					// the pre-update version. The backup was created by
					// copyCodeTree before the overwrite; even a mid-flight
					// failure may have a partial backup. We check for
					// backups before showing the button.
					await checkAndShowRestoreBtn();
				}
			} catch (e) {
				updateStatusEl.setText('Update failed: ' + (e && e.message ? e.message : String(e)));
				await checkAndShowRestoreBtn();
			} finally {
				updating = false;
				updateBtn.removeAttribute('disabled');
				updateBtn.setText('Update from GitHub');
			}
		});
	}
}

class VaultBotSidebarView extends ItemView {
	constructor(leaf, backendUrl, plugin) {
		super(leaf);
		this.backendUrl = backendUrl;
		this.plugin = plugin;
		this.contentEl = this.contentEl || this.container;
	}

	getViewType() {
		return 'vaultbot-sidebar';
	}

	getDisplayText() {
		return 'VaultBot';
	}

	async onOpen() {
		if (!this.contentEl) {
			this.contentEl = this.container || this.containerEl;
		}
		this.display();
	}

	async onClose() {
		this.contentEl.empty();
	}

	display() {
		if (!this.contentEl) {
			this.contentEl = this.container || this.containerEl;
		}
		this.contentEl.empty();
		this.contentEl.addClass('vaultbot-view-root');
		// Three-region layout: fixed header / scrolling chat / fixed input
		// bar at the bottom. Only the chat panel scrolls.
		const headerEl = this.contentEl.createEl('div', {cls: 'vaultbot-header'});
		// Left side of the header: leaf mark + title + subtitle. Wrapped in
		// a flex column so the header row can put the model dropdowns in the
		// upper-right corner without disturbing the title block.
		const headerLeft = headerEl.createDiv({cls: 'vaultbot-header-left'});
		const titleEl = headerLeft.createEl('div', {cls: 'vaultbot-header-title'});
		titleEl.createEl('span', {cls: 'vaultbot-header-mark', text: '🌿'});
		titleEl.createEl('span', {text: 'VaultBot'});

		// Right side of the header: three compact model dropdowns (big /
		// small / vision) in the upper-right corner. Moved up from the
		// footer so model selection is always visible and the chat input
		// area stays uncluttered. Each dropdown is a tiny labeled select.
		const headerModels = headerEl.createDiv({cls: 'vaultbot-header-models'});

		// History toggle ("Recent") — sits in the same row as the LLM
		// dropdowns, not under the title. The button + dropdowns share one
		// tidy row in the header's upper-right area.
		const historyToggle = headerModels.createEl('button', {
			cls: 'vaultbot-history-toggle', text: 'Recent'});
		historyToggle.title = 'Show recent conversations';

		// Backend status line — lives in the header's upper-right corner,
		// small and unobtrusive, so it's not front-and-center in the chat.
		const statusEl = headerModels.createDiv({cls: 'vaultbot-status'});

		// Populate a role <select> from the combined registry "pot". `models`
		// is [{id, model, provider_label, provider_type, vision, instruct,
		// roles}]. Options are keyed by registry model id (not bare model name),
		// grouped by provider, so the user sees local Ollama + cloud models
		// side-by-side and picks any of them into the role. `selectedId` is the
		// id currently assigned to the role. `allowNone` prepends a "(none)".
		const populateSelectPot = (sel, models, selectedId, {allowNone = true, visionOnly = false} = {}) => {
			sel.empty();
			let pool = models.filter(m => m.instruct && (m.kind === undefined || m.kind === 'llm'));
			if (visionOnly) pool = pool.filter(m => m.vision);
			if (allowNone) {
				const none = sel.createEl('option', {text: '(none)', attr: {value: ''}});
				if (!selectedId) none.selected = true;
			}
			if (!pool.length) {
				if (!allowNone) sel.createEl('option', {text: 'No models', attr: {disabled: true}});
				return;
			}
			const byProvider = {};
			pool.forEach(m => {
				const key = m.provider_label || m.provider || '';
				(byProvider[key] = byProvider[key] || []).push(m);
			});
			Object.keys(byProvider).forEach(provLabel => {
				const og = sel.createEl('optgroup', {label: provLabel});
				byProvider[provLabel].forEach(m => {
					const tags = [];
					if (m.vision) tags.push('👁');
					if (Array.isArray(m.roles) && m.roles.length) tags.push('[' + m.roles.join(',') + ']');
					const text = (tags.length ? tags.join(' ') + ' ' : '') + m.model;
					const opt = og.createEl('option', {text, attr: {value: m.id}});
					if (m.id === selectedId) opt.selected = true;
				});
			});
		};

		// --- Big model dropdown (the chat/reasoning model) ---------------
		const bigWrap = headerModels.createDiv({cls: 'vaultbot-header-model'});
		bigWrap.createEl('span', {cls: 'vaultbot-header-model-label', text: 'Big'});
		const bigSelect = bigWrap.createEl('select', {cls: 'vaultbot-header-model-select vaultbot-model-select'});
		bigSelect.createEl('option', {text: '...', attr: {disabled: true}});

		// --- Small model dropdown (tiny local dance partner) --------------
		const smallWrap = headerModels.createDiv({cls: 'vaultbot-header-model'});
		smallWrap.createEl('span', {cls: 'vaultbot-header-model-label', text: 'Small'});
		const smallSelect = smallWrap.createEl('select', {cls: 'vaultbot-header-model-select vaultbot-model-select'});
		smallSelect.createEl('option', {text: '...', attr: {disabled: true}});

		// --- Vision model dropdown (textbook page reader) ----------------
		const visionWrap = headerModels.createDiv({cls: 'vaultbot-header-model'});
		visionWrap.createEl('span', {cls: 'vaultbot-header-model-label', text: 'Vision'});
		const visionSelect = visionWrap.createEl('select', {cls: 'vaultbot-header-model-select vaultbot-model-select'});
		visionSelect.createEl('option', {text: '...', attr: {disabled: true}});

		// Refresh all three dropdowns from the ONE combined pot. Called once on
		// load and whenever the backend comes back online or a model is pulled.
		// The pot holds every model across every provider (local Ollama + cloud),
		// so all three role dropdowns draw from the same interchangeable list.
		const refreshAllModelDropdowns = async () => {
			const online = await this.plugin.onceBackendReady(5000, 500);
			if (!online) {
				[bigSelect, smallSelect, visionSelect].forEach(s => {
					s.empty();
					s.createEl('option', {text: 'offline', attr: {disabled: true}});
				});
				return;
			}
			try {
				const all = await this.plugin.fetchAllModels();
				if (all && Array.isArray(all.models)) {
					const roles = all.roles || {};
					populateSelectPot(bigSelect, all.models, roles.big || '', {allowNone: false});
					populateSelectPot(smallSelect, all.models, roles.small || '', {allowNone: true});
					populateSelectPot(visionSelect, all.models, roles.vision || '', {allowNone: true, visionOnly: false});
					return;
				}
			} catch (e) {}
			[bigSelect, smallSelect, visionSelect].forEach(s => {
				s.empty();
				s.createEl('option', {text: 'no pot — add models in Settings', attr: {disabled: true}});
			});
		};
		refreshAllModelDropdowns();

		// Big model change → map this pot model into the big role.
		bigSelect.addEventListener('change', async () => {
			await this.plugin.setRoleCfg('big', bigSelect.value);
			new Notice(`Big model set: ${bigSelect.value}`);
			refreshAllModelDropdowns();
			const selModel = bigSelect.options[bigSelect.selectedIndex]?.text || bigSelect.value;
			const ctxWin = await this.plugin.fetchContextWindow(selModel);
			if (tokenMeterEl) {
				tokenMeterEl.setAttribute('title',
					`${ctxWin.toLocaleString()} token context window`);
				updateTokenMeter(0, ctxWin);
			}
		});
		// Small model change → map this pot model into the small role.
		smallSelect.addEventListener('change', async () => {
			await this.plugin.setRoleCfg('small', smallSelect.value);
			new Notice(smallSelect.value
				? `Small model set: ${smallSelect.value}`
				: 'Small model cleared — procedures fall back to the big model.');
			refreshAllModelDropdowns();
		});
		// Vision model change → map this pot model into the vision role.
		visionSelect.addEventListener('change', async () => {
			await this.plugin.setRoleCfg('vision', visionSelect.value);
			new Notice(visionSelect.value
				? `Vision model set: ${visionSelect.value}`
				: 'Vision model cleared — page reading falls back to your big model.');
			refreshAllModelDropdowns();
		});

		// History disclosure: expands a dropdown list of past chat sessions
		// (read from /sessions). The toggle button was already created in
		// headerModels above so it sits in the same row as the LLM dropdowns.
		const historyPanel = this.contentEl.createDiv({cls: 'vaultbot-history-panel'});
		historyPanel.style.display = 'none';
		let historyLoaded = false;
		historyToggle.addEventListener('click', async () => {
			const open = historyPanel.style.display !== 'none';
			if (open) {
				historyPanel.style.display = 'none';
				historyToggle.setText('Recent');
				return;
			}
			historyToggle.setText('Loading...');
			try {
				const resp = await fetch(this.backendUrl + '/sessions');
				const data = await resp.json();
				const sessions = data.sessions || [];
				historyPanel.empty();
				if (!sessions.length) {
					historyPanel.createEl('div', {
						cls: 'vaultbot-history-empty',
						text: 'No past conversations yet.'});
				} else {
					for (const s of sessions) {
						const item = historyPanel.createDiv({cls: 'vaultbot-history-item'});
						// Format the start time as a short date.
						let timeLabel = '';
						if (s.started_at) {
							try {
								const d = new Date(s.started_at);
								timeLabel = d.toLocaleDateString(undefined,
									{month: 'short', day: 'numeric'})
									+ ' ' + d.toLocaleTimeString(undefined,
									{hour: 'numeric', minute: '2-digit'});
							} catch (e) { timeLabel = ''; }
						}
						if (timeLabel) {
							item.createEl('span', {
								cls: 'vaultbot-history-time', text: timeLabel});
						}
						item.createEl('span', {
							cls: 'vaultbot-history-preview',
							text: s.preview || '(no messages)'});
						// Load the session's messages read-only on click.
						// We read the .jsonl via a new /sessions/{id} endpoint
						// (added below) and render the user/assistant turns.
						item.addEventListener('click', async () => {
							historyPanel.style.display = 'none';
							historyToggle.setText('Recent');
							try {
								const r = await fetch(
							this.backendUrl + '/sessions/' + s.session_id);
						const turns = (await r.json()).turns || [];
						chatContainer.empty();
						for (const t of turns) {
							if (t.role === 'user') {
								const div = chatContainer.createDiv(
									{cls: 'vaultbot-message user'});
								renderMarkdownInto(div, t.content);
							} else if (t.role === 'assistant' && t.content) {
								const div = chatContainer.createDiv(
									{cls: 'vaultbot-message assistant'});
								const block = div.createEl('div',
									{cls: 'vaultbot-answer-block'});
								renderMarkdownInto(block, t.content);
							if (t.thinking) {
								const thinkHeader = div.createEl('div', {cls: 'vaultbot-thinking-header', text: 'Thinking (click to show)'});
								const thinkBody = div.createEl('div', {cls: 'vaultbot-thinking-block'});
								thinkBody.style.display = 'none';
								thinkBody.setText(t.thinking);
								thinkHeader.addEventListener('click', () => {
									const hidden = thinkBody.style.display === 'none';
									thinkBody.style.display = hidden ? 'block' : 'none';
									thinkHeader.textContent = hidden ? 'Thinking (click to hide)' : 'Thinking (click to show)';
								});
							}
							} else if (t.role === 'tool_call' && t.content) {
								const div = chatContainer.createDiv(
									{cls: 'vaultbot-message tool-call'});
								const label = div.createEl('div',
									{cls: 'vaultbot-tool-call-label'});
								label.createEl('span', {cls: 'vaultbot-tool-icon', text: '\u2699\ufe0f'});
								label.createEl('span', {text: ' ' + (t.content || 'tool')});
								if (t.arguments) {
									const args = div.createEl('div',
										{cls: 'vaultbot-tool-args'});
									args.setText(typeof t.arguments === 'string'
										? t.arguments
										: JSON.stringify(t.arguments, null, 2));
								}
							} else if (t.role === 'tool_result' && t.content) {
								const div = chatContainer.createDiv(
									{cls: 'vaultbot-message tool-result'});
								const label = div.createEl('div',
									{cls: 'vaultbot-tool-result-label',
									 text: 'Result'});
								const body = div.createEl('div',
									{cls: 'vaultbot-tool-result-body'});
								const preview = String(t.content).substring(0, 500);
								body.setText(preview + (t.content.length > 500 ? '...' : ''));
							} else if (t.role === 'thinking' && t.content) {
								const div = chatContainer.createDiv(
									{cls: 'vaultbot-message thinking'});
								const header = div.createEl('div',
									{cls: 'vaultbot-thinking-header',
									 text: 'Thinking'});
								const body = div.createEl('div',
									{cls: 'vaultbot-thinking-body'});
								body.setText(t.content);
								div.addClass('collapsed');
								header.addEventListener('click', () => {
									div.toggleClass('collapsed', !div.hasClass('collapsed'));
								});
							}
						}
						chatContainer.scrollTop = 0;
						setStatus('done', 'Showing past conversation')
							} catch (e) {
								new Notice('Could not load that conversation.');
							}
						});
					}
				}
				historyPanel.style.display = 'block';
				historyToggle.setText('Recent ▴');
				historyLoaded = true;
			} catch (e) {
				new Notice('Could not load conversation history.');
			} finally {
				if (historyPanel.style.display === 'none')
					historyToggle.setText('Recent');
			}
		});

		// Session title bar: editable session title on the left, a
		// compact copy-session-id button on the right. Click title to
		// rename; click ID button to copy the UUID to the clipboard.
		const sessionTitleBar = this.contentEl.createDiv({cls: 'vaultbot-session-title-bar'});
		const sessionTitleEl = sessionTitleBar.createDiv({cls: 'vaultbot-session-title'});
		sessionTitleEl.setText('New Session');
		sessionTitleEl.title = 'Click to rename this session';
		const sessionIdBtn = sessionTitleBar.createEl('button', {cls: 'vaultbot-session-id-btn', text: 'Session ID'});
		sessionIdBtn.title = 'Click to copy the session ID to clipboard';
		sessionIdBtn.addEventListener('click', async () => {
			if (!currentSessionId) { new Notice('No session ID yet.'); return; }
			const shortId = currentSessionId.substring(0, 8);
			sessionIdBtn.setText('Copying...');
			try {
				await navigator.clipboard.writeText(currentSessionId);
			} catch (e) {
				const ta = document.createElement('textarea');
				ta.value = currentSessionId;
				document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
			}
			sessionIdBtn.setText('Copied: ' + shortId);
			setTimeout(() => { sessionIdBtn.setText('Session ID'); }, 2000);
		});
		sessionTitleEl.addEventListener('click', () => {
			sessionTitleEl.setAttribute('contenteditable', 'true');
			sessionTitleEl.addClass('vaultbot-session-title-editing');
			sessionTitleEl.focus();
			const range = document.createRange();
			range.selectNodeContents(sessionTitleEl);
			const sel = window.getSelection();
			sel.removeAllRanges();
			sel.addRange(range);
		});
		const saveSessionTitle = () => {
			sessionTitleEl.removeAttribute('contenteditable');
			sessionTitleEl.removeClass('vaultbot-session-title-editing');
			const newTitle = sessionTitleEl.getText().trim();
			if (newTitle && ws && ws.readyState === WebSocket.OPEN) {
				ws.send(JSON.stringify({type: 'set_title', title: newTitle}));
			}
		};
		sessionTitleEl.addEventListener('blur', saveSessionTitle);
		sessionTitleEl.addEventListener('keydown', (e) => {
			if (e.key === 'Enter') { e.preventDefault(); sessionTitleEl.blur(); }
			if (e.key === 'Escape') { e.preventDefault(); sessionTitleEl.blur(); }
		});

		const chatContainer = this.contentEl.createDiv({cls: 'vaultbot-chat-container'});

		// Smart auto-scroll: track whether the user is "at the bottom" of the
		// chat. When the user scrolls up to read history, auto-scroll pauses.
		// When they scroll back to the bottom, auto-scroll resumes. This
		// mirrors GitHub Copilot's behavior: stay glued to the bottom unless
		// the user explicitly scrolls up, then respect their position until
		// they return to the bottom.
		const SCROLL_THRESHOLD = 80; // px from bottom to still count as "at bottom"
		let userAtBottom = true;     // start assuming we're at the bottom

		chatContainer.addEventListener('scroll', () => {
			const isNearBottom = (chatContainer.scrollHeight - chatContainer.scrollTop - chatContainer.clientHeight) < SCROLL_THRESHOLD;
			userAtBottom = isNearBottom;
			// Show/hide the scroll-to-bottom button based on position
			if (scrollToBottomBtn) {
				scrollToBottomBtn.style.display = userAtBottom ? 'none' : 'flex';
			}
		});

		const smartScrollToBottom = () => {
			if (userAtBottom) {
				chatContainer.scrollTo({ top: chatContainer.scrollHeight, behavior: 'smooth' });
			}
		};

		// Scroll-to-bottom button: a floating button that appears when the
		// user has scrolled up, letting them jump back to the latest chat in
		// one click. Positioned at the bottom-right of the chat container.
		const scrollToBottomBtn = this.contentEl.createDiv({cls: 'vaultbot-scroll-bottom-btn'});
		scrollToBottomBtn.style.display = 'none';
		const btnIcon = scrollToBottomBtn.createSpan({text: '\u2193'});
		scrollToBottomBtn.addEventListener('click', () => {
			chatContainer.scrollTo({ top: chatContainer.scrollHeight, behavior: 'smooth' });
			userAtBottom = true;
			scrollToBottomBtn.style.display = 'none';
		});

	// Delegated click handler: make [[wikilinks]] and external links clickable
	// in chat messages. Works for both user and assistant messages.
	chatContainer.addEventListener('click', (e) => {
		// Internal links ([[wikilink]]) -> open the file in Obsidian
		const intLink = e.target.closest('a.internal-link');
		if (intLink) {
			e.preventDefault();
			const href = intLink.getAttribute('data-href') || intLink.getAttribute('href');
			if (!href) return;
			// Resolve the link through Obsidian's metadata cache. If the
			// note exists, open it. If not, show a notice instead of
			// silently creating an empty note with just the title —
			// which is the "wikilinks resolve to empty notes" bug.
			const resolved = this.app.metadataCache.getFirstLinkpathDest(href, '');
			if (resolved) {
				this.app.workspace.openLinkText(href, '', false);
			} else {
				// Try opening anyway — Obsidian may still resolve it via
				// its own search if the file exists but isn't cached yet.
				// If it truly doesn't exist, show a notice instead of
				// creating an empty note.
				const allFiles = this.app.vault.getMarkdownFiles();
				const found = allFiles.some(f => {
					const basename = f.path.split('/').pop().replace(/\.md$/i, '');
					return basename.toLowerCase() === href.toLowerCase();
				});
				if (found) {
					this.app.workspace.openLinkText(href, '', false);
				} else {
					try { new Notice(`Note "${href}" not found in vault.`); } catch(e2) {}
				}
			}
			return;
		}
		// External links -> open in browser
		const extLink = e.target.closest('a.external-link');
		if (extLink) {
			e.preventDefault();
			const href = extLink.getAttribute('href');
			if (href) { try { window.open(href, '_blank'); } catch(e2) {} }
		}
	});

		let connectionCheckInterval = null;
		let backendWasOnline = false;
		// Typed status: kind drives the color + icon, action is an optional
		// click handler (replaces the old boolean `clickable`). Only states
		// that carry an action look clickable — pure status never does, so a
		// user isn't tempted to click "Done" or "Backend online".
		const STATUS_KIND_CLASS = {
			online:    'vaultbot-status-online',
			offline:   'vaultbot-status-offline',
			starting:  'vaultbot-status-starting',
			error:     'vaultbot-status-error',
			done:      'vaultbot-status-done'
		};
		const setStatus = (kind, text, action) => {
			statusEl.empty();
			// Remove all status-kind classes, then add the current one.
			Object.values(STATUS_KIND_CLASS).forEach(c => statusEl.removeClass(c));
			const cls = STATUS_KIND_CLASS[kind] || STATUS_KIND_CLASS.done;
			statusEl.addClass(cls);
			statusEl.setText(text);
			if (typeof action === 'function') {
				statusEl.style.cursor = 'pointer';
				statusEl.onclick = async () => {
					statusEl.onclick = null;
					await action();
				};
			} else {
				statusEl.style.cursor = 'default';
				statusEl.onclick = null;
			}
		};

		const ensureConnection = async () => {
			// If a boot is in progress, don't open another fetch probe here —
			// just await the shared ready promise. This is what was spamming
			// the dev console with ERR_CONNECTION_REFUSED every 5s while the
			// backend was still coming up.
			if (this.plugin.backendStarting || this.plugin._backendReadyPromise) {
				const ready = await this.plugin.onceBackendReady();
				if (!ready) {
					backendWasOnline = false;
					setStatus('offline', 'Backend offline');
				}
				return;
			}
			const running = await this.plugin.isBackendRunning();
			if (running) {
				setStatus('online', 'Backend online');
				if (!backendWasOnline) {
					backendWasOnline = true;
					refreshModels();
					// Backend just came back up: clear the restart button's
					// dark "busy/offline" state so it reads "Restart" again.
					if (restartButton) {
						restartButton.removeAttribute('disabled');
						restartButton.setText('Restart');
						restartButton.removeClass('vaultbot-restart-busy');
					}
				}
				// Only connect if there is no socket at all, or the existing one is
				// CLOSING/CLOSED. A socket still in CONNECTING state must not be
				// re-initiated or it gets closed before the handshake completes.
				if (!ws || ws.readyState === WebSocket.CLOSING || ws.readyState === WebSocket.CLOSED) {
					connectWebSocket();
				}
				return;
			}
			backendWasOnline = false;
			setStatus('offline', 'Backend offline');
		};

		ensureConnection();
		connectionCheckInterval = window.setInterval(ensureConnection, 5000);
		this.registerInterval(connectionCheckInterval);

		// Footer: the input/buttons. This is the fixed bottom region of the
		// view; only the chat panel above it scrolls. (The model picker
		// dropdowns now live in the header's upper-right corner.)
		const footerEl = this.contentEl.createDiv({cls: 'vaultbot-footer'});

		// refreshModels is now an alias for the header dropdown refresher so
		// the existing call sites (ensureConnection's backend-online branch
		// and the model_pull_done WebSocket handler) keep working.
		const refreshModels = refreshAllModelDropdowns;

		// --- Token-usage meter ------------------------------------------
		// A horizontal bar that fills proportional to how many tokens the
		// current conversation is using, capped at the equipped model's
		// context window. Updates live from context_usage events the backend
		// emits each turn (pre-loop + post-answer). Color shifts moss→clay→
		// bark as it fills so the user can see at a glance how close the
		// context is to overflowing.
		const modelBar = footerEl.createDiv({cls: 'vaultbot-model-bar'});
		const tokenMeterWrap = modelBar.createDiv({cls: 'vaultbot-token-meter-wrap'});
		tokenMeterWrap.setAttribute('aria-hidden', 'true');
		const tokenMeterEl = tokenMeterWrap.createDiv({cls: 'vaultbot-token-meter'});
		const tokenMeterFill = tokenMeterEl.createDiv({cls: 'vaultbot-token-meter-fill'});
		const tokenMeterLabel = tokenMeterWrap.createEl('span', {cls: 'vaultbot-token-meter-label', text: '—'});
		tokenMeterEl.setAttribute('title', 'Context usage — fills as the conversation grows');
		let tokenMeterCtxWindow = 32768;
		function updateTokenMeter(used, window) {
			if (window && window > 0) tokenMeterCtxWindow = window;
			const pct = Math.min(100, Math.max(0, (used / tokenMeterCtxWindow) * 100));
			tokenMeterFill.style.width = pct.toFixed(1) + '%';
			// Color thresholds: <60% moss, 60-85% clay, >85% bark
			tokenMeterFill.removeClass('vaultbot-token-meter-fill-warn',
				'vaultbot-token-meter-fill-crit');
			if (pct > 85) tokenMeterFill.addClass('vaultbot-token-meter-fill-crit');
			else if (pct > 60) tokenMeterFill.addClass('vaultbot-token-meter-fill-warn');
			const usedK = used >= 1000 ? (used / 1000).toFixed(1) + 'k' : String(used);
			const winK = tokenMeterCtxWindow >= 1000
				? (tokenMeterCtxWindow / 1000).toFixed(0) + 'k' : String(tokenMeterCtxWindow);
			tokenMeterLabel.setText(usedK + ' / ' + winK + ' tok');
		}

		// --- Ollama status bar -----------------------------------------
		// A compact bar below the model picker showing live Ollama stats:
		// loaded model name, VRAM usage, context length, and after each
		// chat round: tokens/s (prompt + generation), load time.
		// Polls /ollama/stats every 5s for model/VRAM status; updates
		// instantly when ollama_stats WS messages arrive during chat.
		const statsBar = footerEl.createDiv({cls: 'vaultbot-stats-bar'});
		const statsModelEl = statsBar.createEl('span', {cls: 'vaultbot-stats-model', text: '—'});
		const statsVramEl = statsBar.createEl('span', {cls: 'vaultbot-stats-vram', text: ''});
		const statsCtxEl = statsBar.createEl('span', {cls: 'vaultbot-stats-ctx', text: ''});
		const statsPerfEl = statsBar.createEl('span', {cls: 'vaultbot-stats-perf', text: ''});

		function formatBytes(b) {
			if (!b || b <= 0) return '';
			const gb = b / 1073741824;
			if (gb >= 1) return gb.toFixed(1) + ' GB';
			const mb = b / 1048576;
			return mb.toFixed(0) + ' MB';
		}
		function formatMs(ms) {
			if (ms < 1) return ms.toFixed(2) + 'ms';
			if (ms < 1000) return Math.round(ms) + 'ms';
			return (ms / 1000).toFixed(1) + 's';
		}
		function updateStatsFromPoll(data) {
			if (!data) return;
			const models = data.models || [];
			if (models.length === 0) {
				statsModelEl.setText('No model loaded');
				statsModelEl.removeClass('vaultbot-stats-model-loaded');
				statsVramEl.setText('');
				statsCtxEl.setText('');
			} else {
				// Show the first (primary) loaded model
				const m = models[0];
				statsModelEl.setText(m.name || '—');
				statsModelEl.addClass('vaultbot-stats-model-loaded');
				const vram = formatBytes(m.size_vram);
				const total = formatBytes(m.size_total);
				statsVramEl.setText(vram ? 'VRAM: ' + vram : '');
				statsCtxEl.setText(m.context_length ? 'ctx: ' + (m.context_length / 1000).toFixed(0) + 'k' : '');
			}
		}
		function updateStatsFromChat(stats) {
			// Called when an ollama_stats WS message arrives after a chat round.
			// Shows tokens/s for prompt processing and generation.
			const parts = [];
			if (stats.load_duration_ms > 0) {
				parts.push('load: ' + formatMs(stats.load_duration_ms));
			}
			if (stats.prompt_tokens_per_s > 0) {
				parts.push('prompt: ' + stats.prompt_tokens_per_s + ' t/s');
			}
			if (stats.gen_tokens_per_s > 0) {
				parts.push('gen: ' + stats.gen_tokens_per_s + ' t/s');
			}
			if (stats.eval_count > 0) {
				parts.push(stats.eval_count + ' tok');
			}
			statsPerfEl.setText(parts.join(' · '));
		}
		// Poll /ollama/stats every 5s for model/VRAM status
		let statsPollTimer = null;
		async function pollOllamaStats() {
			try {
				const resp = await fetch(this.plugin.settings.backendUrl + '/ollama/stats');
				if (resp.ok) {
					const data = await resp.json();
					updateStatsFromPoll(data);
				}
			} catch (e) { /* backend may be briefly down during restart */ }
		}
		const _pollFn = pollOllamaStats.bind(this);
		statsPollTimer = setInterval(_pollFn, 5000);
		// Initial poll after a short delay (backend may not be ready yet)
		setTimeout(_pollFn, 2000);

		const inputContainer = footerEl.createDiv({cls: 'vaultbot-input-container'});
		// The chat bar: textarea + an inline Stop button that only appears
		// while a turn is in flight (so there's always something to stop).
		const chatBar = inputContainer.createDiv({cls: 'vaultbot-chat-bar'});
		const input = chatBar.createEl('textarea', {
			cls: 'vaultbot-input',
			attr: {placeholder: 'Ask VaultBot...', rows: '3'}
		});
		const stopButton = chatBar.createEl('button', {text: 'Stop', cls: 'vaultbot-btn vaultbot-btn-quiet vaultbot-btn-stop'});
		stopButton.title = 'Interrupt VaultBot immediately.';
		stopButton.style.display = 'none'; // hidden until a turn is active
		stopButton.addEventListener('click', () => {
			if (ws && ws.readyState === WebSocket.OPEN) {
				ws.send(JSON.stringify({type: 'stop'}));
			}
		});

		// --- Drag & drop file support -----------------------------------
		// Drag files onto the chat bar to insert their vault-relative
		// paths at the cursor position. Works with external files
		// (OS file explorer) and Obsidian file-explorer drags.
		let dragCounter = 0;
		chatBar.addEventListener('dragenter', (e) => {
			e.preventDefault();
			dragCounter++;
			chatBar.addClass('vaultbot-drop-active');
		});
		chatBar.addEventListener('dragover', (e) => {
			e.preventDefault();
			e.dataTransfer.dropEffect = 'copy';
		});
		chatBar.addEventListener('dragleave', (e) => {
			dragCounter--;
			if (dragCounter <= 0) {
				dragCounter = 0;
				chatBar.removeClass('vaultbot-drop-active');
			}
		});
		chatBar.addEventListener('drop', (e) => {
			e.preventDefault();
			dragCounter = 0;
			chatBar.removeClass('vaultbot-drop-active');

			const vaultRoot = this.app.vault.adapter.getBasePath();
			const insertParts = [];

			// 1. External files (OS file explorer): Electron exposes .path on File objects
			if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
				for (let i = 0; i < e.dataTransfer.files.length; i++) {
					const file = e.dataTransfer.files[i];
					if (file.path) {
						let absPath = file.path;
						let relPath = absPath;
						let inVault = absPath.startsWith(vaultRoot);
						if (inVault) {
							relPath = absPath.substring(vaultRoot.length + 1);
						}
						// .md files inside the vault -> wikilink (just the title, no folder, no extension)
						if (inVault && relPath.toLowerCase().endsWith('.md')) {
							const title = relPath.replace(/\.md$/i, '').split('/').pop();
							insertParts.push('[[' + title + ']]');
						} else {
							// Non-md or outside vault -> vault-relative path as-is
							insertParts.push(relPath);
						}
					}
				}
			}

			// 2. Obsidian internal drag: gives obsidian:// URL in text/plain
			if (insertParts.length === 0) {
				const text = e.dataTransfer.getData('text/plain');
				if (text) {
					// Parse obsidian://open?vault=...&file=...
					const m = text.match(/obsidian:\/\/open\?(?:[^&]*&)*file=([^&]+)/);
					if (m) {
						const filePath = decodeURIComponent(m[1]);
						// filePath is vault-relative without extension, e.g. "04-Quality-Gates/Calibration-via-Operator-Feedback"
						const title = filePath.split('/').pop();
						insertParts.push('[[' + title + ']]');
					} else {
						// Not an obsidian:// URL -- use as-is
						insertParts.push(text);
					}
				}
			}

			if (insertParts.length > 0) {
				const cursorPos = input.selectionStart;
				const textBefore = input.value.substring(0, cursorPos);
				const textAfter = input.value.substring(input.selectionEnd);
				const insertText = insertParts.join(' ');
				input.value = textBefore + insertText + textAfter;
				const newPos = cursorPos + insertText.length;
				input.setSelectionRange(newPos, newPos);
				input.focus();
			}
		});

		// Action row: clay (primary) buttons grouped, then moss (quiet)
		// buttons grouped — each cluster sits together.
		const buttonContainer = inputContainer.createDiv({cls: 'vaultbot-button-container'});
		const clayGroup = buttonContainer.createDiv({cls: 'vaultbot-btn-group vaultbot-btn-group-clay'});
		const mossGroup = buttonContainer.createDiv({cls: 'vaultbot-btn-group vaultbot-btn-group-moss'});
		const restartButton = mossGroup.createEl('button', {text: 'Restart', cls: 'vaultbot-btn vaultbot-btn-quiet vaultbot-btn-restart'});
		const diagnoseButton = mossGroup.createEl('button', {text: 'Diagnose', cls: 'vaultbot-btn vaultbot-btn-quiet vaultbot-btn-diagnose'});
		restartButton.title = 'Restart the VaultBot backend. Use this after code changes or if the bot seems stuck. Takes a few seconds.';

		diagnoseButton.title = 'Run a health check. VaultBot looks for common problems (Ollama not running, missing model, port conflict) and shows plain-English fixes.';

		// Restart + Diagnose + Ingest are slash commands now (/restart,

		// /diagnose, /ingest) — the buttons stay in the DOM (the slash-command

		// handler calls restartButton.click()) but are hidden so the bar isn't

		// cluttered with rarely-used buttons. The Call button is the voice

		// entry point.

		restartButton.style.display = 'none';

		diagnoseButton.style.display = 'none';



		// Call button — starts a real-time spoken conversation (Talk Mode).

		// Hidden unless talkMode is on (set in the settings tab). Uses the

		// browser's Web Speech API for both STT + TTS, so there's no server

		// dependency + no blocked DLL. TTS streams in chunks (one sentence at

		// a time) so the voice speaks as the model types — the JARVIS feel.

		const callButton = clayGroup.createEl('button', {text: '📞 Call', cls: 'vaultbot-btn'});

		const muteButton = clayGroup.createEl('button', {text: '🔇', cls: 'vaultbot-btn vaultbot-btn-quiet'});

		let currentAssistantMessage = null;
		let currentThinkingBlock = null;
					let _thinkingActive = false;  // true while receiving thinking chunks; false when answer/tool events arrive
		// Turn state: tracks whether the bot is actively generating, so the
		// inline Stop button only shows when there's something to stop.
		let turnActive = false;
		const setTurnActive = (active) => {
			turnActive = active;
			stopButton.style.display = active ? '' : 'none';
		};
		// ── Voice state (Talk Mode) ──────────────────────────────────

		// The Call button starts a real-time spoken conversation. STT uses the

		// browser's SpeechRecognition for mic capture, then POSTs the audio to

		// /stt (server-side transcription via the configured provider) OR uses

		// the browser's own recognizer when the stt role is the browser.

		// TTS calls /tts per sentence (server-side synthesis via the configured

		// provider — OpenAI, Edge TTS, a local server) and plays the audio as

		// it arrives, so the voice speaks as the model types — chunked, real

		// time. When the tts role is the browser, falls back to speechSynthesis.

		// This is the JARVIS feel with ANY provider, not just the browser.

		let onCall = false;             // in a spoken conversation

		let ttsMuted = false;            // mute toggle

		let ttsSentenceBuffer = '';      // accumulates chunks until a sentence

		let ttsQueue = [];               // sentences waiting to be spoken

		let ttsBusy = false;             // a TTS fetch is in flight

		let ttsAudio = null;             // HTMLAudioElement for server TTS

		let recognition = null;          // SpeechRecognition instance (browser STT)

		let listening = false;           // recognition is active

		let autoListenArmed = false;     // start listening after TTS finishes

		let sttMode = 'browser';         // 'browser' | 'server'

		let ttsMode = 'browser';         // 'browser' | 'server'



		// Show/hide the Call button based on the talk mode setting.

		const talkModeOn = !!(this.plugin.settings.talkMode);

		callButton.style.display = talkModeOn ? '' : 'none';

		muteButton.style.display = 'none';



		// Fetch the current speech role assignments to decide server vs browser.

		const refreshSpeechModes = async () => {

			try {

				const res = await fetch(this.plugin.settings.backendUrl + '/llm/models/all');

				if (!res.ok) return;

				const data = await res.json();

				const roles = data.roles || {};

				const models = data.models || [];

				const sttModel = models.find(m => m.id === roles.stt);

				const ttsModel = models.find(m => m.id === roles.tts);

				sttMode = sttModel && sttModel.provider !== 'browser' ? 'server' : 'browser';

				ttsMode = ttsModel && ttsModel.provider !== 'browser' ? 'server' : 'browser';

			} catch (e) {}

		};

		refreshSpeechModes();



		// ── TTS ────────────────────────────────────────────────────────

		const stopAllTTS = () => {

			ttsSentenceBuffer = '';

			ttsQueue = [];

			ttsBusy = false;

			if (ttsAudio) { try { ttsAudio.pause(); } catch (e) {} }

			if (window.speechSynthesis) { try { window.speechSynthesis.cancel(); } catch (e) {} }

		};



		const playNextTTS = async () => {

			if (ttsBusy || ttsQueue.length === 0) {

				if (ttsQueue.length === 0) {

					// Queue drained — arm auto-listen if enabled.

					if (autoListenArmed && onCall && !ttsMuted && this.plugin.settings.ttsAutoListen) startListening();

				}

				return;

			}

			ttsBusy = true;

			const sentence = ttsQueue.shift();

			try {

				if (ttsMode === 'server') {

					// POST /tts, play the returned audio bytes.

					const r = await fetch(this.plugin.settings.backendUrl + '/tts', {

						method: 'POST', headers: {'Content-Type':'application/json'},

						body: JSON.stringify({text: sentence})

					});

					const ct = r.headers.get('content-type') || '';

					if (ct.indexOf('audio') !== -1) {

						const blob = await r.blob();

						const url = URL.createObjectURL(blob);

						ttsAudio = new Audio(url);

						await new Promise((resolve) => {

							ttsAudio.onended = resolve;

							ttsAudio.onerror = resolve;

							ttsAudio.play().catch(resolve);

						});

						URL.revokeObjectURL(url);

					} else {

						// The server returned JSON (browser sentinel or error) —

						// fall back to browser speechSynthesis for this sentence.

						const j = await r.json().catch(() => ({}));

						if (j.browser) { await speakBrowser(sentence); } else { /* skip */ }

					}

				} else {

					// Browser TTS (speechSynthesis).

					await speakBrowser(sentence);

				}

			} catch (e) {

				// On error, try the browser fallback so a server hiccup

				// doesn't kill the conversation.

				try { await speakBrowser(sentence); } catch (e2) {}

			}

			ttsBusy = false;

			playNextTTS();

		};



		// Speak a sentence via the browser's speechSynthesis (fallback / browser role).

		const speakBrowser = (text) => new Promise((resolve) => {

			if (!window.speechSynthesis || !text || ttsMuted) { resolve(); return; }

			const u = new SpeechSynthesisUtterance(text);

			const name = this.plugin.settings.ttsModel || '';

			const voices = window.speechSynthesis.getVoices();

			const v = voices.find(x => x.name === name);

			if (v) u.voice = v;

			u.rate = 1.0;

			u.onend = resolve;

			u.onerror = resolve;

			window.speechSynthesis.speak(u);

		});



		// Feed a streaming answer chunk into the sentence buffer. When a

		// sentence boundary is hit, queue it for TTS so the voice speaks as

		// the model types — chunked, real-time.

		const feedTTS = (chunk) => {

			if (!onCall || this.plugin.settings.ttsStream === false) return;

			ttsSentenceBuffer += chunk;

			let idx;

			while ((idx = ttsSentenceBuffer.search(/[.!?]\s|\n/)) !== -1) {

				const sentence = ttsSentenceBuffer.slice(0, idx + 2).trim();

				ttsSentenceBuffer = ttsSentenceBuffer.slice(idx + 2);

				if (sentence && !ttsMuted) {

					ttsQueue.push(sentence);

					if (!ttsBusy) playNextTTS();

				}

			}

		};



		// Flush leftover buffered text at answer_done + arm auto-listen.

		const flushTTS = () => {

			if (!onCall) return;

			if (ttsSentenceBuffer.trim()) {

				ttsQueue.push(ttsSentenceBuffer.trim());

				ttsSentenceBuffer = '';

				if (!ttsBusy) playNextTTS();

			}

			if (this.plugin.settings.ttsAutoListen) autoListenArmed = true;

		};



		// ── STT ────────────────────────────────────────────────────────

		// STT captures audio in the browser (mic access is browser-only),

		// then either POSTs it to /stt (server-side transcription) or uses

		// the browser's SpeechRecognition when the stt role is browser.

		const SpeechRecognitionImpl = window.SpeechRecognition || window.webkitSpeechRecognition;

		let mediaRecorder = null;

		let audioChunks = [];



		const buildRecognition = () => {

			if (!SpeechRecognitionImpl) return null;

			const r = new SpeechRecognitionImpl();

			r.lang = this.plugin.settings.sttModel || 'en-US';

			r.continuous = false;

			r.interimResults = false;

			r.maxAlternatives = 1;

			r.onresult = (event) => {

				let transcript = '';

				for (let i = event.resultIndex; i < event.results.length; i++) transcript += event.results[i][0].transcript;

				if (transcript.trim()) { const heard = transcript.trim(); input.value = heard; new Notice('Heard: ' + heard); statusEl.setText('📞 Heard: ' + heard); send('chat'); }

			};

			r.onerror = (event) => { listening = false; statusEl.setText('Call — mic error: ' + (event.error || 'unknown')); };

			r.onend = () => { listening = false; if (onCall) statusEl.setText('📞 On call — say something (click Call to listen)'); };

			return r;

		};



		// Server-side STT: record via MediaRecorder, POST the blob to /stt.

		const startServerListening = async () => {

			try {

				const stream = await navigator.mediaDevices.getUserMedia({audio: true});

				audioChunks = [];

				mediaRecorder = new MediaRecorder(stream);

				mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.push(e.data); };

				mediaRecorder.onstop = async () => {

					stream.getTracks().forEach(t => t.stop());

					const blob = new Blob(audioChunks, {type: mediaRecorder.mimeType || 'audio/webm'});

					if (!blob.size) { listening = false; if (onCall) statusEl.setText('📞 On call — no audio captured'); return; }

					statusEl.setText('📞 Transcribing...');

					try {

						const r = await fetch(this.plugin.settings.backendUrl + '/stt', {method: 'POST', body: blob});

						const j = await r.json();

						if (j.browser) {

							// The stt role switched back to browser; fall back.

							if (!recognition) recognition = buildRecognition();

							if (recognition) { try { recognition.start(); listening = true; } catch (e) {} }

						} else if (j.text && j.text.trim()) {

							const heard = j.text.trim();

							input.value = heard;

							new Notice('Heard: ' + heard);

							statusEl.setText('?? Heard: ' + heard);

							send('chat');

						} else {

							statusEl.setText('📞 No transcription: ' + (j.error || 'empty'));

						}

					} catch (e) {

						statusEl.setText('📞 STT failed: ' + (e.message || e));

					}

					listening = false;

					if (onCall) statusEl.setText('📞 On call — say something (click Call to listen)');

				};

				mediaRecorder.start();

				listening = true;

				statusEl.setText('🎤 Listening... (speak, then pause)');

				// Auto-stop after 6s of silence-ish (MediaRecorder has no VAD);

				// the user can also click Call again to stop early.

				setTimeout(() => { if (mediaRecorder && mediaRecorder.state === 'recording') mediaRecorder.stop(); }, 8000);

			} catch (e) {

				listening = false;

				statusEl.setText('🎤 Mic error: ' + (e.message || e));

			}

		};



		const startListening = () => {

			if (listening) return;

			stopAllTTS();

			autoListenArmed = false;

			// Refresh the mode in case the user changed the stt role.

			refreshSpeechModes().then(() => {

				if (sttMode === 'server') {

					startServerListening();

				} else {

					if (!SpeechRecognitionImpl) { new Notice('Browser STT unavailable. Set an STT provider in Settings, or use Chrome/Edge.'); return; }

					if (!recognition) recognition = buildRecognition();

					if (!recognition) return;

					try { recognition.lang = this.plugin.settings.sttModel || 'en-US'; recognition.start(); listening = true; statusEl.setText('🎤 Listening... (speak, then pause)'); }

					catch (e) { listening = false; }

				}

			});

		};

		const stopListening = () => {

			if (mediaRecorder && mediaRecorder.state === 'recording') { try { mediaRecorder.stop(); } catch (e) {} }

			if (recognition && listening) { try { recognition.stop(); } catch (e) {} }

			listening = false;

		};



		// Toggle the call: on = start a spoken conversation, off = hang up.

		const toggleCall = () => {

			if (!onCall) {

				onCall = true;

				callButton.setText('📞 Hang up');

				muteButton.style.display = '';

				statusEl.setText('📞 On call — say something (click Call to listen)');

				startListening();

			} else {

				onCall = false;

				callButton.setText('📞 Call');

				muteButton.style.display = 'none';

				stopListening();

				stopAllTTS();

				autoListenArmed = false;

				statusEl.setText('Call ended');

			}

		};

		callButton.addEventListener('click', toggleCall);

		muteButton.addEventListener('click', () => {

			ttsMuted = !ttsMuted;

			muteButton.setText(ttsMuted ? '🔈' : '🔇');

			if (ttsMuted) stopAllTTS();

		});;
		// Thinking blocks: shown live while the model reasons, then auto-
		// collapse when the actual answer starts streaming so they don't
		// clutter the chat. The header stays as a clickable toggle so the
		// user can re-expand any past thinking block.
		let currentThinkingHeader = null;
		const setThinkingVisible = (visible) => {
			if (!currentThinkingBlock || !currentThinkingHeader) return;
			currentThinkingBlock.style.display = visible ? 'block' : 'none';
			currentThinkingHeader.textContent = visible
				? 'Thinking (click to hide)'
				: 'Thinking (click to show)';
		};
		// In-order streaming: text is rendered into a *segment* element that
		// is created fresh whenever the model starts talking again (after a
		// tool call, etc.) and appended at the END of the message at that
		// moment. Tool calls/results/progress lines are also appended at the
		// end when they happen. This preserves the true talk→tool→talk→tool
		// order instead of clustering all text above all tool calls.
		let currentAnswerBlock = null;     // current text-segment container
		let currentSegmentText = '';       // markdown accumulated for the segment
		let currentSegmentRenderTimer = null;
		let currentAnswerText = '';        // full plain text across all segments
		// Live activity line: shows the current stage + elapsed time, updated
		// in place by progress/heartbeat events so the user is never staring
		// at a frozen "Calling X..." with no idea if it's still working.
		let currentActivityEl = null;
		let currentToolCallEl = null;   // collapsible tool-call container (header + body)
		let activityStartTs = 0;
		let activityTimer = null;

		const appendUserMessage = (text) => {
			const div = chatContainer.createDiv({cls: 'vaultbot-message user'});
			renderMarkdownInto(div, text).then(() => {
				// Force scroll on user send — user explicitly wants to see the response
				chatContainer.scrollTop = chatContainer.scrollHeight;
			});
		};

		// Render markdown into an element using Obsidian's renderer so
		// tables, lists, code blocks, blockquotes, etc. display properly.
		// Falls back to escaped plain text if the renderer is unavailable.
		const renderMarkdownInto = async (el, text) => {
			el.empty();
			const md = (text || '').trimEnd();
			if (!md) return;
			if (MarkdownRenderer && typeof MarkdownRenderer.renderMarkdown === 'function') {
				try {
					await MarkdownRenderer.renderMarkdown(md, el, '', this);
					return;
				} catch (e) { /* fall through to plain text */ }
			}
			// Fallback: escaped text with line breaks preserved.
			const pre = el.createEl('div');
			pre.style.whiteSpace = 'pre-wrap';
			pre.textContent = md;
		};

		// (Re)render the current text segment from its accumulated markdown.
		// Debounced so rapid chunks don't thrash the renderer.
		const scheduleSegmentRender = (immediate) => {
			if (currentSegmentRenderTimer) {
				window.clearTimeout(currentSegmentRenderTimer);
				currentSegmentRenderTimer = null;
			}
			const run = () => {
				currentSegmentRenderTimer = null;
				if (!currentAnswerBlock) return;
				renderMarkdownInto(currentAnswerBlock, currentSegmentText).then(() => {
					smartScrollToBottom();
				});
			};
			if (immediate) run();
			else currentSegmentRenderTimer = window.setTimeout(run, 60);
		};

		// Close the current text segment so the next event (tool call,
		// result, progress) is appended AFTER the text, preserving order.
		const closeCurrentSegment = () => {
			if (currentSegmentRenderTimer) {
				window.clearTimeout(currentSegmentRenderTimer);
				currentSegmentRenderTimer = null;
			}
			if (currentAnswerBlock && currentSegmentText) {
				// Final render in case a debounced one is pending.
				renderMarkdownInto(currentAnswerBlock, currentSegmentText).then(() => {
					smartScrollToBottom();
				});
			}
			currentAnswerBlock = null;
			currentSegmentText = '';
		};

		// Render a complete assistant message in one shot (used by the
		// ingest button's status reply, which isn't streamed). Mirrors the
		// streaming path's markup so styling stays consistent.
		const appendAssistantMessage = (text) => {
			const div = chatContainer.createDiv({cls: 'vaultbot-message assistant'});
			const block = div.createEl('div', {cls: 'vaultbot-answer-block'});
			renderMarkdownInto(block, text).then(() => {
				smartScrollToBottom();
			});
			smartScrollToBottom();
			// Return the block so callers (e.g. the Restart button) can
			// update its text in place as a status line changes.
			return block;
		};

		// --- Problem card renderer (the "never show a stack trace" rule) ---
		// renderProblem takes a Diagnosis object (from a WS type:"problem"
		// event or a /diagnose result) and renders a styled, categorized
		// card instead of a raw "Error: <traceback>" bubble. It:
		//   1. Shows the plain-English user_message prominently.
		//   2. Shows the remedy_hint as a sub-line when present.
		//   3. Offers a one-click action button when `action` names a
		//      known in-product remedy (restart / pull_model / finish_setup /
		//      open_download_ollama / open_download_python / restore_backup).
		//   4. Offers "Copy for support" that copies a REDACTED bundle
		//      (category + user_message + timestamp) — never raw_for_log,
		//      never paths, never keys — so the operator can paste it to a helper.
		// This is the frontend half of the classify-at-the-edge contract:
		// the backend translates exceptions into Diagnoses; the frontend
		// renders Diagnoses. Raw strings never cross the boundary.
		const ACTION_LABELS = {
			'restart': 'Restart',
			'pull_model': 'Download model',
			'finish_setup': 'Finish setup',
			'open_download_ollama': 'Get Ollama',
			'open_download_python': 'Get Python',
			'repair_faiss': 'Repair',
			'move_vault': 'Move my vault',
			'restore_backup': 'Restore last version'
		};
		const ACTION_HANDLERS = {
			'restart': () => { if (typeof restartButton !== 'undefined') restartButton.click(); },
			'pull_model': () => { /* Phase 4: wire to /models/pull */ new Notice('Use the model picker to download a model.'); },
			'finish_setup': () => { this.plugin._showSetupNeededModal(); },
			'open_download_ollama': () => { window.open('https://ollama.com', '_blank'); },
			'open_download_python': () => { window.open('https://python.org/downloads', '_blank'); },
			'repair_faiss': () => { new Notice('Run the installer again to repair libraries.'); },
			'move_vault': () => { new Notice('Move your vault folder out of the sync folder, then reopen it in Obsidian.'); },
			'restore_backup': () => {
				// Call POST /update/rollback to restore the latest backup,
				// then restart the backend so the old code takes effect.
				(async () => {
					try {
						new Notice('Restoring last version...');
						const resp = await fetch(
							this.backendUrl + '/update/rollback', {method: 'POST'});
						const data = await resp.json();
						if (data.status === 'ok') {
							new Notice(`Restored ${data.restored} file(s) from backup. Restarting...`);
							// Restart the backend so the restored code runs.
							if (typeof restartButton !== 'undefined') {
								restartButton.click();
							} else {
								await this.plugin.restartBackend();
							}
						} else if (data.status === 'no_backup') {
							new Notice('No backup found. Nothing to restore.');
						} else {
							new Notice('Restore failed: ' + (data.error || 'unknown error'));
						}
					} catch (e) {
						new Notice('Restore failed: ' + (e.message || e));
					}
				})();
			}
		};
		const renderProblem = (diagnosis) => {
			const d = diagnosis || {};
			const sev = d.severity || 'broken';
			const card = chatContainer.createDiv({cls: 'vaultbot-message system problem severity-' + sev});
			const icon = sev === 'info' ? '🌿' : (sev === 'fixable' ? '⚙️' : '⚠️');
			const head = card.createDiv({cls: 'vaultbot-problem-head'});
			head.createSpan({cls: 'vaultbot-problem-icon', text: icon});
			head.createSpan({cls: 'vaultbot-problem-title', text: d.user_message || 'Something went wrong.'});
			if (d.remedy_hint) {
				const r = card.createDiv({cls: 'vaultbot-problem-remedy', text: d.remedy_hint});
			}
			const btnRow = card.createDiv({cls: 'vaultbot-problem-actions'});
			if (d.action && ACTION_LABELS[d.action]) {
				const actBtn = btnRow.createEl('button', {text: ACTION_LABELS[d.action], cls: 'mod-cta'});
				actBtn.addEventListener('click', () => {
					const h = ACTION_HANDLERS[d.action];
					if (h) { try { h(); } catch (e) { console.warn('problem action failed', e); } }
				});
			}
			const copyBtn = btnRow.createEl('button', {text: 'Copy for support', cls: 'vaultbot-btn-quiet'});
			copyBtn.addEventListener('click', () => {
				// Redacted bundle: no raw_for_log, no paths, no keys.
				// Read the version from the Obsidian plugin manifest (the
				// Plugin instance doesn't expose .manifest directly, so we
				// read it from the app's plugin manifests registry).
				let ver = '?';
				try {
					const manifests = this.plugin.app.plugins.manifests;
					if (manifests && manifests['vaultbot'])
						ver = manifests['vaultbot'].version || '?';
				} catch (e) {}
				const bundle = JSON.stringify({
					category: d.category || 'generic',
					severity: sev,
					message: d.user_message || '',
					time: new Date().toISOString(),
					version: ver
				}, null, 2);
				try {
					navigator.clipboard.writeText(bundle);
					copyBtn.setText('Copied!');
					setTimeout(() => copyBtn.setText('Copy for support'), 2000);
				} catch (e) {
					new Notice('Could not copy — see the console.');
				}
			});
			smartScrollToBottom();
			return card;
		};

		const startAssistantMessage = () => {
			currentAssistantMessage = chatContainer.createDiv({cls: 'vaultbot-message assistant'});
			// Create per-message locals so the click handler closes over
			// THESE specific elements — not the module-level vars that get
			// nulled when the turn ends. This keeps every past thinking
			// block clickable even after the turn is over.
			const thinkingHeader = currentAssistantMessage.createEl('div', {cls: 'vaultbot-thinking-header', text: 'Thinking (click to show)'});
			const thinkingBlock = currentAssistantMessage.createEl('div', {cls: 'vaultbot-thinking-block'});
			thinkingBlock.style.display = 'none';
			thinkingHeader.addEventListener('click', () => {
				const hidden = thinkingBlock.style.display === 'none';
				thinkingBlock.style.display = hidden ? 'block' : 'none';
				thinkingHeader.textContent = hidden
					? 'Thinking (click to hide)'
					: 'Thinking (click to show)';
			});
			// Expose to the streaming handlers via the module-level refs.
			currentThinkingHeader = thinkingHeader;
			currentThinkingBlock = thinkingBlock;
			// NOTE: no answer block is created up front. Text segments are
			// created on demand so they sit in true stream order relative to
			// tool calls, instead of always above them.
			smartScrollToBottom();
		};

		// --- Live activity line (kills the black box) ---
		// A single mutable line that shows the current stage + a running
		// elapsed-time counter, updated in place by progress/heartbeat
		// events. Cleared when the activity completes or the answer is done.
		const fmtMs = (ms) => {
			const s = Math.floor(ms / 1000);
			if (s < 60) return s + 's';
			return Math.floor(s / 60) + 'm' + String(s % 60).padStart(2, '0') + 's';
		};
		const startActivity = (label, detail) => {
			if (!currentAssistantMessage) startAssistantMessage();
			if (!currentActivityEl) {
				currentActivityEl = currentAssistantMessage.createDiv({cls: 'vaultbot-activity'});
			}
			activityStartTs = Date.now();
			if (activityTimer) { window.clearInterval(activityTimer); activityTimer = null; }
			activityTimer = window.setInterval(() => updateActivity(label), 250);
			updateActivity(label, detail);
		};
		const updateActivity = (label, detail) => {
			if (!currentActivityEl) return;
			const elapsed = Date.now() - activityStartTs;
			let text = '... ' + label + '  [' + fmtMs(elapsed) + ']';
			if (detail) {
				const parts = [];
				for (const k of ['instruction', 'status', 'step_type', 'output', 'round', 'max_rounds', 'new_sources', 'total_sources', 'sources', 'follow_up_sources', 'url', 'title', 'note', 'total_notes', 'total', 'query', 'facts', 'source_count', 'outbound_links', 'amem_evolved', 'amem_links', 'silent_ms', 'chunks', 'node_count', 'duration_ms', 'original_tokens', 'budgeted_tokens', 'chars', 'kept', 'count']) {
					if (detail[k] !== undefined && detail[k] !== null) {
						let v = detail[k];
						if (typeof v === 'string' && v.length > 60) v = v.slice(0, 57) + '...';
						parts.push(k + '=' + v);
					}
				}
				if (parts.length) text += '\n   ' + parts.join(' | ');
			}
			currentActivityEl.setText(text);
			smartScrollToBottom();
		};
		const endActivity = (summary) => {
			if (activityTimer) { window.clearInterval(activityTimer); activityTimer = null; }
			if (currentActivityEl) {
				if (summary) {
					const elapsed = Date.now() - activityStartTs;
					currentActivityEl.setText('Done: ' + summary + '  [' + fmtMs(elapsed) + ']');
					currentActivityEl.style.opacity = '0.6';
				}
				currentActivityEl = null;
			}
			// Finalize any pending collapsible tool-call element.
			if (currentToolCallEl) {
				const elapsed = Date.now() - currentToolCallEl.startTs;
				currentToolCallEl.header.setText('🔧 ' + currentToolCallEl.toolName + '  [' + fmtMs(elapsed) + ']');
				currentToolCallEl = null;
			}
		};

		let ws = null;
		// Backend-provided session_id for the current chat session.
		let currentSessionId = null;
		const connectWebSocket = () => {
			// Don't touch a socket that's already OPEN or still CONNECTING; closing
			// a connecting socket yields 'closed before the connection is established'.
			if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
				return;
			}
			if (ws) {
				try { ws.close(); } catch (e) {}
			}
			const wsUrl = this.backendUrl.replace('http', 'ws') + '/ws';
			ws = new WebSocket(wsUrl);
			ws.onopen = () => {
				statusEl.setText('Connected to VaultBot backend');
			};
			ws.onmessage = (event) => {
				let msg;
				try {
					msg = JSON.parse(event.data);
				} catch (e) {
					if (!currentAssistantMessage) startAssistantMessage();
					if (!currentAnswerBlock) {
						currentAnswerBlock = currentAssistantMessage.createEl('div', {cls: 'vaultbot-answer-block'});
						currentSegmentText = '';
					}
					currentSegmentText += event.data;
					currentAnswerText += event.data;
					scheduleSegmentRender();
					smartScrollToBottom();
					return;
				}

				if (msg.type === 'status') {
					statusEl.setText(msg.content);
				} else if (msg.type === 'thinking') {
					if (!currentAssistantMessage) startAssistantMessage();
					// Only create a NEW thinking block if thinking is RESTARTING
					// after a gap (answer_chunk, tool_call, etc.). During an
					// active thinking phase, _thinkingActive is true and we
					// just append to the existing block. This prevents a new
					// thinking section being created for every word/chunk.
					if (currentThinkingBlock && currentThinkingBlock.getText() && !_thinkingActive) {
						setThinkingVisible(false);
						// Create a fresh thinking block for this new phase.
						const newHeader = currentAssistantMessage.createEl('div', {cls: 'vaultbot-thinking-header', text: 'Thinking (click to show)'});
						const newBlock = currentAssistantMessage.createEl('div', {cls: 'vaultbot-thinking-block'});
						newBlock.style.display = 'none';
						newHeader.addEventListener('click', () => {
							const hidden = newBlock.style.display === 'none';
							newBlock.style.display = hidden ? 'block' : 'none';
							newHeader.textContent = hidden
								? 'Thinking (click to hide)'
								: 'Thinking (click to show)';
						});
						currentThinkingHeader = newHeader;
						currentThinkingBlock = newBlock;
					}
					// Mark that we're in an active thinking phase.
					_thinkingActive = true;
					// Show the thinking block live while the model reasons.
					setThinkingVisible(true);
					currentThinkingBlock.setText((currentThinkingBlock.getText() || '') + msg.content);
					smartScrollToBottom();				} else if (msg.type === 'answer_chunk') {
					_thinkingActive = false;
					if (!currentAssistantMessage) startAssistantMessage();
					// Some models embed reasoning as <think>...</think> tags
					// inside the content stream instead of a separate thinking
					// field. Strip those out of the answer text and route them
					// to the thinking block so they don't render as visible
					// answer text. We track whether we're inside an open tag
					// across chunks (a tag can span multiple chunks).
					let raw = msg.content || '';
					if (raw) {
						// Handle <think> / </think> tags that may span chunks.
						// this._inThinkTag persists across answer_chunk calls.
						if (this._inThinkTag === undefined) this._inThinkTag = false;
						let outText = '';
						// If we were inside a think tag from a previous chunk,
						// everything up to the next </think> is thinking.
						if (this._inThinkTag) {
							const closeIdx = raw.indexOf('</think>');
							if (closeIdx === -1) {
								// Still inside: all thinking.
								setThinkingVisible(true);
								currentThinkingBlock.setText((currentThinkingBlock.getText() || '') + raw);
								raw = '';
							} else {
								setThinkingVisible(true);
								currentThinkingBlock.setText((currentThinkingBlock.getText() || '') + raw.slice(0, closeIdx));
								raw = raw.slice(closeIdx + 8);
								this._inThinkTag = false;
							}
						}
						// Now parse any new <think>...</think> in the remainder.
						while (raw) {
							const openIdx = raw.indexOf('<think>');
							if (openIdx === -1) {
								outText += raw;
								break;
							}
							outText += raw.slice(0, openIdx);
							const afterOpen = raw.slice(openIdx + 7);
							const closeIdx = afterOpen.indexOf('</think>');
							if (closeIdx === -1) {
								// Tag spans into the next chunk.
								setThinkingVisible(true);
								currentThinkingBlock.setText((currentThinkingBlock.getText() || '') + afterOpen);
								this._inThinkTag = true;
								raw = '';
							} else {
								setThinkingVisible(true);
								currentThinkingBlock.setText((currentThinkingBlock.getText() || '') + afterOpen.slice(0, closeIdx));
								raw = afterOpen.slice(closeIdx + 8);
							}
						}
						raw = outText;
					}
					if (!raw) {
						smartScrollToBottom();
						return;
					}
					// The real answer is starting: auto-collapse the thinking
					// block so it doesn't clutter the chat. The header stays
					// as a clickable toggle so the user can re-expand it.
					setThinkingVisible(false);
					// A new text segment: create one (after any preceding tool
					// call/result/progress line) so the text appears IN ORDER
					// relative to tool activity, not always above it.
					if (!currentAnswerBlock) {
						currentAnswerBlock = currentAssistantMessage.createEl('div', {cls: 'vaultbot-answer-block'});
						currentSegmentText = '';
					}
					currentSegmentText += raw;
					currentAnswerText += raw;
					scheduleSegmentRender();
					feedTTS(raw);
					smartScrollToBottom();
				} else if (msg.type === 'tool_call') {
					_thinkingActive = false;
					if (!currentAssistantMessage) startAssistantMessage();
					// The model moved past reasoning to act: auto-collapse the
					// thinking block (it may have been left open if there was
					// no answer_chunk between thinking and the tool call).
					setThinkingVisible(false);
					// Close any open text segment so this tool call is appended
					// AFTER the text that preceded it (true stream order).
					closeCurrentSegment();
					const toolName = msg.tool || 'tool';
					const argsStr = msg.args ? JSON.stringify(msg.args) : '';
					const shortArgs = argsStr.length > 80 ? argsStr.slice(0, 77) + '…' : argsStr;
					// Collapsible tool-call container: header is always visible,
					// body (args + result) is hidden until clicked.
					const container = currentAssistantMessage.createDiv({cls: 'vaultbot-tool-call'});
					const header = container.createDiv({cls: 'vaultbot-tool-call-header'});
					header.setText('🔧 ' + toolName + (shortArgs ? ': ' + shortArgs : '') + ' …');
					const body = container.createDiv({cls: 'vaultbot-tool-call-body'});
					body.style.display = 'none';
					if (argsStr) {
						const argsDiv = body.createDiv({cls: 'vaultbot-tool-call-args'});
						argsDiv.setText(argsStr);
					}
					// Click header to toggle expand/collapse.
					header.addEventListener('click', () => {
						const hidden = body.style.display === 'none';
						body.style.display = hidden ? 'block' : 'none';
						// Refresh header text (elapsed may have updated).
						const el = currentToolCallEl;
						if (el && el.toolName === toolName) {
							const e = Date.now() - el.startTs;
							header.setText((hidden ? '🔽 ' : '🔧 ') + toolName + (shortArgs ? ': ' + shortArgs : '') + '  [' + fmtMs(e) + ']');
						} else {
							header.setText((hidden ? '🔽 ' : '🔧 ') + toolName + (shortArgs ? ': ' + shortArgs : ''));
						}
					});
					currentToolCallEl = { container, header, body, toolName, startTs: Date.now() };
					smartScrollToBottom();
				} else if (msg.type === 'progress') {
					// Granular stage events from the backend (research rounds,
					// scraping, synthesis, gap fill, note writing, A-MEM).
					startActivity(msg.stage, msg.detail || {});
				} else if (msg.type === 'procedure_step') {
					// Live procedure step visibility — the user sees every
					// step of every procedure running in preflight or during
					// the agentic loop. Shows procedure name, step number,
					// instruction, and status. Kills the black box.
					const proc = msg.procedure || 'procedure';
					const step = msg.step || '?';
					const total = msg.total || '?';
					const instruction = msg.instruction || '';
					const status = msg.status || 'running';
					const label = proc + ' step ' + step + '/' + total;
					const detail = {
						instruction: instruction,
						status: status,
						step_type: msg.step_type || '',
					};
					if (msg.output_preview) {
						detail.output = msg.output_preview;
					}
					startActivity(label, detail);
				} else if (msg.type === 'heartbeat') {
					// Periodic "still alive" pulse during long silent waits.
					// Carries elapsed_ms + how long since the last output.
					const label = msg.label || 'working';
					const detail = {silent_ms: msg.silent_ms, chunks: msg.chunks};
					if (!currentActivityEl) {
						startActivity(label, detail);
					} else {
						// Keep the existing label but refresh elapsed + detail.
						updateActivity(label, detail);
					}
				} else if (msg.type === 'tool_result') {
					_thinkingActive = false;
					if (!currentAssistantMessage) startAssistantMessage();
					closeCurrentSegment();
					const summary = msg.summary || 'done';
					const toolName = msg.tool || 'tool';
					if (currentToolCallEl && currentToolCallEl.toolName === toolName) {
						// Populate the matching collapsible tool-call body.
						const elapsed = Date.now() - currentToolCallEl.startTs;
						const resultDiv = currentToolCallEl.body.createDiv({cls: 'vaultbot-tool-call-result'});
						resultDiv.setText(summary);
						currentToolCallEl.header.setText('🔧 ' + toolName + '  [' + fmtMs(elapsed) + ']');
						currentToolCallEl = null;
					} else {
						// No matching tool_call (e.g. reconnected mid-stream):
						// create a standalone collapsible result.
						const container = currentAssistantMessage.createDiv({cls: 'vaultbot-tool-call'});
						const header = container.createDiv({cls: 'vaultbot-tool-call-header'});
						header.setText('🔧 ' + toolName);
						const body = container.createDiv({cls: 'vaultbot-tool-call-body'});
						body.style.display = 'none';
						const resultDiv = body.createDiv({cls: 'vaultbot-tool-call-result'});
						resultDiv.setText(summary);
						header.addEventListener('click', () => {
							const hidden = body.style.display === 'none';
							body.style.display = hidden ? 'block' : 'none';
							header.setText((hidden ? '🔽 ' : '🔧 ') + toolName);
						});
					}
					smartScrollToBottom();
				} else if (msg.type === 'context_usage') {
					// Live token-usage meter update from the backend. Fires
					// each turn (pre-loop + post-answer) carrying the model's
					// context window + estimated used tokens.
					if (typeof msg.context_window === 'number') {
						updateTokenMeter(msg.used_tokens || 0, msg.context_window);
					}
				} else if (msg.type === 'ollama_stats') {
					// Per-round Ollama eval stats from the backend (tokens/s,
					// load time, prompt processing speed). Updates the perf
					// section of the stats bar.
					updateStatsFromChat(msg);
				} else if (msg.type === 'answer_done') {
					_thinkingActive = false;
					endActivity();
					// Flush the final text segment so its markdown renders.
					closeCurrentSegment();
					statusEl.setText('Done');
					flushTTS();
					setTurnActive(false);
					currentAssistantMessage = null;
					currentThinkingBlock = null;
					currentAnswerBlock = null;
					currentSegmentText = '';
					currentAnswerText = '';
				} else if (msg.type === 'error') {
					endActivity();
					setTurnActive(false);
					// Legacy raw-error path: render as a problem card too, so
					// even unclassified errors get the humane treatment. New
					// backend code should emit type:"problem" with a real
					// diagnosis; this wraps raw strings in a generic one.
					statusEl.setText('Problem');
					renderProblem({
						category: 'generic',
						severity: 'broken',
						user_message: msg.content || 'Something went wrong.',
						remedy_hint: 'Try Restart below. If it keeps happening, use Copy for support.',
						action: 'restart',
						raw_for_log: ''
					});
					smartScrollToBottom();
				} else if (msg.type === 'problem') {
					// Typed, classified failure from the backend (see
					// diagnostics.py). The diagnosis carries a plain-English
					// user_message + a remedy_hint + an action token; we render
					// it as a styled card instead of a raw "Error: ..." bubble.
					// This is the "average user never sees a stack trace" rule
					// enforced at the UI boundary.
					endActivity();
					setTurnActive(false);
					const d = msg.diagnosis || {};
					statusEl.setText((d.user_message || 'Problem').split('.')[0] + '.');
					renderProblem(d);
					smartScrollToBottom();
				} else if (msg.type === 'system_info') {
					// Non-error informational messages (/help output, "all
					// healthy"). Rendered as a quiet system bubble, distinct
					// from error/problem cards.
					const div = chatContainer.createDiv({cls: 'vaultbot-message system info'});
					renderMarkdownInto(div, msg.content || '').then(() => {
						smartScrollToBottom();
					});
				} else if (msg.type === 'stopped') {
					// Backend confirmed an interrupt (stop button or new msg).
					endActivity();
					setTurnActive(false);
					// Flush whatever text was streamed so far so it stays visible.
					closeCurrentSegment();
					statusEl.setText('Stopped');
					_thinkingActive = false;
					currentAssistantMessage = null;
					currentThinkingBlock = null;
					currentAnswerBlock = null;
					currentSegmentText = '';
					currentAnswerText = '';
				} else if (msg.type === 'session_info') {
				// Backend sent session metadata (id + title). Update the title
				// display so the user knows which session they're in.
					if (msg.title) sessionTitleEl.setText(msg.title);
					if (msg.session_id) {
						currentSessionId = msg.session_id;
						sessionIdBtn.title = 'Click to copy: ' + currentSessionId;
						sessionIdBtn.setText('ID: ' + currentSessionId.substring(0, 8));
					}
			} else if (msg.type === 'session_reset') {
					// /new command: clear the chat UI for a fresh session.
					endActivity();
					setTurnActive(false);
					chatContainer.empty();
					_thinkingActive = false;
					currentAssistantMessage = null;
					currentThinkingBlock = null;
					currentAnswerBlock = null;
					currentSegmentText = '';
					currentSegmentRenderTimer = null;
					currentAnswerText = '';
					statusEl.setText('New session');
				sessionTitleEl.setText('New Session');
				sessionIdBtn.setText('Session ID');
				sessionIdBtn.title = 'Click to copy the session ID to clipboard';
				currentSessionId = null;
					const div = chatContainer.createDiv({cls: 'vaultbot-message system'});
					div.createSpan({text: msg.content || 'New session started.'});
					smartScrollToBottom();
				} else if (msg.type === 'restart') {
					// Backend requested restart — same code path as the GUI button.
					statusEl.setText('Backend requested restart...');
					this.plugin.restartBackend();
				} else if (msg.type === 'reload_plugin') {
					// Backend requested plugin reload (disable + re-enable).
					// This picks up changes to main.js/styles.css without you
					// having to manually toggle the plugin. The backend stays
					// running during the reload.
					statusEl.setText('Reloading plugin...');
					this.plugin.reloadSelf();
				} else if (msg.type === 'model_pull_progress') {
				// Live progress from a /models/pull background thread.
				// Shown as a status line in the chat (not a problem card) so
				// the user sees the download is happening without a separate
				// modal. The download modal also polls /models to detect
				// completion, so this is a secondary indicator.
				if (msg.progress >= 0) {
					setStatus('starting', `Downloading ${msg.model}: ${msg.progress}%`);
				} else {
					setStatus('starting', `Downloading ${msg.model}...`);
				}
			} else if (msg.type === 'model_pull_done') {
				// Model pull completed (success or failure). Refresh the
				// dropdown so the new model appears, and clear the status.
				if (msg.success) {
					setStatus('online', `Model ${msg.model} ready.`);
					refreshModels();
				} else {
					setStatus('error', `Could not download ${msg.model}. Check that Ollama is running and try again.`);
				}
		} else if (msg.type === 'user_questionnaire') {
				// Interactive question card from the ask_user tool.
				// Renders radio/text questions with an "I don't know, use
				// best practices" option and a free-text comments field.
				// The submit button sends answers via WebSocket (same
				// channel as normal chat messages).
				//
				// Close the current assistant message so the card appears
				// AFTER any text already streamed, and null it out so the
				// post-submit reply creates a NEW assistant message below
				// the card — not appended to the old one above.
				closeCurrentSegment();
				currentAssistantMessage = null;
				currentThinkingBlock = null;
				currentThinkingHeader = null;
				currentAnswerBlock = null;
				currentSegmentText = '';

				const _backendUrl = this.backendUrl;
				const requestId = msg.request_id || '';
				const title = msg.title || 'I need your input';
				const context = msg.context || '';
				const questions = msg.questions || [];

				const card = chatContainer.createDiv({cls: 'vaultbot-message system questionnaire'});
				const header = card.createDiv({cls: 'vaultbot-questionnaire-header'});
				header.createSpan({cls: 'vaultbot-questionnaire-icon', text: '\u2753'});
				header.createSpan({cls: 'vaultbot-questionnaire-title', text: title});

				if (context) {
					const ctx = card.createDiv({cls: 'vaultbot-questionnaire-context'});
					ctx.setText(context);
				}

				const form = card.createDiv({cls: 'vaultbot-questionnaire-form'});
				const answers = {};

				for (const q of questions) {
					const qDiv = form.createDiv({cls: 'vaultbot-questionnaire-q'});
					const qLabel = qDiv.createDiv({cls: 'vaultbot-questionnaire-q-label'});
					qLabel.setText(q.question);

					if (q.type === 'radio' && q.options && q.options.length > 0) {
						const optsDiv = qDiv.createDiv({cls: 'vaultbot-questionnaire-opts'});
						const allOpts = ['__best_practices__', ...q.options];
						const labels = ['idk', ...q.options];
						const defaultVal = q.default === 'best_practices' ? '__best_practices__' : (q.default || q.options[0]);

						for (let i = 0; i < allOpts.length; i++) {
							const optRow = optsDiv.createDiv({cls: 'vaultbot-questionnaire-opt'});
							const radio = optRow.createEl('input', {
								type: 'radio',
								attr: {name: 'q_' + q.id, value: allOpts[i]}
							});
							if (allOpts[i] === defaultVal) radio.checked = true;
							radio.addEventListener('change', () => {
								answers[q.id] = allOpts[i];
							});
							optRow.createSpan({text: labels[i]});
						}
						answers[q.id] = defaultVal;
					} else if (q.type === 'text') {
						const textarea = qDiv.createEl('textarea', {
							cls: 'vaultbot-questionnaire-textarea',
							attr: {placeholder: 'Your thoughts...', rows: '3'}
						});
						if (q.default && q.default !== 'best_practices') {
							textarea.value = q.default;
						}
						textarea.addEventListener('input', () => {
							answers[q.id] = textarea.value;
						});
						answers[q.id] = textarea.value || '';
					}
				}

				const commentDiv = form.createDiv({cls: 'vaultbot-questionnaire-q'});
				commentDiv.createDiv({cls: 'vaultbot-questionnaire-q-label', text: 'Additional comments or nuances'});
				const commentArea = commentDiv.createEl('textarea', {
					cls: 'vaultbot-questionnaire-textarea',
					attr: {placeholder: 'Any constraints, conditions, or creative ideas...', rows: '3'}
				});

				const btnRow = form.createDiv({cls: 'vaultbot-questionnaire-actions'});
				const submitBtn = btnRow.createEl('button', {text: 'Submit answers', cls: 'mod-cta'});
				submitBtn.addEventListener('click', () => {
					if (!ws || ws.readyState !== WebSocket.OPEN) {
						submitBtn.setText('Not connected \u2014 try again');
						setTimeout(() => {
							submitBtn.removeAttribute('disabled');
							submitBtn.setText('Submit answers');
						}, 2000);
						return;
					}
					submitBtn.setAttribute('disabled', 'disabled');
					submitBtn.setText('Submitted \u2713');
					card.querySelectorAll('input, textarea, button').forEach(el => {
						el.setAttribute('disabled', 'disabled');
					});
					ws.send(JSON.stringify({
						type: 'user_response',
						request_id: requestId,
						answers: answers,
						comments: commentArea.value || ''
					}));
				});

				smartScrollToBottom();
			} else if (msg.type === 'vault_changed') {
					// The backend wrote files directly to disk, bypassing
					// Obsidian's vault API. Obsidian's file watcher may not
					// detect these changes immediately (especially on Windows),
					// so the graph view stays stale until Obsidian is restarted.
					// This handler "touches" each changed file through Obsidian's
					// vault API, triggering the metadata cache to re-process and
					// the graph view to update in real-time.
					const files = msg.files || [];
					if (files.length === 0) return;
					const app = this.app;
					(async () => {
						for (const filePath of files) {
							try {
								const file = app.vault.getAbstractFileByPath(filePath);
								if (file && file.path) {
									// File exists in Obsidian's vault — re-read from
									// disk and re-write through the vault API to trigger
									// the modify event → metadata cache re-processes
									// → graph view updates.
									const content = await app.vault.adapter.read(filePath);
									await app.vault.modify(file, content);
								} else {
									// New file not yet in Obsidian's vault. Try to
									// force a reconciliation if the adapter supports it.
									if (app.vault.adapter.reconcile) {
										await app.vault.adapter.reconcile();
									}
								}
							} catch (e) {
								console.log('vault_changed: could not refresh', filePath, e);
							}
						}
					})();
				}
			};
			ws.onclose = () => {
				setStatus('offline', 'Disconnected from backend — retrying...');
				// Null out the socket so ensureConnection() will reconnect next tick.
				ws = null;
				// Hide the Stop button — nothing to stop when disconnected.
				setTurnActive(false);
			};
			ws.onerror = (error) => {
				console.error('WebSocket error:', error);
			};
		};

		const startBackendAndConnect = async () => {
			await this.plugin.startBackendIfNeeded();
			let attempts = 0;
			const poll = window.setInterval(async () => {
				attempts++;
				const running = await this.plugin.isBackendRunning();
				if (running) {
					window.clearInterval(poll);
					setStatus('online', 'Backend online — connecting...');
					connectWebSocket();
				} else if (attempts > 30) {
					window.clearInterval(poll);
					setStatus('error', 'Backend did not start in time');
				}
			}, 1000);
		};

		ensureConnection();

		const send = (type) => {
			const message = input.value.trim();
			if (!message) {
				new Notice('Type a message first.');
				return;
			}
			if (!ws || ws.readyState !== WebSocket.OPEN) {
				new Notice('VaultBot backend is not connected yet.');
				return;
			}
			appendUserMessage(message);
		// Interrupt-on-send: stop any in-flight TTS so the bot isn't talking over the user.
		if (onCall) stopAllTTS();
		ws.send(JSON.stringify({type, message}));
			input.value = '';
			input.focus();
			// A new turn is now in flight: show the inline Stop button.
			setTurnActive(true);
			// Reset the think-tag parser state for the new turn.
			this._inThinkTag = false;
			_thinkingActive = false;
			// Flush the in-flight text segment so it renders before the new turn.
			closeCurrentSegment();
			currentAssistantMessage = null;
			currentThinkingBlock = null;
			currentAnswerBlock = null;
			currentSegmentText = '';
			currentAnswerText = '';
		};

		// Restart button: stops the backend (self-shutdown + taskkill
		// fallback) and starts it fresh. The one-click way for a non-tech
		// user to pick up code changes or recover from a stuck backend
		// without touching a terminal. Disables the button + shows live
		// status in the chat while the restart runs (a few seconds).
		// Restart button: stops the backend (self-shutdown + taskkill
		// fallback) and starts it fresh. While the backend is down or
		// restarting, the button stays in its dark "busy" state and only
		// returns to normal once the backend is confirmed back online — so
		// the user can see at a glance when it's safe to use again.
		const setRestartBusy = (busy) => {
			if (busy) {
				restartButton.setAttribute('disabled', 'disabled');
				restartButton.setText('Restarting...');
				restartButton.addClass('vaultbot-restart-busy');
			} else {
				restartButton.removeAttribute('disabled');
				restartButton.setText('Restart');
				restartButton.removeClass('vaultbot-restart-busy');
			}
		};
		restartButton.addEventListener('click', async () => {
			if (this.plugin.backendStarting) {
				new Notice('Backend is already starting; please wait.');
				return;
			}
			setRestartBusy(true);
			appendUserMessage('(restarting backend)');
			let statusDiv = appendAssistantMessage('Restarting backend...');
			try {
				const ok = await this.plugin.restartBackend((msg) => {
					if (statusDiv) statusDiv.setText(msg);
				});
				if (statusDiv) statusDiv.setText(ok ? 'Backend restarted and ready.' : 'Backend may not have come back up. Running a health check...');
				// Reconnect the websocket so the next message flows.
				connectWebSocket();
			} catch (e) {
				if (statusDiv) statusDiv.setText(`Restart failed: ${e.message || e}`);
			} finally {
				// Only un-busy once the backend is actually back up; if it's
				// still down, leave the button dark so the user sees the
				// backend isn't ready yet. The connection-check loop will
				// keep polling, and a later 'online' transition clears it.
				const up = await this.plugin.isBackendRunning();
				if (up) setRestartBusy(false);
				else {
					restartButton.setText('Offline');
					restartButton.addClass('vaultbot-restart-busy');
					// Auto-run Diagnose so the user sees WHY it didn't come
					// back, with remedy hints — instead of a dark button and
					// no next step. This closes the "Restart failed, now
					// what?" gap for a non-tech user.
					if (typeof runDiagnose === 'function') {
						try { await runDiagnose(); } catch (e) {}
					}
				}
			}
		});
		// Diagnose button: runs the proactive /diagnose battery and renders
		// each returned problem as a card. This is the user's "what's
		// wrong and how do I fix it?" affordance — no terminal, no log
		// file, just plain-English remedy cards. Reuses renderProblem so
		// reactive (WS problem events) and proactive (this button) share
		// one render path.
		const runDiagnose = async () => {
			appendUserMessage('(running health check)');
			let statusDiv = appendAssistantMessage('Checking VaultBot health...');
			diagnoseButton.setAttribute('disabled', 'disabled');
			diagnoseButton.setText('Checking...');
			try {
				const online = await this.plugin.isBackendRunning();
				if (!online) {
					// Backend is down — we can still run /preflight (which
					// doesn't need the backend) to check Python/Ollama/sync/
					// port. Surface those as problem cards.
					if (statusDiv) statusDiv.setText('Backend is down. Checking your setup...');
					try {
						const resp = await fetch(this.backendUrl + '/preflight');
						if (resp.ok) {
							const data = await resp.json();
							const problems = data.problems || [];
							if (statusDiv) statusDiv.setText(problems.length
								? `Found ${problems.length} issue(s):`
								: 'No setup issues found. Try Restart to bring the backend up.');
							problems.forEach(p => renderProblem(p));
						} else {
							if (statusDiv) statusDiv.setText('Could not run the health check. Try Restart, then Diagnose again.');
						}
					} catch (e) {
						if (statusDiv) statusDiv.setText(`Health check failed: ${e.message || e}`);
					}
					return;
				}
				// Backend is up — run the full /diagnose battery.
				const resp = await fetch(this.backendUrl + '/diagnose');
				const data = await resp.json();
				const problems = data.problems || [];
				if (statusDiv) statusDiv.setText(problems.length
					? `Found ${problems.length} issue(s):`
					: 'Everything looks healthy. No problems found.');
				problems.forEach(p => renderProblem(p));
			} catch (e) {
				if (statusDiv) statusDiv.setText(`Health check failed: ${e.message || e}`);
			} finally {
				diagnoseButton.removeAttribute('disabled');
				diagnoseButton.setText('Diagnose');
			}
		};
		diagnoseButton.addEventListener('click', () => runDiagnose());

		// --- Slash-command bar -------------------------------------------
		// Typing "/" at the start of the input (or after a newline) shows a
		// dropdown of available commands. Selecting one fills the input
		// with that command. On Enter, the command is sent via WS and the
		// backend handles it (/new, /help, /clear, /stop, /diagnose). This
		// makes commands discoverable — no need to read the source or
		// README to learn /new exists.
		const SLASH_COMMANDS = [
			{cmd: '/new',      desc: 'Start a fresh conversation'},
			{cmd: '/clear',    desc: 'Clear the chat window (keeps history)'},
			{cmd: '/stop',     desc: 'Stop what I\'m doing'},
			{cmd: '/ingest',   desc: 'Ingest new textbooks from learningMaterial/'},
			{cmd: '/diagnose', desc: 'Run a health check'},
			{cmd: '/help',     desc: 'Show available commands'},
		];
		let cmdDropdown = null;
		const showSlashCommands = () => {
			// Only show when the input starts with "/" and there's no
			// space after it yet (i.e. the user is still typing the command).
			const text = input.value;
			if (!text.startsWith('/') || text.includes(' ')) {
				hideSlashCommands();
				return;
			}
			const typed = text.toLowerCase();
			const matches = SLASH_COMMANDS.filter(c => c.cmd.startsWith(typed));
			if (!matches.length) {
				hideSlashCommands();
				return;
			}
			if (!cmdDropdown) {
				cmdDropdown = chatBar.createDiv({cls: 'vaultbot-cmd-dropdown'});
				cmdDropdown.style.display = 'none';
			}
			cmdDropdown.empty();
			matches.forEach(c => {
				const item = cmdDropdown.createDiv({cls: 'vaultbot-cmd-item'});
				item.createEl('span', {cls: 'vaultbot-cmd-name', text: c.cmd});
				item.createEl('span', {cls: 'vaultbot-cmd-desc', text: c.desc});
				item.addEventListener('click', () => {
					input.value = c.cmd + ' ';
					input.focus();
					// Place cursor at the end.
					const len = input.value.length;
					input.setSelectionRange(len, len);
					hideSlashCommands();
				});
			});
			cmdDropdown.style.display = '';
		};
		const hideSlashCommands = () => {
			if (cmdDropdown) cmdDropdown.style.display = 'none';
		};

		input.addEventListener('input', () => showSlashCommands());
		input.addEventListener('keydown', (e) => {
			// If the slash dropdown is showing, Arrow Up/Down + Enter
			// navigate it instead of sending the message.
			if (cmdDropdown && cmdDropdown.style.display !== 'none') {
				if (e.key === 'Enter' && !e.shiftKey) {
					// Let the default send handle it — the user typed a
					// command and pressed Enter. The backend handles it.
					hideSlashCommands();
					// Don't preventDefault — fall through to the send
					// handler below so the command is sent.
				} else if (e.key === 'Escape') {
					e.preventDefault();
					hideSlashCommands();
					return;
				}
			}
			if (e.key === 'Enter' && !e.shiftKey) {
				e.preventDefault();
				hideSlashCommands();
				send('chat');
			}
		});

		input.focus();
	}
}

module.exports = VaultBotPlugin;
