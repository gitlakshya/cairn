# contexIT

AI-powered repository wiki that stays in sync with your code.

## What it does

Generates a compact wiki in `.context/` — thematic pages averaging ≤80 lines each, with OKF frontmatter, grounded in your actual files and git history. Updates surgically (only pages affected by recent changes). No-ops when nothing changed.

## Install

```bash
npm install && npm run build
```

contexIT is a self-contained **Claude Code plugin** — `.claude-plugin/` + `commands/wiki.md` + `hooks/` live at the repo root, so the same checkout also installs directly as a **Copilot CLI plugin** (it reads the same `.claude-plugin/plugin.json` / `marketplace.json`). Codex and other `AGENTS.md`-style agents pick up the twin skill under `.agents/skills/contexit/`.

### Claude Code

```
/plugin marketplace add gitlakshya/contexit
/plugin install contexit@contexit
```
Then `/contexit:wiki` is available in every project. Manual install (bare `/wiki`, no marketplace):
```bash
mkdir -p .claude/commands && cp commands/wiki.md .claude/commands/wiki.md
```

### GitHub Copilot CLI

```
copilot plugin marketplace add gitlakshya/contexit
copilot plugin install contexit@contexit
```
Or install just the skill: `copilot skill add gitlakshya/contexit:.agents/skills/contexit`.

### Codex / opencode / other AGENTS.md agents

```bash
mkdir -p ~/.agents/skills && cp -r .agents/skills/contexit ~/.agents/skills/   # global
# or: cp -r .agents/skills/contexit your-repo/.agents/skills/                 # per-project
```
Invoke with `$contexit` (Codex), the `skill` tool (opencode), or by asking to "update the contexit wiki".

## Usage

### Claude Code / Copilot CLI
```
/contexit:wiki           # auto: init if new, update if exists
/contexit:wiki init      # force full generation
/contexit:wiki update    # force surgical update
/contexit:wiki update focus on the new auth module
```

### Codex / opencode
Ask to "initialize" or "update" the contexit docs; extra wording rides along as an additional instruction.

### CLI (for git hooks / CI)
```bash
npm run init            # initialize wiki
npm run sync            # update wiki
npm run sync -- --instruction "focus on new auth module"
npm run configure-triggers -- push,merge
```

## Auto-run as a hook (other coding agents)

`hooks/context-gate.sh` is host-agnostic: it reproduces the wiki's no-op check in pure shell (zero tokens) and spawns the configured agent headlessly only when source actually changed. Wiring ships pre-configured:

| Host | Wiring | Runner |
|------|--------|--------|
| Claude Code | `hooks/hooks.json` (`Stop` event, bundled with the plugin) | `claude -p '/contexit:wiki update'` |
| Copilot CLI | same `hooks/hooks.json` (Copilot CLI reads the identical plugin hook path) | `copilot -p 'update the contexit wiki'` |
| Codex | `.codex/hooks.json` (`Stop` event) | `codex exec 'update the contexit wiki'` |

Set `contexit_AGENT=claude|codex|copilot` to pick the runner manually, e.g. when wiring the script into another host's hook system. The `contexit_HOOK=1` guard prevents the headless run from re-triggering its own hook.

## Generated pages

`quickstart.md` (≤40 lines) is always created as the entry point. Additional thematic pages are generated based on what the repo actually contains — up to 8 total, each ≤80 lines.

| Type | Lines | Content |
|------|-------|---------|
| `quickstart.md` | ≤40 | What it is, install, first run |
| `architecture.md` | ≤80 | How it works, patterns, data flow |
| `setup.md` | ≤80 | Prerequisites, install, config, run |
| `api.md` | ≤80 | Public API surface |
| `workflows.md` | ≤80 | Key operational flows |
| *(others)* | ≤80 | components, testing, deployment… |

Entry point: `.context/quickstart.md`

## User scope override

Create `.context/INSTRUCTIONS.md` to guide generation. This file is never auto-modified.

## Sync triggers

Configured in `.contexit.json`. Options: `commit`, `push` (default), `merge`, `ci`, `manual`.

```bash
npm run configure-triggers -- push,merge
```

Git hooks are auto-created in `.git/hooks/` and guarded by `hooks/context-gate.sh` to prevent no-op runs.

## How it works

1. Collects git evidence (status, HEAD, diff summary, recent commits)
2. **Snapshot** — `scripts/finalize.py .context --snapshot` hashes every existing page body
3. AI writes page content (body only, no frontmatter)
4. **Finalize** — `scripts/finalize.py .context` deterministically:
   - Backfills OKF frontmatter on any page missing it
   - Regenerates `index.md` for the wiki root and subdirectories
   - Annotates broken internal links
   - Compares body hashes: changed pages stamped with `generated: { by, at }`; unchanged pages keep their original provenance
   - Deletes transient `.contexit-run.json`
5. Writes `.context/.last-update.json` with HEAD + timestamp

Uses whatever LLM you have configured in your coding agent (Claude Code, Copilot CLI, Codex, opencode, …) — no API keys needed in contexIT itself.

## Repository layout

```
.claude-plugin/
  plugin.json         # Claude Code + Copilot CLI plugin manifest
  marketplace.json    # single-plugin marketplace (source: ./)
commands/
  wiki.md             # Claude Code / Copilot CLI slash command → /contexit:wiki
hooks/
  hooks.json          # Stop-hook wiring shared by Claude Code + Copilot CLI
  context-gate.sh     # shell no-op gate; dispatches to claude|codex|copilot
.codex/
  hooks.json          # Codex Stop-hook wiring → the same gate
.agents/skills/contexit/
  SKILL.md            # skill for Codex, opencode, and other AGENTS.md hosts
  scripts/finalize.py # byte-identical twin so a standalone skill install works
scripts/
  finalize.py         # canonical deterministic post-processor
src/
  index.ts            # CLI (init/update/sync/configure-triggers)
```

## Requirements

- Node.js ≥18
- Python 3 (for `scripts/finalize.py`)
- A coding agent installed for AI generation (Claude Code, Copilot CLI, or Codex); falls back to placeholder content without one
