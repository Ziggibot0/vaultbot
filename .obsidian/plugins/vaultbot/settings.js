const { PluginSettingTab, Setting, Notice } = require('obsidian');
const path = require('path');

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


module.exports = VaultBotSettingTab;
