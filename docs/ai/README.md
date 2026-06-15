# AI Tooling Guide

This repo is set up so Claude Code, Codex, and OpenCode can share the same
operating model.

## Canonical Instructions

- `AGENTS.md`: shared rules for all agents
- `CLAUDE.md`: Claude Code-specific wrapper
- `docs/ai/opencode.md`: OpenCode-specific wrapper
- `.codex/skills/*`: Codex repo-local skill references
- `.claude/commands/*`: Claude slash command prompts
- `.claude/agents/*`: Claude subagent prompts

## Recommended Agent Flow

1. Read `AGENTS.md`.
2. Use the relevant command/skill for the task.
3. Inspect existing code.
4. Implement narrowly.
5. Run targeted validation.
6. Rebuild Docker services if code is copied into images.
7. Report changed files and verification.

## Validation Commands

- `make smoke`
- `make frontend-build`
- `make backend-compile`
- `make rebuild-frontend`
- `make rebuild-backend`
- `make rebuild-backend-all`
- `make migrate`

## Tool Ownership

Use Claude Code for deep implementation and multi-file refactors.
Use Codex for fast repo edits, tests, browser verification, and code review.
Use OpenCode as a lighter terminal-native agent using the same `AGENTS.md`.

