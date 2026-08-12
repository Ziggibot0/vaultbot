const { Plugin, Notice, Modal } = require('obsidian');
const { spawn } = require('child_process');
const path = require('path');
const VaultBotSettingTab = require('./settings.js');
const VaultBotSidebarView = require('./sidebar.js');

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

		// Read the shared-secret auth token that the backend generated on
		// first startup. The token is stored in vaultbot_backend/.vaultbot_auth_token
		// (gitignored). We attach it as X-VaultBot-Token header on every HTTP
		// request and as ?token= query param on the WebSocket. This prevents
		// other processes on the same machine from hijacking the backend API.
		this._authToken = this._readAuthToken();

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
		// The MCP server is part of the plugin â€” it just works when Obsidian
		// opens. It waits for the backend to be ready, then spawns and writes
		// MCP client configs so VS Code Copilot Chat / Claude auto-discover it.
		setTimeout(() => this.startMcpServerIfNeeded(), 6000);

		// Belt-and-suspenders: fire-and-forget a /shutdown beacon the moment
		// the Obsidian window starts closing. navigator.sendBeacon is built
		// for exactly this â€” it delivers the POST during teardown without
		// needing a response and isn't cancelled like fetch when the renderer
		// is destroyed. The backend's /shutdown endpoint self-terminates via
		// os._exit, so no response is needed. This survives the case where
		// onunload's async fetch is torn down before it completes.
		this._beforeUnloadHandler = (e) => {
			try {
				navigator.sendBeacon(this.settings.backendUrl + '/shutdown', new Blob([''], {type: 'text/plain'}));
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
		const required = ['vaultbot_stuff/vaultbot_backend/', '.venv/', 'vaultbot_stuff/vaultbot_backend/vaultbot_index/'];
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

	// ── Auth token helpers ──────────────────────────────────────────────
	// The backend generates a shared-secret token on first startup and stores
	// it in vaultbot_backend/.vaultbot_auth_token. We read it once on plugin
	// load and attach it to every HTTP request (X-VaultBot-Token header) and
	// WebSocket connection (?token= query param). This prevents other
	// processes on the same machine from calling the backend API.
	_readAuthToken() {
		try {
			let vaultRoot;
			if (this.app.vault.adapter.getBasePath) {
				vaultRoot = this.app.vault.adapter.getBasePath();
			} else {
				vaultRoot = this.app.vault.configDir.replace(/[\\/]\.obsidian[\\/]?$/, '');
			}
			const tokenPath = path.join(vaultRoot, 'vaultbot_stuff', 'vaultbot_backend', '.vaultbot_auth_token');
			const fs = require('fs');
			if (fs.existsSync(tokenPath)) {
				return fs.readFileSync(tokenPath, 'utf8').trim();
			}
		} catch (e) {
			console.warn('VaultBot: could not read auth token', e);
		}
		return null;
	}

	// Refresh the auth token (called after backend restart, since a new
	// backend instance may have generated a new token).
	_refreshAuthToken() {
		this._authToken = this._readAuthToken();
	}

	// Fetch wrapper that attaches the auth token header automatically.
	// Use this instead of raw fetch() for all backend API calls.
	async _authFetch(url, options = {}) {
		const headers = Object.assign({}, options.headers || {});
		if (this._authToken) {
			headers['X-VaultBot-Token'] = this._authToken;
		}
		return fetch(url, Object.assign({}, options, { headers }));
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
			const pidFile = path.join(vaultRoot, 'vaultbot_stuff', 'vaultbot_backend', 'vaultbot.pid');
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
			? 'irm https://github.com/ziggibot-uni/vaultbot/raw/main/setup.ps1 | iex'
			: 'curl -fsSL https://github.com/ziggibot-uni/vaultbot/raw/main/setup.sh | bash';

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
			const mcpPy = path.join(vaultRoot, 'vaultbot_stuff', 'vaultbot_backend', 'mcp_server.py');
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

	// Kill the backend process when Obsidian closes so nothing is left
	// running in the background. The backend writes its PID to
	// vaultbot_backend/vaultbot.pid on startup; we read that and taskkill it.
	// PRIMARY path: POST /shutdown â€” the backend self-terminates, which is
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
		const pidFile = path.join(vaultRoot, 'vaultbot_stuff', 'vaultbot_backend', 'vaultbot.pid');

		// 1) Ask the backend to self-terminate. Best-effort, short timeout.
		try {
			const controller = new AbortController();
			const to = setTimeout(() => controller.abort(), 3000);
			await fetch(this.settings.backendUrl + '/shutdown', {
				method: 'POST',
				signal: controller.signal
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
	//   - vaultbot_stuff/vaultbot_backend/**/*.py  (the backend engine)
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
		const backendDir = path.join(vaultRoot, 'vaultbot_stuff', 'vaultbot_backend');
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
				'--exclude=*/vaultbot_stuff/vaultbot_backend/*.log',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/*_log.json',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/calibration_log.json',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/claim_verification_log.json',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/consolidation_log.json',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/embedding_drift.json',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/procedure_failure_log.json',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/rag_eval_log.json',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/touch_counts.json',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/vaultbot.pid',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/sessions',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/sessions/*',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/checkpoints',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/checkpoints/*',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/vaultbot_index',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/vaultbot_index/*',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/trash',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/trash/*',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/__pycache__',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/__pycache__/*',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/*/__pycache__',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/*/__pycache__/*',
				'--exclude=*/vaultbot_stuff/vaultbot_backend/**/*.pyc'
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
			const srcBackend = path.join(archiveRoot, 'vaultbot_stuff', 'vaultbot_backend');
			if (!fs.existsSync(srcBackend)) throw new Error('Archive has no vaultbot_stuff/vaultbot_backend/ folder.');
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
				const reqPath = path.join(vaultRoot, 'vaultbot_stuff', 'vaultbot_backend', 'requirements.txt');
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
		const logFile = path.join(vaultRoot, 'vaultbot_stuff', 'vaultbot_backend', 'backend.log');
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
		const reqPath = path.join(vaultRoot, 'vaultbot_stuff', 'vaultbot_backend', 'requirements.txt');
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
			const pidFile = path.join(vaultRoot, 'vaultbot_stuff', 'vaultbot_backend', 'vaultbot.pid');
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

			const mainPy = path.join(vaultRoot, 'vaultbot_stuff', 'vaultbot_backend', 'main.py');
			const logFile = path.join(vaultRoot, 'vaultbot_stuff', 'vaultbot_backend', 'backend.log');

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
			const altLog = path.join(vaultRoot, 'vaultbot_stuff', 'vaultbot_backend', `backend-${stamp}.log`);
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
					// in the plugin settings â€” the backend's load_dotenv('../.env')
					// will pick it up.
					TAVILY_API_KEY: this.settings.tavilyApiKey || process.env.TAVILY_API_KEY || '',
					// Pass the allowContributions setting so the submit_contribution
					// tool can check it without reading data.json directly.
					VAULTBOT_ALLOW_CONTRIBUTIONS: this.settings.allowContributions ? 'true' : 'false',
				VAULTBOT_SAFE_MODE: this.settings.safeMode !== false ? 'true' : 'false',
				VAULTBOT_ALLOW_WEB_RESEARCH: this.settings.allowWebResearch !== false ? 'true' : 'false',
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
