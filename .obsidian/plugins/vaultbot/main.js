// VaultBot plugin entry point.
// The Obsidian plugin system loads this file. It requires the three
// class modules and re-exports the plugin class.
//
// Module layout:
//   main.js      — this file (entry point, ~10 lines)
//   plugin.js    — VaultBotPlugin (lifecycle, backend, auth, models, MCP, update)
//   settings.js  — VaultBotSettingTab (settings UI)
//   sidebar.js   — VaultBotSidebarView (chat UI, WebSocket, console)

const path = require('path');
const VaultBotPlugin = require(path.join(__dirname, 'plugin.js'));
module.exports = VaultBotPlugin;