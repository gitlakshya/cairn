# Cairn

AI-powered repository wiki that stays in sync with your code.

[MIT licensed](LICENSE) · [Changelog](CHANGELOG.md)

## What it does

Generates a compact wiki in `.cairn/` — thematic pages averaging ≤80 lines each, with OKF frontmatter, grounded in your actual files and git history. Updates surgically (only pages affected by recent changes). No-ops when nothing changed.

Material claims can cite exact evidence (`repo://path#L10-L42`); `scripts/finalize.py` tracks and hashes those citations and flags them when the cited source drifts (Grounded-Claims-lite). Diagrams are validated too — a broken Mermaid fence degrades to readable text with a repair note instead of shipping broken.

## Install

```bash
npm install && npm run build
```

Cairn is a self-contained **Claude Code plugin** — `.claude-plugin/` + `commands/wiki.md` + `hooks/` live at the repo root, so the same checkout also installs directly as a **Copilot CLI plugin** (it reads the same `.claude-plugin/plugin.json` / `marketplace.json`). Codex and other `AGENTS.md`-style agents pick up the twin skill under `.agents/skills/cairn/`.

### Claude Code

```
/plugin marketplace add gitlakshya/cairn
/plugin install cairn@cairn
```
Then `/cairn:wiki` is available in every project. Manual install (bare `/wiki`, no marketplace):
```bash
mkdir -p .claude/commands && cp commands/wiki.md .claude/commands/wiki.md
```

### GitHub Copilot CLI

```
copilot plugin marketplace add gitlakshya/cairn
copilot plugin install cairn@cairn
```
Or install just the skill: `copilot skill add gitlakshya/cairn:.agents/skills/cairn`.

### Codex / opencode / other AGENTS.md agents

```bash
mkdir -p ~/.agents/skills && cp -rL .agents/skills/cairn ~/.agents/skills/   # global
# or: cp -rL .agents/skills/cairn your-repo/.agents/skills/                 # per-project
```
`scripts/finalize.py` under the skill is a symlink into this checkout's own `scripts/`; `-L` dereferences it into a standalone copy so the finalizer still resolves once the skill is copied elsewhere. A plain `cp -r` reproduces the symlink literally and it will point outside the copy.

Invoke with `$cairn` (Codex), the `skill` tool (opencode), or by asking to "update the cairn wiki".

## Usage

### Claude Code / Copilot CLI
```
/cairn:wiki           # auto: init if new, update if exists
/cairn:wiki init      # force full generation
/cairn:wiki update    # force surgical update
/cairn:wiki update focus on the new auth module
```

### Codex / opencode
Ask to "initialize" or "update" the cairn docs; extra wording rides along as an additional instruction.

### CLI (for git hooks / CI)
```bash
npm run init            # initialize wiki
npm run sync            # update wiki
npm run sync -- --instruction "focus on new auth module"
npm run configure-triggers -- push,merge
```

## Auto-run as a hook (other coding agents)

`hooks/cairn-gate.sh` is host-agnostic: it reproduces the wiki's no-op check in pure shell (zero tokens) and spawns the configured agent headlessly only when source actually changed. Wiring ships pre-configured:

| Host | Wiring | Runner |
|------|--------|--------|
| Claude Code | `hooks/hooks.json` (`Stop` event, bundled with the plugin) | `claude -p '/cairn:wiki update'` |
| Copilot CLI | same `hooks/hooks.json` (Copilot CLI reads the identical plugin hook path) | `copilot -p 'update the cairn wiki'` |
| Codex | `.codex/hooks.json` (`Stop` event) | `codex exec 'update the cairn wiki'` |

Set `cairn_AGENT=claude|codex|copilot` to pick the runner manually, e.g. when wiring the script into another host's hook system. The `cairn_HOOK=1` guard prevents the headless run from re-triggering its own hook.

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

Entry point: `.cairn/quickstart.md`

## Agent boundary files

Every run of `scripts/finalize.py` upserts a small managed block into `CLAUDE.md`, `AGENTS.md`, and `.cursorrules` at the repo root, pointing agents at `.cairn/quickstart.md` before any other repo exploration:

```
<!-- cairn:start -->
...pointer text...
<!-- cairn:end -->
```

This is self-gating, not mode-gated: a file missing the block gets it created (or appended, if the file already exists with other content) — a file that already carries the block is left completely untouched, marker and all. So it's written once per file, automatically, with no `--init`-style flag or init/update coordination required. Delete the marked block by hand to have it re-bootstrapped on the next run.

## Grounded-Claims-lite

Pages may cite material claims inline as `[text](repo://path/to/file#L10-L42)` links. On every run, `scripts/finalize.py`:

- backfills the page's `sources` frontmatter from those citations (never hand-author this field);
- hashes each cited line range and compares it against `.cairn/.sources-state.json` (committed with the wiki);
- inserts a `<!-- cairn: stale evidence - ... -->` comment when a citation's target is missing, its line range no longer fits the file, or its content has changed since last verified.

The next run's agent finds that comment, re-verifies the claim against current source, and removes it — the same repair loop already used for broken internal links.

## Diagrams

Agents add ```mermaid fences (sequence, state, flow, ER) where they clarify a runtime flow, lifecycle, or data model better than prose, following the dedicated `skills/mermaid-diagrams/SKILL.md` skill (diagram-type selection, grounding discipline, Mermaid syntax-safety rules — adapted from `langchain-ai/openwiki`'s skill of the same name). `scripts/finalize.py` validates every fence with a lightweight, zero-dependency check (known diagram keyword, balanced brackets/quotes). A fence that fails is degraded in place to a ```text fence with a `cairn: mermaid validation failed` comment explaining why, so it renders as readable text instead of a broken diagram until an agent repairs it on the next run.

## User scope override

Create `.cairn/INSTRUCTIONS.md` to guide generation. This file is never auto-modified.

## Sync triggers

The first `init` automatically configures the `push` trigger and installs a Git `post-push` hook. Configuration is stored in `.cairn/cairn.json`. Options are `commit`, `push` (default), `merge`, `ci`, and `manual`.

Installing the plugin alone cannot modify a target repository's `.git/hooks/` directory. Run `init` once in each repository to create the default `post-push` hook.

```bash
npm run configure-triggers -- push,merge
```

Git hooks are auto-created in `.git/hooks/` and guarded by `hooks/cairn-gate.sh` to prevent no-op runs.

## Development

`scripts/finalize.py` is the deterministic core (frontmatter, sources, mermaid, links, provenance) and has a golden-fixture test suite in `tests/`:

```bash
python3 -m unittest discover -s tests -v
```

`.agents/skills/cairn/scripts/finalize.py` is a symlink to `scripts/finalize.py` — there is exactly one copy of the finalizer; do not hand-edit or re-create it as a separate file.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the test suite, plugin manifest validation, and a symlink check on every PR.


## How it works

1. Collects git evidence (status, HEAD, diff summary, recent commits)
2. **Snapshot** — `scripts/finalize.py .cairn --snapshot` hashes every existing page body
3. AI writes page content (body only, no frontmatter), citing material claims as `repo://` links and adding Mermaid diagrams where they clarify a flow
4. **Finalize** — `scripts/finalize.py .cairn` deterministically:
   - Backfills OKF frontmatter on any page missing it
   - Extracts `repo://` citations into each page's `sources` field, hashes cited ranges, and flags drifted/missing evidence (Grounded-Claims-lite)
   - Validates Mermaid fences and degrades broken ones to commented `text` fences
   - Regenerates `index.md` for the wiki root and subdirectories
   - Annotates broken internal links
   - Compares body hashes: changed pages stamped with `generated: { by, at }`; unchanged pages keep their original provenance
   - Deletes transient `.cairn-run.json`
5. Writes `.cairn/.last-update.json` with HEAD + timestamp

Uses whatever LLM you have configured in your coding agent (Claude Code, Copilot CLI, Codex, opencode, …) — no API keys needed in Cairn itself.

## Repository layout

```
.claude-plugin/
  plugin.json         # Claude Code + Copilot CLI plugin manifest
  marketplace.json    # single-plugin marketplace (source: ./)
commands/
  wiki.md             # Claude Code / Copilot CLI slash command → /cairn:wiki
hooks/
  hooks.json          # Stop-hook wiring shared by Claude Code + Copilot CLI
  cairn-gate.sh     # shell no-op gate; dispatches to claude|codex|copilot
.codex/
  hooks.json          # Codex Stop-hook wiring → the same gate
skills/mermaid-diagrams/
  SKILL.md            # diagram-type selection, discipline, syntax-safety rules
.agents/skills/cairn/
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
