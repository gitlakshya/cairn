# contexIT

AI-powered repository wiki that stays in sync with your code.

## What it does

Generates a compact wiki in `.context/` — thematic pages averaging ≤80 lines each, with OKF frontmatter, grounded in your actual files and git history. Updates surgically (only pages affected by recent changes). No-ops when nothing changed.

## Install

```bash
npm install && npm run build
```

Add the slash command to Claude Code:
```bash
# project scope
mkdir -p .claude/commands && cp commands/wiki.md .claude/commands/contextit.md

# or global scope
mkdir -p ~/.claude/commands && cp commands/wiki.md ~/.claude/commands/contextit.md
```

## Usage

### Slash command (in Claude Code)
```
/contextit:wiki           # auto: init if new, update if exists
/contextit:wiki init      # force full generation
/contextit:wiki update    # force surgical update
/contextit:wiki update focus on the new auth module
```

### CLI (for git hooks / CI)
```bash
npm run init            # initialize wiki
npm run sync            # update wiki
npm run sync -- --instruction "focus on new auth module"
npm run configure-triggers -- push,merge
```

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

Configured in `.contextit.json`. Options: `commit`, `push` (default), `merge`, `ci`, `manual`.

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
   - Deletes transient `.contextit-run.json`
5. Writes `.context/.last-update.json` with HEAD + timestamp

Uses whatever LLM you have configured in Claude Code — no API keys needed in contexIT.

## Requirements

- Node.js ≥18
- Python 3 (for `scripts/finalize.py`)
- Claude Code installed (for AI generation; falls back to placeholder without it)
