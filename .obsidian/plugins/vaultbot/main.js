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
			selectedModel: '',
			researchBackend: 'freesearch',
			tavilyApiKey: ''
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
		if (!this.settings.selectedModel) {
			this.settings.selectedModel = '';
		}
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
		const required = ['vaultbot_stuff/vaultbot_backend/', 'vaultbot_venv/', 'vaultbot_stuff/vaultbot_backend/vaultbot_index/'];
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
			while (Date.now() - start < timeoutMs) {
				if (await this.isBackendRunning()) return true;
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

	async setBackendModel(model) {
		try {
			const response = await fetch(this.settings.backendUrl + '/set_model', {
				method: 'POST',
				headers: {'Content-Type': 'application/json'},
				body: JSON.stringify({model})
			});
			return response.ok;
		} catch (e) {
			return false;
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
	async fetchLLMConfig() {
		try {
			const response = await fetch(this.settings.backendUrl + '/llm/config');
			if (!response.ok) return null;
			return await response.json();
		} catch (e) {
			return null;
		}
	}

	// Switch the synthesis LLM backend at runtime. backend = 'ollama' | 'openai'.
	// For 'openai' the user supplies base_url + api_key + model. The backend
	// persists these to .env and rebuilds the client immediately (no restart).
	async pushLLMConfig({backend, baseUrl, apiKey, model}) {
		try {
			const body = {};
			if (backend) body.backend = backend;
			if (baseUrl) body.base_url = baseUrl;
			if (apiKey) body.api_key = apiKey;
			if (model) body.model = model;
			const response = await fetch(this.settings.backendUrl + '/llm/config', {
				method: 'POST',
				headers: {'Content-Type': 'application/json'},
				body: JSON.stringify(body)
			});
			return response.ok ? await response.json() : null;
		} catch (e) {
			return null;
		}
	}

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

	// Read the dedicated vision-model config (the model used to read textbook
	// pages, separate from the chat model). Lets the settings panel show
	// whether a vision model is configured and reachable, so a user with a
	// text-only chat model can confirm their page-reading model is wired up.
	async fetchVisionConfig() {
		try {
			const response = await fetch(this.settings.backendUrl + '/llm/vision_config');
			if (!response.ok) return null;
			return await response.json();
		} catch (e) {
			return null;
		}
	}

	// Configure (or clear) the dedicated vision model at runtime. The vision
	// model is a SEPARATE concern from the chat model: a user keeps their
	// fast/cheap text-only chat model and delegates page-reading to a vision-
	// capable model on its own backend. Sending an empty model clears the
	// config so the page reader falls back to the chat model's own vision.
	async pushVisionConfig({backend, baseUrl, apiKey, model, ollamaHost}) {
		try {
			const body = {};
			if (backend) body.backend = backend;
			if (baseUrl !== undefined) body.base_url = baseUrl;
			if (apiKey !== undefined) body.api_key = apiKey;
			if (model !== undefined) body.model = model;
			if (ollamaHost !== undefined) body.ollama_host = ollamaHost;
			const response = await fetch(this.settings.backendUrl + '/llm/vision_config', {
				method: 'POST',
				headers: {'Content-Type': 'application/json'},
				body: JSON.stringify(body)
			});
			return response.ok ? await response.json() : null;
		} catch (e) {
			return null;
		}
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
	// Windows: vaultbot_venv/Scripts/{pythonw.exe,python.exe}
	// macOS/Linux: vaultbot_venv/bin/python
	// ─────────────────────────────────────────────────────────────────────
	_venvBinDir() {
		return process.platform === 'win32' ? 'Scripts' : 'bin';
	}

	_venvPythonExe(vaultRoot) {
		const bin = this._venvBinDir();
		const candidates = process.platform === 'win32'
			? [path.join(vaultRoot, 'vaultbot_venv', bin, 'pythonw.exe'),
			   path.join(vaultRoot, 'vaultbot_venv', bin, 'python.exe')]
			: [path.join(vaultRoot, 'vaultbot_venv', bin, 'python')];
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
			const running = await this.isBackendRunning();
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

		// 2) Wait briefly for the process to actually exit, then verify with
		//    taskkill against the PID file as a hard fallback.
		const waitMs = 1500;
		const start = Date.now();
		while (Date.now() - start < waitMs) {
			if (!await this.isBackendRunning()) break;
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

	async startBackendIfNeeded() {
		if (this.backendStarting) {
			new Notice('VaultBot backend is already starting...');
			return;
		}

		this.backendStarting = true;

		try {
			let running = await this.isBackendRunning();
			if (running) {
				new Notice('VaultBot backend is already running.');
				return;
			}

			new Notice('Starting VaultBot backend...');

			let vaultRoot;
			if (this.app.vault.adapter.getBasePath) {
				vaultRoot = this.app.vault.adapter.getBasePath();
			} else {
				vaultRoot = this.app.vault.configDir.replace(/[\\/]\.obsidian[\\/]?$/, '');
			}

			const mainPy = path.join(vaultRoot, 'vaultbot_stuff', 'vaultbot_backend', 'main.py');
			const logFile = path.join(vaultRoot, 'vaultbot_stuff', 'vaultbot_backend', 'backend.log');

			const fs = require('fs');
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
					TAVILY_API_KEY: this.settings.tavilyApiKey || process.env.TAVILY_API_KEY || ''
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
					throw new Error('Backend process started but did not respond in time. Check vaultbot_stuff/vaultbot_backend/backend.log.');
				}
			}
			new Notice('VaultBot backend is ready.');
		} catch (err) {
			new Notice('Failed to start VaultBot backend: ' + err.message);
			console.error('VaultBot backend spawn error:', err);
		} finally {
			this.backendStarting = false;
		}
	}
}

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

		const modelSetting = new Setting(containerEl)
			.setName('Ollama model')
			.setDesc('Which locally installed Ollama model VaultBot should use');
		const modelDropdown = modelSetting.controlEl.createEl('select');
		modelDropdown.style.minWidth = '180px';
		modelDropdown.createEl('option', {text: 'Waiting for backend...', attr: {disabled: true}});

		const populateModels = async () => {
			const online = await this.plugin.waitForBackend();
			if (!online) {
				modelDropdown.empty();
				modelDropdown.createEl('option', {text: 'Backend offline - start backend first', attr: {disabled: true}});
				return;
			}
			modelDropdown.empty();
			modelDropdown.createEl('option', {text: 'Fetching models...', attr: {disabled: true}});
			try {
				const {models, current} = await this.plugin.fetchModels();
				modelDropdown.empty();
				if (!models.length) {
					modelDropdown.createEl('option', {text: 'No models found', attr: {disabled: true}});
					return;
				}
				const selected = this.plugin.settings.selectedModel || current || models[0].name || models[0];
				models.forEach(m => {
					const name = typeof m === 'string' ? m : m.name;
					const label = (typeof m === 'object' && m.vision) ? '👁 ' + name : name;
					const opt = modelDropdown.createEl('option', {text: label, attr: {value: name}});
					if (name === selected) opt.selected = true;
				});
				this.plugin.settings.selectedModel = selected;
				await this.plugin.setBackendModel(selected);
				await this.plugin.saveSettings();
			} catch (e) {
				modelDropdown.empty();
				modelDropdown.createEl('option', {text: 'Could not reach backend', attr: {disabled: true}});
			}
		};

		populateModels();
		modelDropdown.addEventListener('change', async () => {
			this.plugin.settings.selectedModel = modelDropdown.value;
			await this.plugin.saveSettings();
			await this.plugin.setBackendModel(modelDropdown.value);
		});

		containerEl.createEl('h3', {text: 'LLM Backend'});

		// The synthesis LLM is the only step that spends tokens, so it's the
		// one swappable surface. 'Ollama' runs a local model (free, private,
		// needs a beefy machine). 'API key' hits any OpenAI-compatible
		// endpoint (OpenAI, OpenRouter->Anthropic, Gemini, etc.) so a weak
		// laptop runs zero local compute. The research loop stays token-free
		// either way.
		const llmBackendSetting = new Setting(containerEl)
			.setName('Synthesis LLM backend')
			.setDesc('Where the final-answer LLM runs. The research + retrieval loop is always local + token-free.');
		const llmBackendDropdown = llmBackendSetting.controlEl.createEl('select');
		llmBackendDropdown.createEl('option', {text: 'Ollama (local, free)', attr: {value: 'ollama'}});
		llmBackendDropdown.createEl('option', {text: 'API key (OpenAI-compatible)', attr: {value: 'openai'}});

		// API-key backend fields (shown only when 'openai' is selected).
		const apiFieldsEl = containerEl.createDiv();
		apiFieldsEl.style.display = 'none';
		apiFieldsEl.style.paddingLeft = '0';
		apiFieldsEl.createEl('div', {text: 'Paste your API key + endpoint. Works with OpenAI, OpenRouter, Gemini proxies, vLLM, LM Studio, etc.', attr: {style: 'opacity:0.7;font-size:0.85em;margin:4px 0 8px 0;'}});

		const baseUrlRow = apiFieldsEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:8px;margin-bottom:6px;'}});
		baseUrlRow.createEl('span', {text: 'Base URL', attr: {style: 'min-width:80px;font-size:0.85em;'}});
		const baseUrlInput = baseUrlRow.createEl('input', {type: 'text', attr: {placeholder: 'https://api.openai.com', style: 'flex:1;min-width:220px;'}});
		baseUrlInput.value = 'https://api.openai.com';

		const apiKeyRow = apiFieldsEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:8px;margin-bottom:6px;'}});
		apiKeyRow.createEl('span', {text: 'API key', attr: {style: 'min-width:80px;font-size:0.85em;'}});
		const apiKeyInput = apiKeyRow.createEl('input', {type: 'password', attr: {placeholder: 'sk-...', style: 'flex:1;min-width:220px;'}});

		const llmModelRow = apiFieldsEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:8px;margin-bottom:8px;'}});
		llmModelRow.createEl('span', {text: 'Model', attr: {style: 'min-width:80px;font-size:0.85em;'}});
		const llmModelInput = llmModelRow.createEl('input', {type: 'text', attr: {placeholder: 'gpt-4o-mini', style: 'flex:1;min-width:220px;'}});

		const llmStatusEl = apiFieldsEl.createEl('div', {attr: {style: 'opacity:0.7;font-size:0.8em;min-height:1em;'}});
		const llmSaveBtn = apiFieldsEl.createEl('button', {text: 'Save & switch backend', cls: 'mod-cta'});
		llmSaveBtn.style.marginTop = '4px';
		llmSaveBtn.addEventListener('click', async () => {
			llmStatusEl.setText('Switching backend...');
			const res = await this.plugin.pushLLMConfig({
				backend: 'openai',
				baseUrl: baseUrlInput.value.trim(),
				apiKey: apiKeyInput.value.trim(),
				model: llmModelInput.value.trim()
			});
			if (res && res.status === 'ok') {
				llmStatusEl.setText(`Connected to ${res.backend} (${res.model}). Running: ${res.running}`);
				new Notice('LLM backend switched. Reloading model list...');
				populateModels();
			} else {
				llmStatusEl.setText('Failed — check the key, base URL, and model id.');
			}
		});

		// Load the current backend config and reflect it in the UI.
		const refreshLLMConfig = async () => {
			const cfg = await this.plugin.fetchLLMConfig();
			if (!cfg) {
				llmStatusEl.setText('Backend offline — start the backend first.');
				return;
			}
			llmBackendDropdown.value = cfg.backend || 'ollama';
			apiFieldsEl.style.display = (cfg.backend === 'openai') ? 'block' : 'none';
			if (cfg.base_url) baseUrlInput.value = cfg.base_url;
			if (cfg.model) llmModelInput.value = cfg.model;
			llmStatusEl.setText(`Active: ${cfg.backend} | model: ${cfg.model || '(none)'} | running: ${cfg.running}`);
		};
		refreshLLMConfig();
		llmBackendDropdown.addEventListener('change', async () => {
			apiFieldsEl.style.display = (llmBackendDropdown.value === 'openai') ? 'block' : 'none';
			if (llmBackendDropdown.value === 'ollama') {
				// Switch back to Ollama immediately (no extra fields needed).
				const res = await this.plugin.pushLLMConfig({backend: 'ollama'});
				if (res && res.status === 'ok') {
					llmStatusEl.setText(`Switched to Ollama. Running: ${res.running}`);
					new Notice('Switched to local Ollama. Reloading models...');
					populateModels();
				}
			}
		});

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

		containerEl.createEl('h3', {text: 'Vision Model (textbook page reader)'});

		// The vision model is a SEPARATE, OPTIONAL model used ONLY to read
		// rendered textbook pages (textbook_read_page). It lets a user keep a
		// fast/cheap text-only chat model and still get equations/figures read
		// exactly as printed — the page-reading step uses this model instead of
		// the chat model. Leave the model field empty to fall back to the chat
		// model's own vision (a vision-capable chat model needs no separate
		// config). Human-centered: a non-coder picks a vision model once and
		// never thinks about it again; the ingest alert names THIS model.
		const visionDescEl = containerEl.createEl('div', {text:
			'Optional. A vision-capable model used only to read textbook PDF pages ' +
			'(so equations and figures come through exactly as printed). Leave empty ' +
			'to fall back to your chat model\u2019s own vision. Works with a different ' +
			'Ollama host or a separate OpenAI-compatible endpoint than your chat model.',
			attr: {style: 'opacity:0.7;font-size:0.85em;margin:4px 0 10px 0;'}});

		const visionBackendSetting = new Setting(containerEl)
			.setName('Vision model backend')
			.setDesc('Where the page-reading model runs. Defaults to your chat backend.');
		const visionBackendDropdown = visionBackendSetting.controlEl.createEl('select');
		visionBackendDropdown.createEl('option', {text: 'Same as chat backend', attr: {value: ''}});
		visionBackendDropdown.createEl('option', {text: 'Ollama (local, free)', attr: {value: 'ollama'}});
		visionBackendDropdown.createEl('option', {text: 'API key (OpenAI-compatible)', attr: {value: 'openai'}});

		// Vision API-key backend fields (shown only when 'openai' is selected).
		const visionApiFieldsEl = containerEl.createDiv();
		visionApiFieldsEl.style.display = 'none';
		visionApiFieldsEl.style.paddingLeft = '0';
		visionApiFieldsEl.createEl('div', {text: 'Use a separate endpoint/key than your chat model, or reuse the chat model\u2019s values by leaving blank.',
			attr: {style: 'opacity:0.7;font-size:0.85em;margin:4px 0 8px 0;'}});

		const visionBaseUrlRow = visionApiFieldsEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:8px;margin-bottom:6px;'}});
		visionBaseUrlRow.createEl('span', {text: 'Base URL', attr: {style: 'min-width:80px;font-size:0.85em;'}});
		const visionBaseUrlInput = visionBaseUrlRow.createEl('input', {type: 'text', attr: {placeholder: 'https://api.openai.com', style: 'flex:1;min-width:220px;'}});
		visionBaseUrlInput.value = 'https://api.openai.com';

		const visionApiKeyRow = visionApiFieldsEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:8px;margin-bottom:6px;'}});
		visionApiKeyRow.createEl('span', {text: 'API key', attr: {style: 'min-width:80px;font-size:0.85em;'}});
		const visionApiKeyInput = visionApiKeyRow.createEl('input', {type: 'password', attr: {placeholder: 'sk-... (blank = reuse chat key)', style: 'flex:1;min-width:220px;'}});

		const visionModelRow = visionApiFieldsEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:8px;margin-bottom:8px;'}});
		visionModelRow.createEl('span', {text: 'Model', attr: {style: 'min-width:80px;font-size:0.85em;'}});
		const visionModelInput = visionModelRow.createEl('input', {type: 'text', attr: {placeholder: 'gpt-4o-mini', style: 'flex:1;min-width:220px;'}});

		// Ollama-host field (shown only when 'ollama' is selected).
		const visionOllamaFieldsEl = containerEl.createDiv();
		visionOllamaFieldsEl.style.display = 'none';
		visionOllamaFieldsEl.style.paddingLeft = '0';
		visionOllamaFieldsEl.createEl('div', {text: 'Only set this if your vision model lives on a different Ollama daemon than your chat model. Leave blank to use the same host.',
			attr: {style: 'opacity:0.7;font-size:0.85em;margin:4px 0 8px 0;'}});
		const visionOllamaHostRow = visionOllamaFieldsEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:8px;margin-bottom:8px;'}});
		visionOllamaHostRow.createEl('span', {text: 'Ollama host', attr: {style: 'min-width:80px;font-size:0.85em;'}});
		const visionOllamaHostInput = visionOllamaHostRow.createEl('input', {type: 'text', attr: {placeholder: 'http://localhost:11434', style: 'flex:1;min-width:220px;'}});

		// Model field shown for the 'same as chat' / 'ollama' paths (a bare
		// model name). For 'openai' the model input lives in the API fields.
		const visionOllamaModelRow = visionOllamaFieldsEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:8px;margin-bottom:8px;'}});
		visionOllamaModelRow.createEl('span', {text: 'Model', attr: {style: 'min-width:80px;font-size:0.85em;'}});
		const visionOllamaModelInput = visionOllamaModelRow.createEl('input', {type: 'text', attr: {placeholder: 'llava / minicpm-v / qwen-vl', style: 'flex:1;min-width:220px;'}});

		const visionStatusEl = containerEl.createEl('div', {attr: {style: 'opacity:0.7;font-size:0.8em;min-height:1em;margin-bottom:6px;'}});

		const visionSaveBtn = containerEl.createEl('button', {text: 'Save vision model', cls: 'mod-cta'});
		visionSaveBtn.style.marginBottom = '6px';
		visionSaveBtn.addEventListener('click', async () => {
			visionStatusEl.setText('Saving vision model...');
			const be = visionBackendDropdown.value;
			// The model input depends on which backend is selected.
			let modelVal = '';
			if (be === 'openai') {
				modelVal = visionModelInput.value.trim();
			} else if (be === 'ollama') {
				modelVal = visionOllamaModelInput.value.trim();
			} else {
				// 'same as chat' — use the Ollama model field as the model name
				// (the backend mirrors the chat backend automatically).
				modelVal = visionOllamaModelInput.value.trim();
			}
			const res = await this.plugin.pushVisionConfig({
				backend: be || undefined,
				baseUrl: be === 'openai' ? visionBaseUrlInput.value.trim() : undefined,
				apiKey: be === 'openai' ? visionApiKeyInput.value.trim() : undefined,
				model: modelVal,
				ollamaHost: (be === 'ollama' || be === '') ? visionOllamaHostInput.value.trim() : undefined
			});
			if (res && res.status === 'ok') {
				const msg = res.configured
					? `Vision model set: ${res.model} (${res.backend}). Running: ${res.running}`
					: 'Vision model cleared \u2014 page reading falls back to your chat model.';
				visionStatusEl.setText(msg);
				new Notice(res.configured ? 'Vision model saved.' : 'Vision model cleared.');
				refreshVisionConfig();
			} else {
				visionStatusEl.setText('Failed \u2014 check the key, base URL, and model id.');
			}
		});

		const visionClearBtn = containerEl.createEl('button', {text: 'Clear vision model'});
		visionClearBtn.style.marginLeft = '8px';
		visionClearBtn.addEventListener('click', async () => {
			visionStatusEl.setText('Clearing vision model...');
			const res = await this.plugin.pushVisionConfig({model: ''});
			if (res && res.status === 'ok') {
				visionStatusEl.setText('Vision model cleared \u2014 page reading falls back to your chat model.');
				new Notice('Vision model cleared.');
				refreshVisionConfig();
			} else {
				visionStatusEl.setText('Failed to clear \u2014 is the backend running?');
			}
		});

		// Load the current vision config and reflect it in the UI.
		const refreshVisionConfig = async () => {
			const cfg = await this.plugin.fetchVisionConfig();
			if (!cfg) {
				visionStatusEl.setText('Backend offline \u2014 start the backend first.');
				return;
			}
			visionBackendDropdown.value = cfg.backend || '';
			visionApiFieldsEl.style.display = (cfg.backend === 'openai') ? 'block' : 'none';
			visionOllamaFieldsEl.style.display = (cfg.backend === 'openai') ? 'none' : 'block';
			if (cfg.base_url) visionBaseUrlInput.value = cfg.base_url;
			if (cfg.model) {
				if (cfg.backend === 'openai') visionModelInput.value = cfg.model;
				else visionOllamaModelInput.value = cfg.model;
			}
			const state = cfg.configured
				? `Active: ${cfg.backend} | model: ${cfg.model} | running: ${cfg.running}`
				: 'Not configured \u2014 page reading uses your chat model\u2019s vision (if any).';
			visionStatusEl.setText(state);
		};
		refreshVisionConfig();
		visionBackendDropdown.addEventListener('change', async () => {
			const be = visionBackendDropdown.value;
			visionApiFieldsEl.style.display = (be === 'openai') ? 'block' : 'none';
			visionOllamaFieldsEl.style.display = (be === 'openai') ? 'none' : 'block';
		});

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

		// ── Directives panel ────────────────────────────────────────────
		// High-leverage behavioral toggles that map to writing/removing
		// directive .md files at the vault root via the Obsidian vault API.
		// A non-tech user can flip "Autonomy on/off" or "Keep replies short"
		// without navigating folders or editing markdown — the panel writes
		// the file (and the directive takes effect on the next chat turn
		// via retrieval). Power users can still edit the .md directly.
		containerEl.createEl('h3', {text: 'Directives'});
		const dirDesc = containerEl.createEl('div');
		dirDesc.setText(
			'These toggles shape how VaultBot behaves. Each one writes a ' +
			'short directive note that VaultBot reads on its next turn. ' +
			'You can also edit the notes directly at the vault root — the ' +
			'toggles are just a quick way to turn them on or off.');
		dirDesc.style.opacity = '0.7';
		dirDesc.style.fontSize = '0.85em';
		dirDesc.style.marginBottom = '10px';

		// Each directive: path at vault root, short title, description,
		// and the content to write when enabled. The content is a concise
		// version of the baseline template — short enough to be readable
		// in the settings panel but complete enough to direct the model.
		const DIRECTIVES = [
			{path: 'Autonomy-Directive.md',
			 title: 'Autonomy',
			 desc: 'Let VaultBot act on its own — store, organize, research, ' +
				'and self-improve without asking permission each time. Report after.',
			 on: '# Autonomy Directive\n\nAct on your own. Store, organize, ' +
				'research, and self-improve without asking permission each ' +
				'time. Report what you did after the fact.\n'},
			{path: 'Vault-Knowledge-Only-Directive.md',
			 title: 'Vault knowledge only',
			 desc: 'The vault is the ONLY knowledge source. Never reference ' +
				'training data. If the vault has nothing, say "I don\'t know."',
			 on: '# Vault Knowledge Only Directive\n\nThe vault is the ONLY ' +
				'knowledge source. Never reference training data. If the ' +
				'vault has nothing on a topic, say "I don\'t know" and offer ' +
				'to research it.\n'},
			{path: 'IDK-Fallback-Directive.md',
			 title: 'Honest "I don\'t know"',
			 desc: 'When the vault is empty AND research is down, say ' +
				'"I don\'t know." No hedging, no training-data leakage.',
			 on: '# IDK Fallback Directive\n\nWhen the vault has nothing on ' +
				'a topic AND research is unavailable, say "I don\'t know." ' +
				'No hedging, no guessing from training data, no filler.\n'},
			{path: 'No-Wikipedia-Directive.md',
			 title: 'No Wikipedia',
			 desc: 'Never cite Wikipedia as a source. Use primary sources, ' +
				'academic papers, or specialist forums instead.',
			 on: '# No Wikipedia Directive\n\nNever cite Wikipedia as a ' +
				'source. Prefer primary sources, academic papers, and ' +
				'specialist forums. If the only available source is ' +
				'Wikipedia, say so and offer to find better sources.\n'},
			{path: 'Communication-Preferences.md',
			 title: 'Keep replies short',
			 desc: 'Bottom line up front. Bullet points over paragraphs. ' +
				'Report accomplishments, not regurgitation.',
			 on: '# Communication Preferences\n\n## Style\n- Keep it short. ' +
				'No walls of text.\n- Report accomplishments, not ' +
				'regurgitation. Tell me what you DID, not everything you ' +
				'learned.\n- Bottom line up front. Lead with the outcome.\n' +
				'## Format\n- Bullet points over paragraphs.\n- If research ' +
				'was done, say what was researched and where the note lives. ' +
				'Don\'t paste the full synthesis into chat.\n'},
		];

		const vault = this.app.vault;
		const dirToggles = [];
		for (const d of DIRECTIVES) {
			// Check if the file already exists to set the initial toggle state.
			let exists = false;
			try {
				const file = vault.getAbstractFileByPath(d.path);
				exists = !!(file && file.path);
			} catch (e) { exists = false; }

			const setting = new Setting(containerEl)
				.setName(d.title)
				.setDesc(d.desc);
			const toggle = setting.addToggle(t => t
				.setValue(exists)
				.onChange(async (value) => {
					toggle.setDisabled(true);
					try {
						if (value) {
							// Write the directive file via the vault API
							// so Obsidian detects it immediately (no file-
							// watcher lag + graph view updates in real time).
							await vault.adapter.write(d.path, d.on);
						} else {
							// Remove the directive file. Use trash if
							// available, otherwise delete.
							const file = vault.getAbstractFileByPath(d.path);
							if (file) {
								try {
									await vault.trash(file, true);
								} catch (e2) {
									await vault.delete(file);
								}
							}
						}
					} catch (e) {
						new Notice('Could not ' + (value ? 'enable' : 'disable') +
							' directive: ' + (e.message || e));
						// Revert the toggle on failure.
						t.setValue(!value);
					} finally {
						toggle.setDisabled(false);
					}
				}));
			dirToggles.push(toggle);
		}

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
		const titleEl = headerEl.createEl('div', {cls: 'vaultbot-header-title'});
		titleEl.createEl('span', {cls: 'vaultbot-header-mark', text: '🌿'});
		titleEl.createEl('span', {text: 'VaultBot'});
		headerEl.createEl('div', {cls: 'vaultbot-header-sub', text: 'a garden for your thoughts'});

		// History disclosure: a small "Recent" toggle in the header that
		// expands a list of past chat sessions (read from /sessions). This
		// makes closed/reopened Obsidian feel less like data loss: the user
		// can see their past conversations and pick up where they left off.
		// Selecting an entry loads its messages read-only into the chat.
		const historyToggle = headerEl.createEl('button', {
			cls: 'vaultbot-history-toggle', text: 'Recent'});
		historyToggle.title = 'Show recent conversations';
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
									}
								}
								chatContainer.scrollTop = 0;
								setStatus('done', 'Showing past conversation');
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

		const statusEl = this.contentEl.createDiv({cls: 'vaultbot-status'});

		// Session title: shows the current session's title above the chat.
		// Click to edit inline; Enter or blur saves via WebSocket set_title.
		const sessionTitleEl = this.contentEl.createDiv({cls: 'vaultbot-session-title'});
		sessionTitleEl.setText('New Session');
		sessionTitleEl.title = 'Click to rename this session';
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

		// Smart auto-scroll: only scroll to bottom when the user is already near
		// the bottom. This lets the user scroll up to read history while
		// VaultBot is streaming without being yanked back down on every chunk.
		const SCROLL_THRESHOLD = 80; // px from bottom to still count as "at bottom"
		const smartScrollToBottom = () => {
			const isNearBottom = (chatContainer.scrollHeight - chatContainer.scrollTop - chatContainer.clientHeight) < SCROLL_THRESHOLD;
			if (isNearBottom) {
				chatContainer.scrollTo({ top: chatContainer.scrollHeight, behavior: 'smooth' });
			}
		};

	// Delegated click handler: make [[wikilinks]] and external links clickable
	// in chat messages. Works for both user and assistant messages.
	chatContainer.addEventListener('click', (e) => {
		// Internal links ([[wikilink]]) -> open the file in Obsidian
		const intLink = e.target.closest('a.internal-link');
		if (intLink) {
			e.preventDefault();
			const href = intLink.getAttribute('data-href') || intLink.getAttribute('href');
			if (href) this.app.workspace.openLinkText(href, '', false);
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

		// Footer: the model picker + input/buttons. This is the fixed
		// bottom region of the view; only the chat panel above it scrolls.
		const footerEl = this.contentEl.createDiv({cls: 'vaultbot-footer'});

		// Model picker dropdown
		const modelBar = footerEl.createDiv({cls: 'vaultbot-model-bar'});
		modelBar.createEl('span', {text: 'Model:', cls: 'vaultbot-model-label'});
		const modelSelect = modelBar.createEl('select', {cls: 'vaultbot-model-select'});
		modelSelect.createEl('option', {text: 'Fetching models...', attr: {disabled: true}});
		const refreshModels = async () => {
			// Use the single-flight ready promise instead of an independent
			// 250ms poll loop — that loop was the worst of the three console
			// spammers during boot (two fetches every 250ms).
			const online = await this.plugin.onceBackendReady(5000, 500);
			if (!online) {
				modelSelect.empty();
				modelSelect.createEl('option', {text: 'Backend offline', attr: {disabled: true}});
				return;
			}
			const {models, current} = await this.plugin.fetchModels();
			modelSelect.empty();
			if (!models.length) {
				// No models installed: instead of a dead-end "No models found"
				// disabled option, show a call-to-action that lets the user
				// download a recommended model right here — no terminal, no
				// `ollama pull` command. The option value is a sentinel the
				// change handler checks.
				modelSelect.createEl('option', {
					text: 'No models yet — click to download ↓',
					attr: {value: '__pull__'}});
				return;
			}
			// Curate: group into Vision-capable / Text models / Embedding.
			// Vision-capable models get a 👁 marker. Embedding models
			// (instruct=false) are separated so the user doesn't accidentally
			// pick an embed model for chat.
			const selected = this.plugin.settings.selectedModel || current
				|| models.find(m => m.instruct)?.name || models[0].name;
			// Group 1: Vision-capable instruct models (if any exist)
			const visionModels = models.filter(m => m.vision && m.instruct);
			const textModels = models.filter(m => !m.vision && m.instruct);
			const otherModels = models.filter(m => !m.instruct);
			if (visionModels.length) {
				const og = modelSelect.createEl('optgroup', {label: 'Vision-capable 👁'});
				visionModels.forEach(m => {
					const opt = og.createEl('option', {
						text: (m.vision ? '👁 ' : '') + m.name,
						attr: {value: m.name}});
					if (m.name === selected) opt.selected = true;
				});
			}
			if (textModels.length) {
				const og = modelSelect.createEl('optgroup', {label: 'Text models'});
				textModels.forEach(m => {
					const opt = og.createEl('option', {
						text: m.name, attr: {value: m.name}});
					if (m.name === selected) opt.selected = true;
				});
			}
			if (otherModels.length) {
				const og = modelSelect.createEl('optgroup', {label: 'Embedding / other'});
				otherModels.forEach(m => {
					const opt = og.createEl('option', {
						text: m.name, attr: {value: m.name}});
					if (m.name === selected) opt.selected = true;
				});
			}
			this.plugin.settings.selectedModel = selected;
			await this.plugin.saveSettings();
			await this.plugin.setBackendModel(selected);
			// Refresh the meter ceiling for the newly equipped model.
			const ctxWin = await this.plugin.fetchContextWindow(selected);
			tokenMeterEl.setAttribute('title',
				`${ctxWin.toLocaleString()} token context window — ${selected}`);
			updateTokenMeter(0, ctxWin);
		};
		refreshModels();
		modelSelect.addEventListener('change', async () => {
			// Sentinel: the "No models yet" option was selected → show the
			// download dialog instead of trying to set a non-existent model.
			if (modelSelect.value === '__pull__') {
				await this.plugin._showDownloadModelModal(() => refreshModels());
				return;
			}
			this.plugin.settings.selectedModel = modelSelect.value;
			await this.plugin.saveSettings();
			await this.plugin.setBackendModel(modelSelect.value);
			// Re-size the meter for the new model's context window.
			const ctxWin = await this.plugin.fetchContextWindow(modelSelect.value);
			tokenMeterEl.setAttribute('title',
				`${ctxWin.toLocaleString()} token context window — ${modelSelect.value}`);
			updateTokenMeter(0, ctxWin);
		});
		const refreshBtn = modelBar.createEl('span', {text: '↻', cls: 'vaultbot-model-refresh'});
		refreshBtn.title = 'Refresh model list';
		refreshBtn.addEventListener('click', () => refreshModels());

		// --- Token-usage meter ------------------------------------------
		// A horizontal bar that fills proportional to how many tokens the
		// current conversation is using, capped at the equipped model's
		// context window. Updates live from context_usage events the backend
		// emits each turn (pre-loop + post-answer). Color shifts moss→clay→
		// bark as it fills so the user can see at a glance how close the
		// context is to overflowing.
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
		const ingestButton = clayGroup.createEl('button', {text: 'Ingest', cls: 'vaultbot-btn'});
		const restartButton = mossGroup.createEl('button', {text: 'Restart', cls: 'vaultbot-btn vaultbot-btn-quiet vaultbot-btn-restart'});
		const diagnoseButton = mossGroup.createEl('button', {text: 'Diagnose', cls: 'vaultbot-btn vaultbot-btn-quiet vaultbot-btn-diagnose'});
		ingestButton.title = 'Ingest any new textbooks from the learningMaterial/ folder into the vault. No AI involved - just parses and links them. Weaving happens in the background.';
		restartButton.title = 'Restart the VaultBot backend. Use this after code changes or if the bot seems stuck. Takes a few seconds.';
		diagnoseButton.title = 'Run a health check. VaultBot looks for common problems (Ollama not running, missing model, port conflict) and shows plain-English fixes.';

		let currentAssistantMessage = null;
		let currentThinkingBlock = null;
		// Turn state: tracks whether the bot is actively generating, so the
		// inline Stop button only shows when there's something to stop.
		let turnActive = false;
		const setTurnActive = (active) => {
			turnActive = active;
			stopButton.style.display = active ? '' : 'none';
		};
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
				for (const k of ['round', 'max_rounds', 'new_sources', 'total_sources', 'sources', 'follow_up_sources', 'url', 'title', 'note', 'total_notes', 'total', 'query', 'facts', 'source_count', 'outbound_links', 'amem_evolved', 'amem_links', 'silent_ms', 'chunks']) {
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
		};

		let ws = null;
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
					// Show the thinking block live while the model reasons.
					setThinkingVisible(true);
					currentThinkingBlock.setText((currentThinkingBlock.getText() || '') + msg.content);
					smartScrollToBottom();
				} else if (msg.type === 'answer_chunk') {
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
					smartScrollToBottom();
				} else if (msg.type === 'tool_call') {
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
					startActivity('Calling ' + toolName + (argsStr ? ': ' + argsStr : '...'), {});
				} else if (msg.type === 'progress') {
					// Granular stage events from the backend (research rounds,
					// scraping, synthesis, gap fill, note writing, A-MEM).
					startActivity(msg.stage, msg.detail || {});
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
					if (!currentAssistantMessage) startAssistantMessage();
					closeCurrentSegment();
					const summary = (msg.tool || 'tool') + ' - ' + (msg.summary || 'done');
					endActivity(summary);
					const resDiv = currentAssistantMessage.createDiv({cls: 'vaultbot-tool-result'});
					resDiv.setText('  - ' + summary);
					smartScrollToBottom();
				} else if (msg.type === 'context_usage') {
					// Live token-usage meter update from the backend. Fires
					// each turn (pre-loop + post-answer) carrying the model's
					// context window + estimated used tokens.
					if (typeof msg.context_window === 'number') {
						updateTokenMeter(msg.used_tokens || 0, msg.context_window);
					}
				} else if (msg.type === 'answer_done') {
					endActivity();
					// Flush the final text segment so its markdown renders.
					closeCurrentSegment();
					statusEl.setText('Done');
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
					currentAssistantMessage = null;
					currentThinkingBlock = null;
					currentAnswerBlock = null;
					currentSegmentText = '';
					currentAnswerText = '';
				} else if (msg.type === 'session_info') {
				// Backend sent session metadata (id + title). Update the title
				// display so the user knows which session they're in.
				if (msg.title) sessionTitleEl.setText(msg.title);
			} else if (msg.type === 'session_reset') {
					// /new command: clear the chat UI for a fresh session.
					endActivity();
					chatContainer.empty();
					currentAssistantMessage = null;
					currentThinkingBlock = null;
					currentAnswerBlock = null;
					currentSegmentText = '';
					currentSegmentRenderTimer = null;
					currentAnswerText = '';
					statusEl.setText('New session');
				sessionTitleEl.setText('New Session');
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
			ws.send(JSON.stringify({type, message, model: this.plugin.settings.selectedModel}));
			input.value = '';
			input.focus();
			// A new turn is now in flight: show the inline Stop button.
			setTurnActive(true);
			// Reset the think-tag parser state for the new turn.
			this._inThinkTag = false;
			// Flush the in-flight text segment so it renders before the new turn.
			closeCurrentSegment();
			currentAssistantMessage = null;
			currentThinkingBlock = null;
			currentAnswerBlock = null;
			currentSegmentText = '';
			currentAnswerText = '';
		};

		// Ingest button: a one-press way for a non-tech user to feed new
		// textbooks into the vault without typing a prompt. No LLM involved -
		// the backend scans learningMaterial/ for uningested PDFs, parses +
		// weaves them, and reports back. Weaving continues in the background
		// so the user isn't blocked.
		ingestButton.addEventListener('click', async () => {
			if (!ws || ws.readyState !== 1) {
				new Notice('VaultBot backend is not connected yet.');
				return;
			}
			ingestButton.setText('Ingesting...');
			ingestButton.setAttribute('disabled', 'disabled');
			appendUserMessage('(ingesting any new textbooks from learningMaterial/)');
			// Human-centered vision check: before ingesting, probe whether the
			// page-reading model can read textbook pages (equations/figures).
			// The probed model is the dedicated vision model if one is
			// configured, else the chat model. If it can't see images, alert
			// the user RIGHT HERE in the chat — in plain language — that they
			// should pick a vision model in Settings, so the LLM can later read
			// the pages it's pointed to. This is the moment a non-coder learns
			// their setup needs one extra field, not a silent failure later
			// when they ask a math question.
			try {
				const vcheck = await this.plugin.fetchVisionCheck();
				if (vcheck && vcheck.vision_capable === false) {
					const which = vcheck.source === 'vision'
						? 'your vision model'
						: 'your current chat model';
					const settingsPath = vcheck.source === 'vision'
						? 'VaultBot Settings → Vision Model'
						: 'VaultBot Settings → Vision Model (or pick a vision-capable chat model under LLM Backend)';
					appendAssistantMessage(
						`Heads up: ${which} (${vcheck.model || 'unknown'}) can't read images. ` +
						`That's fine for ingest (it just indexes the PDFs), but when you later ask about ` +
						`math/figures, I won't be able to see the equations on the page. ` +
						`Open ${settingsPath} and pick a vision-capable model ` +
						`(e.g. gpt-4o-mini, gemini-1.5-flash, qwen-vl, llava) so I can read textbook pages for you. ` +
						`Proceeding with ingest now...`
					);
				}
			} catch (e) { /* vision check is advisory, never blocks ingest */ }
			try {
				const resp = await fetch(this.backendUrl + '/ingest_learning_material', {
					method: 'POST',
					headers: {'Content-Type': 'application/json'},
					body: JSON.stringify({})
				});
				const data = await resp.json();
				const msg = data.message || `Ingested ${data.ingested || 0}, skipped ${data.skipped || 0}.`;
				appendAssistantMessage(msg);
				if (data.details) {
					for (const d of data.details) {
						if (d.error) appendAssistantMessage(`  X ${d.file}: ${d.error}`);
						else appendAssistantMessage(`  OK ${d.file}: ${d.notes_created} notes`);
					}
				}
			} catch (e) {
				appendAssistantMessage(`Ingest request failed: ${e.message || e}`);
			} finally {
				ingestButton.setText('Ingest');
				ingestButton.removeAttribute('disabled');
			}
		});
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
