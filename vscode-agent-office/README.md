# Agent Office Dashboard

Local VS Code Activity Bar extension for this workspace.

It contributes an **Agent Office** Activity Bar icon and renders the dashboard
directly inside a VS Code Webview. It does not open a browser and does not use a
local web server.

The Webview reads these workspace files through the extension host:

- `pipeline_state.json`
- `test_results.jsonl`
- `logs/token_log.jsonl`
- `agent_office_control.json`

Token budget is read from `AGENT_OFFICE_TOKEN_BUDGET` when set, otherwise it
uses `200000`.
