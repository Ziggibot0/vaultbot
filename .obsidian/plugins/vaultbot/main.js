const { Plugin, Setting, ItemView, PluginSettingTab, Notice, MarkdownRenderer } = require('obsidian');
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
			researchBackend: 'tavily',
			tavilyApiKey: '',
			ttsEnabled: true,
			ttsMuted: false,
			ttsVoice: 'am_michael',
			ttsRate: 190,
			ttsMode: 'server'
		};
		this.backendStarting = false;
		this.mcpProcess = null;
		await this.loadSettings();

		this.addCommand({
			id: 'open-vaultbot-sidebar',
			name: 'Open VaultBot Sidebar',
			callback: () => {
				this.openSidebar();
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

		if (this.settings.autoStartBackend) {
			// Wait a moment for Obsidian to settle, then try a single start.
			setTimeout(() => this.startBackendIfNeeded(), 2000);
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
		await this.stopBackend();
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

	async fetchModels() {
		try {
			const response = await fetch(this.settings.backendUrl + '/models');
			if (!response.ok) return {models: [], current: ''};
			const data = await response.json();
			return {
				models: Array.isArray(data.models) ? data.models : [],
				current: data.current || ''
			};
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
			const venvPython = path.join(vaultRoot, 'vaultbot_venv', 'Scripts', 'pythonw.exe');
			const mcpPy = path.join(vaultRoot, 'vaultbot_backend', 'mcp_server.py');
			const fs = require('fs');
			// pythonw.exe = no console window. Fall back to python.exe if missing.
			const mcpPythonExe = fs.existsSync(venvPython) ? venvPython : path.join(vaultRoot, 'vaultbot_venv', 'Scripts', 'python.exe');
			if (!fs.existsSync(mcpPythonExe) || !fs.existsSync(mcpPy)) {
				console.warn('VaultBot MCP server: missing venv or mcp_server.py');
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
		const pidFile = path.join(vaultRoot, 'vaultbot_backend', 'vaultbot.pid');

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
	// Self-updater: pull the latest CODE from GitHub and apply it over the
	// live vault, WITHOUT touching any user state.
	//
	// What gets updated (code only):
	//   - vaultbot_backend/**/*.py            (the backend engine)
	//   - .obsidian/plugins/vaultbot/main.js   (this plugin file)
	//   - .obsidian/plugins/vaultbot/manifest.json
	//   - .obsidian/plugins/vaultbot/styles.css
	//
	// What is PRESERVED (never overwritten):
	//   - .obsidian/plugins/vaultbot/data.json (your keys, model, voice, etc.)
	//   - every .md doc in the vault (notes, chat logs, research, textbooks)
	//   - vaultbot_backend/*_log.json, sessions/, checkpoints/,
	//     vaultbot_index/, *.log, *.pid, kokoro_models/, stt_models/,
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
		const backendDir = path.join(vaultRoot, 'vaultbot_backend');
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
				'--exclude=*/vaultbot_backend/*.log',
				'--exclude=*/vaultbot_backend/*_log.json',
				'--exclude=*/vaultbot_backend/calibration_log.json',
				'--exclude=*/vaultbot_backend/claim_verification_log.json',
				'--exclude=*/vaultbot_backend/consolidation_log.json',
				'--exclude=*/vaultbot_backend/embedding_drift.json',
				'--exclude=*/vaultbot_backend/procedure_failure_log.json',
				'--exclude=*/vaultbot_backend/rag_eval_log.json',
				'--exclude=*/vaultbot_backend/touch_counts.json',
				'--exclude=*/vaultbot_backend/vaultbot.pid',
				'--exclude=*/vaultbot_backend/sessions',
				'--exclude=*/vaultbot_backend/sessions/*',
				'--exclude=*/vaultbot_backend/checkpoints',
				'--exclude=*/vaultbot_backend/checkpoints/*',
				'--exclude=*/vaultbot_backend/vaultbot_index',
				'--exclude=*/vaultbot_backend/vaultbot_index/*',
				'--exclude=*/vaultbot_backend/kokoro_models',
				'--exclude=*/vaultbot_backend/kokoro_models/*',
				'--exclude=*/vaultbot_backend/stt_models',
				'--exclude=*/vaultbot_backend/stt_models/*',
				'--exclude=*/vaultbot_backend/trash',
				'--exclude=*/vaultbot_backend/trash/*',
				'--exclude=*/vaultbot_backend/__pycache__',
				'--exclude=*/vaultbot_backend/__pycache__/*',
				'--exclude=*/vaultbot_backend/*/__pycache__',
				'--exclude=*/vaultbot_backend/*/__pycache__/*',
				'--exclude=*/vaultbot_backend/**/*.pyc'
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
			const srcBackend = path.join(archiveRoot, 'vaultbot_backend');
			if (!fs.existsSync(srcBackend)) throw new Error('Archive has no vaultbot_backend/ folder.');
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
		// (logs, sessions, models) that aren't in the repo are kept.
		async function copyCodeTree(src, dest) {
			const stack = [{ s: src, d: dest }];
			while (stack.length) {
				const { s, d } = stack.pop();
				fs.mkdirSync(d, { recursive: true });
				for (const entry of fs.readdirSync(s, { withFileTypes: true })) {
					const sp = path.join(s, entry.name);
					const dp = path.join(d, entry.name);
					if (entry.isDirectory()) {
						// Skip __pycache__ dirs everywhere — never propagate them.
						if (entry.name === '__pycache__') continue;
						stack.push({ s: sp, d: dp });
					} else if (entry.isFile()) {
						// Skip compiled bytecode and stale .bak files.
						if (entry.name.endsWith('.pyc') || entry.name.endsWith('.bak')) continue;
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

			const venvPython = path.join(vaultRoot, 'vaultbot_venv', 'Scripts', 'pythonw.exe');
			const mainPy = path.join(vaultRoot, 'vaultbot_backend', 'main.py');
			const logFile = path.join(vaultRoot, 'vaultbot_backend', 'backend.log');

			const fs = require('fs');
			// pythonw.exe is the windowless Python â€” it has no console at all,
			// so no window flashes on screen. Fall back to python.exe if pythonw
			// is somehow missing (shouldn't happen, but be safe).
			const pythonExe = fs.existsSync(venvPython) ? venvPython : path.join(vaultRoot, 'vaultbot_venv', 'Scripts', 'python.exe');
			if (!fs.existsSync(pythonExe)) {
				throw new Error('Python not found at ' + pythonExe);
			}
			if (!fs.existsSync(mainPy)) {
				throw new Error('Backend script not found at ' + mainPy);
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
			const altLog = path.join(vaultRoot, 'vaultbot_backend', `backend-${stamp}.log`);
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
					// OpenMP conflict guard: faster-whisper (libomp140) + faiss/torch
					// (libiomp5md) in one process crashes without this. Must be set
					// before Python imports torch/faiss.
					KMP_DUPLICATE_LIB_OK: 'TRUE'
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
			running = await this.waitForBackend();
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
					throw new Error('Backend process started but did not respond in time. Check vaultbot_backend/backend.log.');
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

		new Setting(containerEl)
			.setName('Backend URL')
			.setDesc('URL of the VaultBot backend API')
			.addText(text => text
				.setPlaceholder('http://localhost:8000')
				.setValue(this.plugin.settings.backendUrl)
				.onChange(async (value) => {
					this.plugin.settings.backendUrl = value;
					await this.plugin.saveSettings();
				}));

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
				const selected = this.plugin.settings.selectedModel || current || models[0];
				models.forEach(name => {
					const opt = modelDropdown.createEl('option', {text: name, attr: {value: name}});
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

		containerEl.createEl('h3', {text: 'Voice'});

		new Setting(containerEl)
			.setName('Speak replies')
			.setDesc('When on, VaultBot reads its full reply aloud after each answer.')
			.addToggle(toggle => toggle
				.setValue(this.plugin.settings.ttsEnabled)
				.onChange(async (value) => {
					this.plugin.settings.ttsEnabled = value;
					await this.plugin.saveSettings();
				}));

		new Setting(containerEl)
			.setName('TTS engine')
			.setDesc("'browser' uses the built-in speechSynthesis (fast, streaming, no setup). 'server' uses the backend's local Kokoro v1.0 neural voice (offline, natural, JARVIS-like).")
			.addDropdown(dropdown => dropdown
				.addOption('browser', 'Browser (speechSynthesis)')
				.addOption('server', 'Server (Kokoro v1.0)')
				.setValue(this.plugin.settings.ttsMode || 'server')
				.onChange(async (value) => {
					this.plugin.settings.ttsMode = value;
					await this.plugin.saveSettings();
				}));

		const voiceSetting = new Setting(containerEl)
			.setName('Voice')
			.setDesc('Kokoro voice for server TTS. (recommended) = JARVIS-like male voices.');
		const voiceDropdown = voiceSetting.controlEl.createEl('select');
		const setVoiceOptions = (voices) => {
			const cur = this.plugin.settings.ttsVoice || 'am_michael';
			voices.forEach(v => {
				const label = (v.recommended ? '(recommended) ' : '') + v.name;
				const opt = voiceDropdown.createEl('option', {text: label, attr: {value: v.id}});
				if (v.id === cur) opt.selected = true;
			});
		};
		voiceDropdown.addEventListener('change', async () => {
			this.plugin.settings.ttsVoice = voiceDropdown.value;
			await this.plugin.saveSettings();
		});
		// Try fetching voices from the backend; fall back to browser voices.
		try {
			const resp = await fetch(this.plugin.settings.backendUrl + '/voices');
			if (resp.ok) {
				const data = await resp.json();
				setVoiceOptions(data.voices || []);
			}
		} catch (e) { /* backend may be down */ }

		new Setting(containerEl)
			.setName('Speech rate (words/min)')
			.setDesc('Server TTS speed. 190 is a natural default.')
			.addText(text => text
				.setPlaceholder('190')
				.setValue(String(this.plugin.settings.ttsRate || 190))
				.onChange(async (value) => {
					const n = parseInt(value, 10);
					if (!isNaN(n) && n > 0) {
						this.plugin.settings.ttsRate = n;
						await this.plugin.saveSettings();
				}
			}));

		containerEl.createEl('h3', {text: 'Updates'});

		// One-click self-updater. Pulls the latest CODE from GitHub and
		// applies it over the live vault. User state is never touched:
		//   - data.json (your keys/model/voice) is preserved
		//   - all your .md notes, chat logs, research, textbooks stay put
		//   - backend runtime state (sessions, checkpoints, indexes, logs,
		//     models, pid) is left exactly as-is
		// Only vaultbot_backend/*.py + the plugin's main.js/manifest/styles
		// are replaced. The backend is stopped first (Windows locks .py
		// files while running) and restarted automatically when done.
		containerEl.createEl('div', {text:
			'Update VaultBot to the latest version from GitHub. This replaces ' +
			'only the code (backend Python files + this plugin) \u2014 your notes, ' +
			'chat logs, API keys, model choice, and all other settings are kept ' +
			'safe. The backend restarts automatically when the update finishes.',
			attr: {style: 'opacity:0.7;font-size:0.85em;margin:4px 0 10px 0;'}});

		const updateRow = containerEl.createDiv({attr: {style: 'display:flex;align-items:center;gap:8px;flex-wrap:wrap;'}});
		const refInput = updateRow.createEl('input', {type: 'text', attr: {
			placeholder: 'main (branch or tag)',
			style: 'flex:1;min-width:180px;'}});
		refInput.value = 'main';
		const updateBtn = updateRow.createEl('button', {text: 'Update from GitHub', cls: 'mod-cta'});
		const updateStatusEl = containerEl.createEl('div', {attr: {style: 'opacity:0.7;font-size:0.8em;min-height:1em;margin-top:6px;'}});

		// Show the currently-installed version from manifest.json so the user
		// can compare before/after.
		let installedVersion = '?';
		try {
			const fs = require('fs');
			const manPath = path.join(this.plugin.app.vault.adapter.getBasePath ? this.plugin.app.vault.adapter.getBasePath() : '', '.obsidian', 'plugins', 'vaultbot', 'manifest.json');
			if (fs.existsSync(manPath)) {
				installedVersion = JSON.parse(fs.readFileSync(manPath, 'utf8')).version || '?';
			}
		} catch (e) {}
		updateStatusEl.setText(`Installed version: ${installedVersion}`);

		let updating = false;
		updateBtn.addEventListener('click', async () => {
			if (updating) return;
			updating = true;
			updateBtn.setAttribute('disabled', 'disabled');
			updateBtn.setText('Updating…');
			try {
				const res = await this.plugin.performSelfUpdate((msg) => {
					updateStatusEl.setText(msg);
				}, refInput.value.trim() || 'main');
				if (res && res.ok) {
					updateStatusEl.setText(`Done. VaultBot is now v${res.version}.`);
					new Notice(`VaultBot updated to v${res.version}.`);
				} else {
					updateStatusEl.setText(`Update failed: ${res && res.error ? res.error : 'unknown error'}`);
				}
			} catch (e) {
				updateStatusEl.setText('Update failed: ' + (e && e.message ? e.message : String(e)));
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

		const statusEl = this.contentEl.createDiv({cls: 'vaultbot-status'});

		const chatContainer = this.contentEl.createDiv({cls: 'vaultbot-chat-container'});

		let connectionCheckInterval = null;
		let backendWasOnline = false;
		const setStatus = (text, clickable = false) => {
			statusEl.setText(text);
			statusEl.style.cursor = clickable ? 'pointer' : 'default';
			if (clickable) {
				statusEl.onclick = async () => {
					statusEl.onclick = null;
					await this.plugin.startBackendIfNeeded();
					startBackendAndConnect();
				};
			} else {
				statusEl.onclick = null;
			}
		};

		const ensureConnection = async () => {
			const running = await this.plugin.isBackendRunning();
			if (running) {
				setStatus('Backend online');
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
			setStatus('Backend offline - click to start', true);
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
			const online = await this.plugin.waitForBackend(5000, 250);
			if (!online) {
				modelSelect.empty();
				modelSelect.createEl('option', {text: 'Backend offline', attr: {disabled: true}});
				return;
			}
			const {models, current} = await this.plugin.fetchModels();
			modelSelect.empty();
			if (!models.length) {
				modelSelect.createEl('option', {text: 'No models found', attr: {disabled: true}});
				return;
			}
			const selected = this.plugin.settings.selectedModel || current || models[0];
			models.forEach(name => {
				const opt = modelSelect.createEl('option', {text: name, attr: {value: name}});
				if (name === selected) opt.selected = true;
			});
			this.plugin.settings.selectedModel = selected;
			await this.plugin.saveSettings();
			await this.plugin.setBackendModel(selected);
		};
		refreshModels();
		modelSelect.addEventListener('change', async () => {
			this.plugin.settings.selectedModel = modelSelect.value;
			await this.plugin.saveSettings();
			await this.plugin.setBackendModel(modelSelect.value);
		});
		const refreshBtn = modelBar.createEl('span', {text: '↻', cls: 'vaultbot-model-refresh'});
		refreshBtn.title = 'Refresh model list';
		refreshBtn.addEventListener('click', () => refreshModels());

		const inputContainer = footerEl.createDiv({cls: 'vaultbot-input-container'});
		// The chat bar: textarea + an inline Stop button that only appears
		// while a turn is in flight (so there's always something to stop).
		const chatBar = inputContainer.createDiv({cls: 'vaultbot-chat-bar'});
		const input = chatBar.createEl('textarea', {
			cls: 'vaultbot-input',
			attr: {placeholder: 'Ask VaultBot...', rows: '3'}
		});
		const stopButton = chatBar.createEl('button', {text: 'Stop', cls: 'vaultbot-btn vaultbot-btn-quiet vaultbot-btn-stop'});
		stopButton.title = 'Interrupt VaultBot immediately. Also stops any voice playback.';
		stopButton.style.display = 'none'; // hidden until a turn is active

		// Action row: clay (primary) buttons grouped, then moss (quiet)
		// buttons grouped — each cluster sits together.
		const buttonContainer = inputContainer.createDiv({cls: 'vaultbot-button-container'});
		const clayGroup = buttonContainer.createDiv({cls: 'vaultbot-btn-group vaultbot-btn-group-clay'});
		const mossGroup = buttonContainer.createDiv({cls: 'vaultbot-btn-group vaultbot-btn-group-moss'});
		const ingestButton = clayGroup.createEl('button', {text: 'Ingest', cls: 'vaultbot-btn'});
		const callButton = clayGroup.createEl('button', {text: 'Call', cls: 'vaultbot-btn'});
		const muteButton = clayGroup.createEl('button', {text: 'Mute', cls: 'vaultbot-btn vaultbot-btn-mute'});
		const restartButton = mossGroup.createEl('button', {text: 'Restart', cls: 'vaultbot-btn vaultbot-btn-quiet vaultbot-btn-restart'});
		callButton.title = 'Hold to talk, or click to toggle recording. Your speech is transcribed locally and sent as a chat message; the reply is spoken back.';
		ingestButton.title = 'Ingest any new textbooks from the learningMaterial/ folder into the vault. No AI involved - just parses and links them. Weaving happens in the background.';
		muteButton.title = 'Toggle voice playback on/off. Interrupts the current utterance + discards the queued audio when muted.';
		restartButton.title = 'Restart the VaultBot backend. Use this after code changes or if the bot seems stuck. Takes a few seconds.';

		// --- Voice: recording + STT + TTS --------------------------------
		// Press & hold (or click toggle) the Call button -> MediaRecorder
		// captures audio -> POST /stt (vosk, offline) -> text goes into the
		// input and auto-sends as a chat. The assistant's final reply is
		// spoken aloud via the browser's speechSynthesis or the backend's
		// local SAPI voice (per settings). No cloud keys, no per-call cost.
		let mediaRecorder = null;
		let audioChunks = [];
		let isRecording = false;
		let recordingSince = 0;
		const supported = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
		if (!supported) callButton.setAttribute('disabled', 'disabled');

		const setCallState = (recording) => {
			isRecording = recording;
			callButton.setText(recording ? 'Recording...' : 'Call');
			callButton.toggleClass('vaultbot-recording', recording);
		};

		const stopAndSend = async () => {
			if (!isRecording || !mediaRecorder) return;
			setCallState(false);
			try { mediaRecorder.stop(); } catch (e) {}
		};

		const startRecording = async () => {
			if (isRecording) return;
			if (!supported) { new Notice('This browser cannot capture audio.'); return; }
			// Don't bother recording/transcribing when muted — mute means go
			// fully text-only (no voice in either direction).
			if (ttsMuted) { new Notice('Unmute first to use voice.'); return; }
			try {
				const stream = await navigator.mediaDevices.getUserMedia({audio: true});
				audioChunks = [];
				// Prefer OGG/Opus: the backend decodes it with pure-Python
				// soundfile (libsndfile), which has no DLL dependency that a
				// Windows Application Control policy can block. The default
				// MediaRecorder format is webm/opus, which needs PyAV or
				// ffmpeg to decode — both unavailable/blocked on this box.
				// Fall back to whatever the browser offers if OGG isn't
				// supported (Chrome records webm; the backend still tries
				// soundfile/PyAV/pydub on whatever arrives).
				const candidates = ['audio/ogg;codecs=opus', 'audio/ogg', 'audio/webm;codecs=opus', 'audio/webm', ''];
				let pickedMime = '';
				for (const m of candidates) {
					if (m === '' || MediaRecorder.isTypeSupported(m)) { pickedMime = m; break; }
				}
				mediaRecorder = pickedMime ? new MediaRecorder(stream, {mimeType: pickedMime}) : new MediaRecorder(stream);
				mediaRecorder.ondataavailable = (e) => {
					if (e.data && e.data.size) audioChunks.push(e.data);
				};
				mediaRecorder.onstop = async () => {
					stream.getTracks().forEach(t => t.stop());
					const blob = new Blob(audioChunks, {type: mediaRecorder.mimeType || 'audio/ogg'});
					audioChunks = [];
					if (!blob.size) return;
					statusEl.setText('Transcribing...');
					try {
						const resp = await fetch(this.backendUrl + '/stt', {
							method: 'POST',
							headers: {'Content-Type': blob.type},
							body: blob
						});
						if (!resp.ok) { new Notice('Transcription failed (' + resp.status + ').'); statusEl.setText('Done'); return; }
						const data = await resp.json();
						const text = (data.text || '').trim();
						if (!text) { new Notice('No speech detected.'); statusEl.setText('Done'); return; }
						input.value = text;
						statusEl.setText('Done');
						send('chat');
					} catch (e) {
						new Notice('STT error: ' + e.message);
						statusEl.setText('Done');
					}
				};
				recordingSince = Date.now();
				mediaRecorder.start();
				setCallState(true);
			} catch (e) {
				new Notice('Microphone unavailable: ' + e.message);
			}
		};

		// Click toggles; press-and-hold is the "walkie-talkie" mode.
		let holdTimer = null;
		const onDown = (e) => {
			if (e.pointerType === 'mouse') {
				// start on press, stop on release
				holdTimer = null;
				startRecording();
			} else {
				// touch/pen: also press-and-hold
				startRecording();
			}
		};
		const onUp = (e) => { stopAndSend(); };
		callButton.addEventListener('pointerdown', (e) => { e.preventDefault(); onDown(e); });
		callButton.addEventListener('pointerup', (e) => { e.preventDefault(); onUp(e); });
		callButton.addEventListener('pointerleave', (e) => { if (isRecording) onUp(e); });
		callButton.addEventListener('pointercancel', onUp);

		// --- Streaming TTS: speak as tokens stream, not all at the end ----
		// Accumulates streamed answer text and flushes complete sentences to
		// the browser's speechSynthesis queue the moment a sentence boundary
		// is seen. This makes the voice start talking in ~1 sentence of
		// latency instead of waiting for the whole reply. Server (Kokoro)
		// TTS is whole-utterance, so streaming mode requires the browser
		// engine; if ttsMode==='server' we fall back to one-shot at the end.
		// Mute state is persisted in plugin settings so it survives across
		// turns, reloads, and sessions.
		let ttsMuted = !!(this.plugin.settings.ttsMuted);
		let ttsSentenceBuffer = '';
		let ttsActiveUtterances = [];
		const SENTENCE_BOUNDARY = /([.!?])\s+/;

		const updateMuteButton = () => {
			muteButton.setText(ttsMuted ? 'Unmute' : 'Mute');
			muteButton.toggleClass('vaultbot-mute-active', ttsMuted);
		};

		const stopAllTTS = () => {
			try { window.speechSynthesis.cancel(); } catch (e) {}
			// Stop + discard the server WAV: pause the Audio element, revoke
			// its object URL so the browser releases the blob, and null it.
			if (currentAudio) {
				try { currentAudio.pause(); } catch (e) {}
				try {
					if (currentAudio.src && currentAudio.src.startsWith('blob:')) {
						URL.revokeObjectURL(currentAudio.src);
					}
				} catch (e) {}
				currentAudio = null;
			}
			ttsSentenceBuffer = '';
			ttsActiveUtterances = [];
		};

		// Speak a sentence immediately (streaming). No-op if muted.
		const speakSentence = (sentence) => {
			sentence = (sentence || '').trim();
			if (!sentence || ttsMuted || !window.speechSynthesis) return;
			try {
				const u = new SpeechSynthesisUtterance(sentence);
				if (this.plugin.settings.ttsVoice) {
					const v = window.speechSynthesis.getVoices().find(v => v.name === this.plugin.settings.ttsVoice || v.voiceURI === this.plugin.settings.ttsVoice);
					if (v) u.voice = v;
				}
				u.rate = (this.plugin.settings.ttsRate || 190) / 190;
				ttsActiveUtterances.push(u);
				u.onend = () => { ttsActiveUtterances = ttsActiveUtterances.filter(x => x !== u); };
				u.onerror = () => { ttsActiveUtterances = ttsActiveUtterances.filter(x => x !== u); };
				window.speechSynthesis.speak(u);
			} catch (e) {}
		};

		// Feed a streaming text chunk into the TTS pipeline. Flushes complete
		// sentences; keeps the trailing fragment buffered for the next chunk.
		const feedStreamingTTS = (textChunk) => {
			if (!textChunk || ttsMuted) return;
			if (this.plugin.settings.ttsMode !== 'browser' || !window.speechSynthesis) return;
			ttsSentenceBuffer += textChunk;
			// Split on sentence boundaries; keep the last fragment buffered.
			let parts = ttsSentenceBuffer.split(SENTENCE_BOUNDARY);
			while (parts.length >= 3) {
				// split() with a capturing group yields [text, delim, text, delim, ...]
				const sentence = parts[0] + parts[1];
				parts = parts.slice(2);
				if (sentence.trim()) speakSentence(sentence);
			}
			ttsSentenceBuffer = parts.join('');
		};

		// Stop button: interrupt the backend + kill voice playback. Sits
		// inline in the chat bar and only appears while a turn is active.
		stopButton.addEventListener('click', () => {
			// Ask the backend to cancel the in-flight task. The backend then
			// emits a 'stopped' event that clears the local turn state.
			if (ws && ws.readyState === WebSocket.OPEN) {
				try { ws.send(JSON.stringify({type: 'stop'})); } catch (e) {}
			}
			// Kill voice + discard any queued WAV immediately, don't wait on
			// the backend round-trip.
			stopAllTTS();
			endActivity();
			// Optimistically hide the stop button + flush partial text so the
			// UI reacts instantly. The 'stopped' handler will finish cleanup.
			setTurnActive(false);
			closeCurrentSegment();
			statusEl.setText('Interrupted');
			currentAssistantMessage = null;
			currentThinkingBlock = null;
			currentAnswerBlock = null;
			currentSegmentText = '';
			currentAnswerText = '';
		});

		// Mute button: toggle voice on/off. Muting kills the current
		// utterance AND discards the queued server WAV (revoke its blob URL
		// so the audio is gone, not just paused). State is persisted to
		// plugin settings so it survives across turns + sessions.
		muteButton.addEventListener('click', async () => {
			ttsMuted = !ttsMuted;
			this.plugin.settings.ttsMuted = ttsMuted;
			try { await this.plugin.saveSettings(); } catch (e) {}
			updateMuteButton();
			if (ttsMuted) stopAllTTS();
		});
		updateMuteButton();

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
		let currentAnswerText = '';        // full plain text across all segments, for TTS
		// Live activity line: shows the current stage + elapsed time, updated
		// in place by progress/heartbeat events so the user is never staring
		// at a frozen "Calling X..." with no idea if it's still working.
		let currentActivityEl = null;
		let activityStartTs = 0;
		let activityTimer = null;

		const appendUserMessage = (text) => {
			const div = chatContainer.createDiv({cls: 'vaultbot-message user'});
			div.createSpan({text});
			chatContainer.scrollTop = chatContainer.scrollHeight;
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
					chatContainer.scrollTop = chatContainer.scrollHeight;
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
					chatContainer.scrollTop = chatContainer.scrollHeight;
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
				chatContainer.scrollTop = chatContainer.scrollHeight;
			});
			chatContainer.scrollTop = chatContainer.scrollHeight;
			// Return the block so callers (e.g. the Restart button) can
			// update its text in place as a status line changes.
			return block;
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
			chatContainer.scrollTop = chatContainer.scrollHeight;
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
			chatContainer.scrollTop = chatContainer.scrollHeight;
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
					chatContainer.scrollTop = chatContainer.scrollHeight;
					return;
				}

				if (msg.type === 'status') {
					statusEl.setText(msg.content);
				} else if (msg.type === 'thinking') {
					if (!currentAssistantMessage) startAssistantMessage();
					// Show the thinking block live while the model reasons.
					setThinkingVisible(true);
					currentThinkingBlock.setText((currentThinkingBlock.getText() || '') + msg.content);
					chatContainer.scrollTop = chatContainer.scrollHeight;
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
						chatContainer.scrollTop = chatContainer.scrollHeight;
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
					// Stream into TTS so the voice starts as soon as a full
					// sentence is available, not at the very end.
					if (this.plugin.settings.ttsEnabled) feedStreamingTTS(raw);
					chatContainer.scrollTop = chatContainer.scrollHeight;
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
					chatContainer.scrollTop = chatContainer.scrollHeight;
				} else if (msg.type === 'answer_done') {
					endActivity();
					// Flush the final text segment so its markdown renders.
					closeCurrentSegment();
					statusEl.setText('Done');
					setTurnActive(false);
					const spokenText = currentAnswerText;
					currentAssistantMessage = null;
					currentThinkingBlock = null;
					currentAnswerBlock = null;
					currentSegmentText = '';
					currentAnswerText = '';
					if (this.plugin.settings.ttsEnabled && spokenText.trim() && !ttsMuted) {
						// Streaming (browser) mode already spoke sentence-by-
						// sentence as chunks arrived. Flush any final fragment
						// left in the buffer (no trailing punctuation).
						if (this.plugin.settings.ttsMode === 'browser') {
							if (ttsSentenceBuffer.trim()) {
								speakSentence(ttsSentenceBuffer);
								ttsSentenceBuffer = '';
							}
						} else {
							// Server (Kokoro) mode is whole-utterance; speak
							// the full reply once now.
							speakReply(spokenText);
						}
					}
				} else if (msg.type === 'error') {
					endActivity();
					setTurnActive(false);
					statusEl.setText('Error: ' + msg.content);
					const div = chatContainer.createDiv({cls: 'vaultbot-message system error'});
					div.createSpan({text: 'Error: ' + msg.content});
					chatContainer.scrollTop = chatContainer.scrollHeight;
				} else if (msg.type === 'stopped') {
					// Backend confirmed an interrupt (stop button or new msg).
					stopAllTTS();
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
					ttsSentenceBuffer = '';
				} else if (msg.type === 'session_reset') {
					// /new command: clear the chat UI for a fresh session.
					stopAllTTS();
					endActivity();
					chatContainer.empty();
					currentAssistantMessage = null;
					currentThinkingBlock = null;
					currentAnswerBlock = null;
					currentSegmentText = '';
					currentSegmentRenderTimer = null;
					currentAnswerText = '';
					ttsSentenceBuffer = '';
					statusEl.setText('New session');
					const div = chatContainer.createDiv({cls: 'vaultbot-message system'});
					div.createSpan({text: msg.content || 'New session started.'});
					chatContainer.scrollTop = chatContainer.scrollHeight;
				} else if (msg.type === 'restart') {
					// Backend requested restart — same code path as the GUI button.
					statusEl.setText('Backend requested restart...');
					this.plugin.restartBackend();
				}
			};
			ws.onclose = () => {
				setStatus('Disconnected from backend - retrying...');
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
					setStatus('Backend online - connecting...');
					connectWebSocket();
				} else if (attempts > 30) {
					window.clearInterval(poll);
					setStatus('Backend did not start in time', true);
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
		// Interrupt any in-flight reply + stop voice so the new message
		// takes over immediately instead of queueing behind the old one.
		stopAllTTS();
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
		ttsSentenceBuffer = '';
			currentActivityEl = null;
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
			stopAllTTS();
			appendUserMessage('(restarting backend)');
			let statusDiv = appendAssistantMessage('Restarting backend...');
			try {
				const ok = await this.plugin.restartBackend((msg) => {
					if (statusDiv) statusDiv.setText(msg);
				});
				if (statusDiv) statusDiv.setText(ok ? 'Backend restarted and ready.' : 'Backend may not have come back up. Check the backend log.');
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
				}
			}
		});
		input.addEventListener('keydown', (e) => {
			if (e.key === 'Enter' && !e.shiftKey) {
				e.preventDefault();
				send('chat');
			}
		});

		// --- TTS: speak the assistant's reply aloud ----------------------
		// Two engines, picked per settings:
		//  - 'browser': window.speechSynthesis â€” zero setup, streams as the
		//    browser allows, no backend round-trip.
		//  - 'server': POST the text to /tts; the backend synthesizes with the
		//    local Windows SAPI voice (truly offline) and returns a WAV we
		//    play. More robust when the browser has no/enqueued voices.
		let currentAudio = null;
		const speakReply = async (text) => {
			text = (text || '').trim();
			if (!text) return;
			// Honor the mute toggle — without this, server (Kokoro) mode kept
			// talking after the user muted, which is why the Mute button
			// appeared to do nothing (the default ttsMode is 'server').
			if (ttsMuted) return;
			// Cancel anything still playing so a new answer doesn't overlap.
			try { window.speechSynthesis.cancel(); } catch (e) {}
			if (currentAudio) { try { currentAudio.pause(); } catch (e) {} currentAudio = null; }

			if (this.plugin.settings.ttsMode === 'browser' && window.speechSynthesis) {
				try {
					const u = new SpeechSynthesisUtterance(text);
					if (this.plugin.settings.ttsVoice) {
						const v = window.speechSynthesis.getVoices().find(v => v.name === this.plugin.settings.ttsVoice || v.voiceURI === this.plugin.settings.ttsVoice);
						if (v) u.voice = v;
					}
					u.rate = (this.plugin.settings.ttsRate || 190) / 190;
					window.speechSynthesis.speak(u);
					return;
				} catch (e) { /* fall through to server */ }
			}

			try {
				const resp = await fetch(this.backendUrl + '/tts', {
					method: 'POST',
					headers: {'Content-Type': 'application/json'},
					body: JSON.stringify({
						text: text,
						voice: this.plugin.settings.ttsVoice || undefined,
						rate: this.plugin.settings.ttsRate || 190
					})
				});
				if (!resp.ok) return;
				const blob = await resp.blob();
				const url = URL.createObjectURL(blob);
				currentAudio = new Audio(url);
				currentAudio.onended = () => { URL.revokeObjectURL(url); currentAudio = null; };
				currentAudio.play().catch(() => {});
			} catch (e) { /* silent fail â€” TTS is best-effort */ }
		};

		input.focus();
	}
}

module.exports = VaultBotPlugin;
