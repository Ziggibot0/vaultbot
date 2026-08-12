const { ItemView, Notice, MarkdownRenderer } = require('obsidian');

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
		headerLeft.createEl('div', {cls: 'vaultbot-header-sub', text: 'a garden for your thoughts'});

		// Right side of the header: three compact model dropdowns (big /
		// small / vision) in the upper-right corner. Moved up from the
		// footer so model selection is always visible and the chat input
		// area stays uncluttered. Each dropdown is a tiny labeled select.
		const headerModels = headerEl.createDiv({cls: 'vaultbot-header-models'});

		// Populate a role <select> from the combined registry "pot". `models`
		// is [{id, model, provider_label, provider_type, vision, instruct,
		// roles}]. Options are keyed by registry model id (not bare model name),
		// grouped by provider, so the user sees local Ollama + cloud models
		// side-by-side and picks any of them into the role. `selectedId` is the
		// id currently assigned to the role. `allowNone` prepends a "(none)".
		const populateSelectPot = (sel, models, selectedId, {allowNone = true, visionOnly = false} = {}) => {
			sel.empty();
			let pool = models.filter(m => m.instruct);
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

		// History disclosure: a small "Recent" toggle in the header that
		// expands a list of past chat sessions (read from /sessions). This
		// makes closed/reopened Obsidian feel less like data loss: the user
		// can see their past conversations and pick up where they left off.
		// Selecting an entry loads its messages read-only into the chat.
		const historyToggle = headerLeft.createEl('button', {
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
			const wsUrl = this.backendUrl.replace('http', 'ws') + '/ws'
				+ (this.plugin._authToken ? '?token=' + encodeURIComponent(this.plugin._authToken) : '');
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
				} else if (msg.type === 'ollama_stats') {
					// Per-round Ollama eval stats from the backend (tokens/s,
					// load time, prompt processing speed). Updates the perf
					// section of the stats bar.
					updateStatsFromChat(msg);
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
						const labels = ['I don\'t know, use best practices', ...q.options];
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
			ws.send(JSON.stringify({type, message}));
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


module.exports = VaultBotSidebarView;
