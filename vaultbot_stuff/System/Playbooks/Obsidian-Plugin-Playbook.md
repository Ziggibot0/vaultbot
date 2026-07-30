---
type: playbook
status: active
created: 2026-07-29
summary: "Obsidian plugin development playbook for VaultBot: plugin structure, lifecycle, settings, views, commands, backend communication, WebSocket patterns, and GUI components. Sourced from the actual VaultBot plugin code (main.js, 2508 lines)."
tags: [obsidian, plugin, gui, playbook, reference, javascript]
sources:
  - "VaultBot plugin source code (.obsidian/plugins/vaultbot/main.js)"
  - "Obsidian API (docs.obsidian.md)"
depends_on:
  - "[[Research-Roadmap]]"
  - "[[Python-3.11-Playbook]]"
---

# Obsidian Plugin Development Playbook

> The VaultBot plugin (v1.5.2, 2508 lines of JS) is the bridge between Obsidian and the Python backend. This playbook documents the patterns used in the real plugin code.

## Plugin Structure

### manifest.json
```json
{
  "id": "vaultbot",
  "name": "VaultBot",
  "version": "1.5.2",
  "minAppVersion": "0.15.0",
  "description": "AI assistant that lives in your Obsidian vault",
  "author": "VaultBot Developer",
  "isDesktopOnly": true
}
```

Key fields:
- `id`: unique plugin identifier (lowercase, no spaces)
- `name`: display name in Obsidian settings
- `minAppVersion`: minimum Obsidian version required
- `isDesktopOnly`: true if plugin uses Node.js APIs (VaultBot uses child_process)

### File Layout
```
.obsidian/plugins/vaultbot/
  manifest.json    # Plugin metadata
  main.js          # Compiled plugin code (the actual JS)
  styles.css       # Plugin styles
  data.json        # Saved settings (auto-generated)
  mcp.json         # MCP server config (auto-generated)
```

## Plugin Lifecycle

### onload() - Plugin initialization
```javascript
async onload() {
    // 1. Default settings
    this.settings = {
        backendUrl: 'http://127.0.0.1:8000',
        autoStartBackend: true,
        selectedModel: "",
        // ... more settings
    };
    await this.loadSettings();

    // 2. Register commands
    this.addCommand({
        id: 'open-vaultbot-sidebar',
        name: 'Open VaultBot Sidebar',
        callback: () => { this.openSidebar(); }
    });

    // 3. Register ribbon icon
    this.addRibbonIcon('bot', 'VaultBot', () => {
        this.openSidebar();
    });

    // 4. Register settings tab
    this.addSettingTab(new VaultBotSettingTab(this.app, this));

    // 5. Register custom view
    this.registerView(
        'vaultbot-sidebar',
        (leaf) => new VaultBotSidebarView(leaf, backendUrl, this)
    );

    // 6. Auto-start backend if configured
    if (this.settings.autoStartBackend) {
        setTimeout(() => this.startBackendIfNeeded(), 2000);
    }
}
```

### onunload() - Cleanup
```javascript
async onunload() {
    // Remove event listeners
    if (this._beforeUnloadHandler) {
        window.removeEventListener("beforeunload", this._beforeUnloadHandler);
    }
    // Stop MCP server
    this.stopMcpServer();
    // Stop backend (unless reloading)
    if (this._isReloading) {
        console.log("Keeping backend alive for reload");
    } else {
        await this.stopBackend();
    }
}
```

## Settings Pattern

### Loading and Saving
```javascript
async loadSettings() {
    const saved = await this.loadData() || {};
    this.settings = Object.assign({}, this.settings, saved);
}

async saveSettings() {
    await this.saveData(this.settings);
}
```

### Settings Tab (GUI)
```javascript
class VaultBotSettingTab extends PluginSettingTab {
    constructor(app, plugin) {
        super(app, plugin);
        this.plugin = plugin;
    }

    display() {
        const { containerEl } = this;
        containerEl.empty();

        // Text setting
        new Setting(containerEl)
            .setName('Backend URL')
            .setDesc('URL of the VaultBot backend')
            .addText(text => text
                .setPlaceholder("http://127.0.0.1:8000")
                .setValue(this.plugin.settings.backendUrl)
                .onChange(async (value) => {
                    this.plugin.settings.backendUrl = value;
                    await this.plugin.saveSettings();
                }));

        // Toggle setting
        new Setting(containerEl)
            .setName('Auto-start backend')
            .setDesc('Start backend when Obsidian opens')
            .addToggle(toggle => toggle
                .setValue(this.plugin.settings.autoStartBackend)
                .onChange(async (value) => {
                    this.plugin.settings.autoStartBackend = value;
                    await this.plugin.saveSettings();
                }));

        // Dropdown setting
        new Setting(containerEl)
            .setName('Model')
            .setDesc('Select the LLM model')
            .addDropdown(dropdown => dropdown
                .addOption("model1", "Model 1")
                .addOption("model2", "Model 2")
                .setValue(this.plugin.settings.selectedModel)
                .onChange(async (value) => {
                    this.plugin.settings.selectedModel = value;
                    await this.plugin.saveSettings();
                }));
    }
}
```

## Custom View (Sidebar)

```javascript
class VaultBotSidebarView extends ItemView {
    constructor(leaf, backendUrl, plugin) {
        super(leaf);
        this.backendUrl = backendUrl;
        this.plugin = plugin;
    }

    getViewType() { return "vaultbot-sidebar"; }
    getDisplayText() { return "VaultBot"; }
    getIcon() { return "bot"; }

    async onOpen() {
        const container = this.containerEl.children[1];
        container.empty();
        // Build UI here
        container.createEl("h2", { text: "VaultBot" });
        // ... add chat interface, controls, etc.
    }

    async onClose() {
        // Cleanup
    }
}
```

## Backend Communication

### HTTP Requests
```javascript
// GET request
async isBackendRunning() {
    try {
        const response = await fetch(
            this.settings.backendUrl + "/health",
            { method: "GET" }
        );
        return response.ok;
    } catch (e) {
        return false;
    }
}

// POST request
async setBackendModel(model) {
    const response = await fetch(
        this.settings.backendUrl + "/set_model",
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model })
        }
    );
    return response.json();
}
```

### WebSocket (real-time streaming)
```javascript
connectWebSocket() {
    const wsUrl = this.settings.backendUrl
        .replace("http://", "ws://")
        .replace("https://", "wss://")
        + "/ws";

    this.ws = new WebSocket(wsUrl);

    this.ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        switch (data.type) {
            case "chat":
                this.appendChat(data.content);
                break;
            case "tool_call":
                this.showToolCall(data);
                break;
            case "reload_plugin":
                this.plugin.reloadSelf();
                break;
        }
    };

    this.ws.onclose = () => {
        // Reconnect after delay
        setTimeout(() => this.connectWebSocket(), 3000);
    };
}
```

## Backend Lifecycle Management

### Starting the Backend
```javascript
async startBackendIfNeeded() {
    if (await this.isBackendRunning()) return;

    const venvPython = path.join(
        this.app.vault.adapter.getBasePath(),
        "vaultbot_venv", "Scripts", "python.exe"
    );
    const mainScript = path.join(
        this.app.vault.adapter.getBasePath(),
        "vaultbot_backend", "main.py"
    );

    this.backendProcess = spawn(venvPython, [mainScript], {
        cwd: this.app.vault.adapter.getBasePath(),
        detached: false,
        windowsHide: true
    });
}
```

### Stopping the Backend
```javascript
async stopBackend() {
    // Send shutdown request
    try {
        navigator.sendBeacon(
            this.settings.backendUrl + "/shutdown",
            new Blob([''], {type: 'text/plain'})
        );
    } catch (e) {}

    // Kill process if still running
    if (this.backendProcess) {
        try { this.backendProcess.kill(); } catch (e) {}
    }
}
```

### Plugin Self-Reload (without killing backend)
```javascript
async reloadSelf() {
    this._isReloading = true;
    // Schedule re-enable from setTimeout (survives plugin destruction)
    setTimeout(async () => {
        await this.app.plugins.enablePlugin('vaultbot');
    }, 500);
    // Disable (onunload sees _isReloading, keeps backend alive)
    await this.app.plugins.disablePlugin('vaultbot');
}
```

## GUI Components

### Notices (toast notifications)
```javascript
new Notice("Backend started successfully");
new Notice("Error: " + error, 5000); // 5 second duration
```

### Creating DOM elements
```javascript
const div = container.createEl("div", { cls: "vaultbot-chat" });
const button = div.createEl("button", { text: "Send" });
button.onclick = () => this.sendMessage();
```

### Markdown rendering
```javascript
const { MarkdownRenderer } = require("obsidian");
MarkdownRenderer.renderMarkdown(
    markdownText,
    targetEl,
    sourcePath,
    this.component
);
```

## Key Patterns Learned from VaultBot Plugin

| Pattern | Implementation | Why It Matters
|---|---|---
| **IPv4 binding** | Use 127.0.0.1, not localhost | Windows resolves localhost to ::1 (IPv6), backend binds IPv4 only
| **sendBeacon for shutdown** | navigator.sendBeacon during beforeunload | Survives renderer teardown, unlike fetch
| **Plugin reload** | disable + setTimeout(enable) | Picks up main.js changes without manual toggle
| **Backend coexistence** | _isReloading flag in onunload | Keeps backend alive during plugin reload (2s vs 8s)
| **Health check** | GET /health, fallback GET / | HEAD returns 405 on FastAPI GET-only routes
| **WebSocket reconnect** | onclose -> setTimeout(reconnect) | Handles backend restarts gracefully
| **Settings migration** | Check for old values in loadSettings | Migrates localhost to 127.0.0.1 automatically

## Editing the Plugin

When editing main.js:
1. Use `js_safe_write` (not `safe_write`) - it validates JS syntax with node --check
2. After writing, call `plugin_reload` to pick up changes without restarting backend
3. If backend restart is needed, call `backend_restart`
4. The .bak file is auto-created by js_safe_write for rollback

When editing styles.css:
1. Use `js_safe_write` or direct file write
2. Call `plugin_reload` after writing

## Related

- [[Research-Roadmap]] - Phase 3, topic 6
- [[Python-3.11-Playbook]] - the backend is Python, the plugin is JS
- [[What-Is-A-Bit]] - the plugin is bits that control other bits
- VaultBot plugin source - `.obsidian/plugins/vaultbot/main.js` (2508 lines)