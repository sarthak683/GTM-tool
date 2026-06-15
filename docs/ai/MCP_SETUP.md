# MCP Setup Notes

This file tracks recommended MCP servers for the owner to install in each tool.
Keep MCP minimal. Too many servers make agents slower and less predictable.

## Claude Code

Installed for this repo:

- Playwright MCP in `.mcp.json` for localhost UI testing
- Context7 Claude plugin at user scope for current library docs and docs skills
- Sequential Thinking MCP for complex implementation planning
- Memory MCP for persistent project facts
- Postgres MCP scoped to local dev DB only
- Filesystem MCP scoped to `/Users/sarthak/GTM-tool`
- GitHub MCP using `gh auth token` at runtime, so no token is stored in repo

Not installed by default:

- Kubernetes MCP. Use explicit `kubectl` commands and handoff files instead.
- Production Postgres MCP. Local Postgres only is configured.

Keep secrets out of this repo.

## Codex

Installed:

- Browser plugin for localhost verification
- Context7 MCP in `~/.codex/config.toml`
- Sequential Thinking MCP
- Memory MCP
- local Postgres MCP
- repo-scoped Filesystem MCP
- GitHub MCP using `gh auth token` at runtime
- Repo-local and global CRM skills under `.codex/skills/` and `~/.codex/skills/`

Already available in this environment:

- GitHub plugin
- Browser plugin
- Computer Use plugin

Codex already reads `AGENTS.md` in this repo.

## OpenCode

Installed:

- OpenCode CLI in `~/.local/bin/opencode`
- Context7 MCP in `opencode.jsonc`
- Playwright MCP in `opencode.jsonc`
- Sequential Thinking MCP in `opencode.jsonc`
- Memory MCP in `opencode.jsonc`
- local Postgres MCP in `opencode.jsonc`
- repo-scoped Filesystem MCP in `opencode.jsonc`
- GitHub MCP using `gh auth token` at runtime in `opencode.jsonc`

Use `opencode.jsonc` as the repo-level starting config. Put credentials and
machine-specific provider settings in your user-level OpenCode config.

## Security

Do not commit:

- API keys
- database passwords
- kubeconfigs
- OAuth tokens
- ACR credentials
- production DB connection strings
