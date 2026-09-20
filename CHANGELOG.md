# Changelog

All notable changes to Cairn are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## Versioning policy

Cairn is pre-1.0 (`0.x.y`) — per [SemVer §4](https://semver.org/#spec-item-4),
the public API (CLI flags, generated `.cairn/` file layout, OKF frontmatter
shape) may change in a **MINOR** bump, not only a MAJOR one. Concretely:

- **MINOR** (`0.x.0`): CLI flag/behavior changes, generated-file layout changes,
  OKF frontmatter field additions/removals — anything that could require a
  consumer to re-run `init` or adjust a CI recipe.
- **PATCH** (`0.x.y`): bug fixes, internal refactors, doc fixes — safe to
  upgrade without any consumer-side changes.

Once the CLI surface and OKF output are stable enough to commit to, Cairn will
cut `1.0.0` and switch to standard SemVer (breaking changes only on MAJOR).

## [0.5.0] — 2026-09-20

### Added
- Durable `.cairn/.run-lock.json`: guards `init()`/`update()` against
  overlapping runs and lets the next run detect and resume after a crash
  mid-run (stale lock from a dead pid warns and proceeds; a live pid blocks
  with a clear error).
- `--help` now documents the previously-undocumented `--sync-docs` alias for
  `--update`.
- `scripts/finalize.py` now deterministically upserts a managed
  `<!-- cairn:start -->...<!-- cairn:end -->` pointer block into `CLAUDE.md`,
  `AGENTS.md`, and `.cursorrules` at the repo root — directs agents to
  `.cairn/quickstart.md` first, even when those files already exist.
  Self-gating on the marker's presence rather than on init vs. update mode: a
  file that already carries the block is left completely untouched, so it's
  written once per file with no flag or caller-mode coordination needed.

### Changed
- The CLI's own `writeBoundaryFiles()` (which only created these files if
  absent) is removed; the finalizer is now the single source of truth shared
  by the CLI, Claude Code, and Codex routes. The Claude/Codex worker prompt no
  longer forbids touching `AGENTS.md`/`CLAUDE.md` — it explains they're
  maintained deterministically instead.

## [0.4.0] — 2026-09-20

### Added
- Golden-fixture `unittest` suite for `scripts/finalize.py` (`tests/`), run in CI.
- `LICENSE` (MIT).
- `examples/cairn-update.yml` — a self-contained CI recipe for a *target* repo:
  checks out the target repo, installs the Claude Code CLI, checks out and
  builds Cairn into `.cairn-tool/`, then runs `--update` against the target.

### Changed
- `.agents/skills/cairn/scripts/finalize.py` is now a symlink to
  `scripts/finalize.py` instead of a hand-synced duplicate.
- `init()` no longer persists `initialized: true` until generation and
  finalize both succeed, so an interrupted init can be retried.
- Standalone skill installs (`cp -rL`) now dereference the finalizer symlink
  instead of copying a broken relative link.

### Fixed
- Generated `CLAUDE.md`/`AGENTS.md` templates and `--help` text pointed at the
  stale `.cairn.json` path; corrected to `.cairn/cairn.json`.
- CI recipe previously assumed `npm run sync` and a pre-installed `claude`
  binary existed in the consuming repo, which is only ever true inside the
  Cairn repo itself.

## [3.1.0] — prior release

Repository renamed contexIT → Cairn; Claude Code + Codex plugin/skill wiring;
CI validation and release workflows added. See `git log v3.1.0` for detail —
this file starts tracking changes going forward from 0.4.0.
