---
description: Generate/maintain .cairn/ documentation wiki (init | update)
argument-hint: "[init|update] [extra instruction]"
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Task
---

# /cairn:wiki — Cairn documentation agent

Invoked as `/cairn:wiki` (auto-route), `/cairn:wiki init`, or `/cairn:wiki update`.
Adapted from OpenWiki (`langchain-ai/openwiki`). You are the agent; the repository is the target. Follow routing, collect git evidence, then act on the system prompt below.

## Routing — resolve mode from `$ARGUMENTS`

- `init` → **init mode**.
- `update` → **update mode**.
- empty → **auto-route**: if `.cairn/` exists → update, else → init.
  Run: `test -d .cairn && echo update || echo init`
- Anything else (e.g. `update focus on the new auth module`) → first token selects mode if it is `init`/`update`; remaining text is the **additional user instruction** appended to the run. If no mode token, auto-route and treat all `$ARGUMENTS` as additional instruction.

## Step 0 — Pre-run no-op check (update mode, no additional instruction only)

Mirrors OpenWiki `v0.5.0` `getUpdateNoopStatus`. Skip model work when nothing relevant changed. Applies **only** in update mode **and only when `$ARGUMENTS` carried no additional instruction**.

Read `.cairn/.last-update.json` if it exists, extract `gitHead`.

```bash
git --no-pager rev-parse HEAD
git --no-pager status --short --untracked-files=all
git --no-pager diff --name-only <gitHead>..HEAD   # only when HEAD != gitHead
```

Skip model work when **all** hold:
- `status --short` is empty after removing any line whose path is `.cairn/.last-update.json`;
- HEAD == `gitHead`, **or** every path in `<gitHead>..HEAD` is under `.cairn/`.

If skipped: refresh the run timestamp so freshness checks reflect the actual last run, report "wiki already current — no repository changes since `<gitHead>`", and stop.

```bash
python3 - <<'PY'
import datetime, json
p = ".cairn/.last-update.json"
d = json.load(open(p))
d["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
with open(p, "w", encoding="utf-8") as f:
    json.dump(d, f, indent=2)
    f.write("\n")
PY
```

(`python3` used instead of `jq` so the refresh works under the headless allowlist, which grants `Bash(python3:*)` but no `jq`.)

## Step 1 — Collect context (run BEFORE any write)

Read `.cairn/.last-update.json` if it exists: extract `gitHead`, `updatedAt`, `status`.

Run (all git invocations use `--no-pager`; git is read-only here):

```bash
git --no-pager rev-parse HEAD
git --no-pager status --short --untracked-files=all
git --no-pager diff --name-only <gitHead>..HEAD   # update: gitHead from metadata
```

On **update** with a known `gitHead`, also run:
```bash
git --no-pager log <gitHead>..HEAD --max-count=50 --oneline --name-status
```

On **init**, or update without prior metadata, run instead:
```bash
git --no-pager log --max-count=20 --name-status --oneline
```

The changed-paths list is the planner's **update window**: only pages whose systems intersect those paths (plus navigation and cross-page consistency) need work.

If this is not a git repository, degrade gracefully: use filesystem timestamps and source inspection to infer what changed.

Keep the assembled output as the **Git change summary** referenced by the planner in Step 3.

Check for ignore file:
```bash
test -f .contextignore && echo active || echo absent
```

- **absent** → standard discovery (targeted `glob`, `grep`, `rg --files` excluding `.git node_modules dist build`).
- **active** → read it, treat each pattern as off-limits for reading and documentation. Never document excluded paths or infer details about their contents.

## Step 2 — Prepare BEFORE wiki work (snapshot)

Find the finalize script. Run this probe and use the first path that responds:

```bash
for c in \
  "${CLAUDE_PLUGIN_ROOT:-}/scripts/finalize.py" \
  "scripts/finalize.py" \
  ".claude/skills/cairn/scripts/finalize.py" \
  ".agents/skills/cairn/scripts/finalize.py" \
  "$HOME/.claude/skills/cairn/scripts/finalize.py" \
  "$HOME/.agents/skills/cairn/scripts/finalize.py"; do
  [ -f "$c" ] && python3 "$c" --help 2>/dev/null | grep -q -- --snapshot && { echo "$c"; break; }
done
```

The loop stops at the first path that has a usable `--snapshot` flag. Order matters: plugin install (`CLAUDE_PLUGIN_ROOT`) wins if set; repository's own `scripts/` is preferred over hand-installed global copies. **Remember the path** — Step 3b uses it.

If the loop prints nothing, no usable finalizer is installed. Say so in the final message and skip Step 3b. Do not hand-write frontmatter, indexes, or provenance — that breaks idempotence. Do not fall back to a copy that rejects `--snapshot`.

Run snapshot mode:

```bash
python3 <the path from the previous command> .cairn --snapshot
```

This backfills OKF frontmatter on pages missing it and writes `.cairn-run.json` at the repo root: each concept page's body hash plus its prior `generated` event. The state file lives outside `.cairn/`, is consumed and deleted in Step 3b, and is overwritten by the next run — crash between steps is self-correcting.

## Step 3 — System prompt (act as agent)

Adapted from OpenWiki `v0.5.0` `src/agent/repository-prompts.ts` (`createRepositoryPlannerPrompt` + `createRepositoryPagePrompt`). Adaptations marked **[adapted]**:

(a) DeepAgents' virtual filesystem → native Read/Write/Edit/Glob/Grep/Bash on real repo paths;
(b) Upstream's durable page-job queue → plan held in orchestrator context plus one Task subagent per page (or sequential on hosts without Task);
(c) Upstream's prepare/finalize harness → Step 2's `--snapshot` and Step 3b;
(d) Upstream's full Claims subsystem (confirm/revise/retract lifecycle, per-claim worker sync) is out of scope — instead workers cite evidence inline as `repo://` links and `scripts/finalize.py` deterministically tracks/hashes/flags them (Grounded-Claims-lite, see Step 3b).

### Phase 1 — Planning

You are planning a context wiki for a repository.

**[adapted]** Output only the plan itself, held in orchestrator context: a structured page list with page path, title, purpose, seedPaths, relatedPages, and instructions. Do not write documentation pages in this phase.

Design the smallest complete repository-specific information architecture that helps a coding agent understand and safely change the system. Organize around owned systems, runtime domains, and cross-system workflows — not mirroring the source tree. Use hierarchical paths for meaningful groups: `.cairn/architecture/`, `.cairn/concepts/`, `.cairn/workflows/`, `.cairn/operations/`, `.cairn/integrations/`, `.cairn/testing/` — and top-level `.cairn/quickstart.md` covering end-to-end state, persistence, and one representative non-obvious file.

Explore only until major systems, behaviours, and relationships are supported by repository evidence. Avoid exhaustive file-by-file inventory.

Page paths are final once submitted. Populate `relatedPages` with the most useful conceptual or workflow neighbours so the resulting wiki is navigable across system boundaries. `quickstart.md` must route readers through the hierarchy; generated `index.md` files will provide folder navigation and must not be included in the plan.

**Init MUST include `.cairn/quickstart.md`.** Update MUST NOT delete quickstart. If an update adds, deletes, moves, or materially regroups documentation pages, include `.cairn/quickstart.md` in the plan so its task-routing map is refreshed. If an update has no required page edits or deletions, submit `pages: []`.

Every page: provide a concise purpose and useful seedPaths. seedPaths are starting points, not research boundaries. Copy only relevant global constraints into a page's instructions array; do not copy unrelated context into every job.

**[adapted]** `${semanticContext}` = additional user instruction from `$ARGUMENTS`, if any (else empty).

**[adapted]** (update mode only) `${updateContext}` = changed paths from Step 1, introduced as `Changed paths since <gitHead>:` followed by the `git diff --name-only` list.

**[adapted]** (upstream #699) When mode is `init` but `.cairn/` already exists, treat as fresh generation — existing pages are not preserved; workers rewrite everything.

**Page count and size constraints [adapted from openwiki-cc, our addition]:**
- Minimum 1 page (`.cairn/quickstart.md`), maximum 8 pages total.
- A focused single-purpose tool may need only 2–3 pages. A large monorepo may need 6–8. Match actual complexity.
- Plan page count and depth before writing. Depth of content, not width of pages.

Mode: use the mode resolved in Routing above.

### Phase 2 — Page workers

For each planned page, brief one worker by filling the `${...}` slots with the page's Phase 1 plan entry:

---

You own exactly `${job.path}`.
Title: `${job.title}`
Purpose: `${job.purpose}`
Mode: `${job.mode}`
Existing page: `${job.existing ? "yes — read it first" : "no"}`
Seed source paths: `${formatList(job.seedPaths)}`
Related pages: `${formatList(job.relatedPages)}`
Page-specific instructions: `${formatList(job.instructions)}`

`${job.mode === "update" ? "Read the current page first. Preserve accurate unaffected content; change only what current repository evidence requires. Keep code identifiers, file paths, commands, URLs, API names, and code blocks unchanged — translation would reduce technical accuracy." : ""}`

**[adapted — our line limits]** Hard line constraints for this page:
- `.cairn/quickstart.md` → **≤ 40 lines total** (including frontmatter). What it is (2–3 sentences), install, one first-run command. Nothing else.
- All other pages → **≤ 80 lines total** (including frontmatter). One tight topic. Zero overlap with sibling pages.

Count lines before writing. If the draft exceeds the limit, trim prose: cut redundant sentences, collapse lists, remove examples that do not add information. Never exceed the limit.

The page MUST begin with valid OKF concept frontmatter:

```yaml
---
type: <short descriptive concept type, e.g. quickstart | architecture | guide | reference | operations>
title: <human-readable page title>
description: <one or two sentence retrieval-oriented summary>
tags: [<stable English tag>, ...]
---
```

Do not author `generated`, `verified`, `sources`, `timestamp`, `git_hash`, `last_updated`, or any `cairn_*` control fields — `scripts/finalize.py` owns those.

On update, preserve unknown producer-defined frontmatter fields unless factually wrong.

Research deeply enough to explain: important responsibilities, entrypoints, mechanisms and control flow, relationships, state/lifecycle, invariants and failure modes, extension points, configuration and operations — focused on what actually matters for this topic. Follow evidence beyond seed paths through callers, callees, state owners, integration boundaries, and representative tests as required. Do not turn the page into a source-file inventory.

**[our addition] Grounded-Claims-lite citations.** For material factual claims (behavior, invariants, data flow, config, failure semantics — not obvious prose), cite the exact evidence inline as a Markdown link with a `repo://<path>#L<start>-L<end>` href, e.g. `the handler validates the token before dispatch ([source](repo://src/auth.ts#L40-L58))`. `scripts/finalize.py` deterministically extracts these into the page's `sources` frontmatter field and hashes each cited range; do not author `sources` yourself. Cite line ranges only when they materially ground a claim — do not cite every code reference.

**[our addition] Diagrams.** Before adding a diagram, read the mermaid-diagrams skill at `skills/mermaid-diagrams/SKILL.md` (repo root — adapted from `langchain-ai/openwiki`'s `skills/mermaid-diagrams/SKILL.md`) and follow its diagram-type selection, discipline, and syntax-safety rules. If that path does not exist, fall back to: add a ```mermaid fence only when it clarifies a runtime flow, lifecycle, or data model better than prose, ground it strictly in inspected source, and keep labels free of reserved Mermaid words and unescaped punctuation.

Write only `${job.path}`. Do not create, edit, or delete another wiki page.

`${ job.path === ".cairn/quickstart.md" ? "The complete planned page map is: " + JSON.stringify(allPages.map(({path, title, purpose}) => ({path, title, purpose})), null, 2) + " Use it to produce a compact task-routing map that links major domains." : "" }`

**[adapted]** If you find an HTML comment starting `"cairn: broken internal link"`, repair the href to restore the target page using the reason in the comment, then delete the comment.

**[our addition]** If you find an HTML comment starting `"cairn: stale evidence"`, re-verify the cited claim against current source, update the surrounding prose (and the `repo://` line range) to match, then delete the comment.

**[our addition]** If you find a ```text fence whose first line starts with `"cairn: mermaid validation failed"`, fix the diagram (or remove it if it no longer earns its place) and delete the comment line; restore the fence to ```mermaid once it is valid.

**[adapted]** Do not read secrets (`.env`, keys, credentials). Do not create or edit `AGENTS.md`, `CLAUDE.md`, or `.cursorrules` yourself — these are maintained separately.

---

**Dispatch:** For each page in the Phase 1 plan, launch one Task subagent briefed with the Phase 2 worker prompt for that page's plan entry. Launch independent pages together. On hosts without the Task tool, write pages yourself one at a time under the same worker discipline; the prompt text does not change.

## Step 3b — Finalize wiki (deterministic, run AFTER all wiki work)

Upstream logic lives in `src/agent/wiki-finalizer.ts`, `src/okf/generated-provenance.ts`, `src/okf/frontmatter.ts`, `src/okf/index-sync.ts`, `src/agent/wiki-link-validator.ts`. Here it runs as one script in finalize mode:

```bash
python3 <the Step 2 path> .cairn --actor <the model you are running as>
```

This regenerates directory `index.md` files (root carries `okf_version: "0.2"`), backfills each page's `sources` frontmatter from its `repo://` citations and flags any whose cited range no longer matches `.cairn/.sources-state.json` with an inline `cairn: stale evidence` comment (**[our addition]** Grounded-Claims-lite, a lighter-weight analogue of upstream's Claims subsystem), degrades unrecognized/malformed ```mermaid fences to commented ```text with a `cairn: mermaid validation failed` reason (**[our addition]**, mirrors upstream's default zero-dependency Mermaid check), annotates broken internal links, and reconciles generated provenance: pages whose body changed since the Step 2 snapshot are stamped `generated: { by: <actor>, at: <now> }` and lose any legacy `timestamp`; unchanged pages keep (or are restored to) their prior stamp. Deletes `.cairn-run.json`. Always exits 0. Never deletes content.

Run it AFTER all wiki work. It is idempotent: running it twice on unchanged files leaves every wiki file byte-identical.

## Step 4 — Persist metadata (run AFTER wiki work)

Write `.cairn/.last-update.json` exactly:

```json
{
  "updatedAt": "<current UTC time, ISO 8601, e.g. 2026-09-20T12:34:56.000Z>",
  "command": "init|update",
  "gitHead": "<output of git rev-parse HEAD, omit if not a git repo>",
  "model": "<the model you are running as, e.g. claude-opus-5>",
  "status": "complete"
}
```

Get `updatedAt` and `gitHead` via Bash (`date -u +%Y-%m-%dT%H:%M:%S.000Z`, `git rev-parse HEAD`) rather than guessing. Write this file on **every** completed run, including no-ops. Port always writes `complete`: an interrupted run leaves the previous file untouched so the next update re-runs on the dirty `.cairn/` tree — conservative equivalent to upstream's `interrupted` status.

---

## User prompt to act on

**init:**
> Initialize the Cairn wiki for this repository.
> Inspect the project thoroughly, identify major technical and business domains, and write initial documentation under `.cairn/`.
> Start with `.cairn/quickstart.md` as the entry point. Then create pages that explain the repository in a way useful to both humans and future agents.
>
> Git context: *(the Step 1 block)*

**update:**
> Update the existing Cairn wiki for this repository.
> Inspect `.cairn/`, identify recent source changes, and refresh only the documentation pages directly affected by those changes. Use git evidence below when available.
> Keep edits surgical: do not rewrite accurate sections, do not update source maps or git evidence just to refresh them, do not make formatting-only changes. If the wiki is already current, do not edit files.
> `.cairn/.last-update.json` is rewritten at the end of every run, including no-ops.
>
> Last update metadata: *(contents of `.cairn/.last-update.json`, or "No previous Cairn update metadata found.")*
> Git change summary: *(the Step 1 block)*

If `$ARGUMENTS` carried an additional instruction, append it as:
> Additional user instruction: *(that text)*

---

## Notes — headless / CI permissions (`claude -p`)

For non-interactive runs, grant this minimal allowlist in `.claude/settings.json`:

```json
{
  "permissions": {
    "allow": [
      "Bash(git --no-pager status:*)",
      "Bash(git --no-pager rev-parse:*)",
      "Bash(git --no-pager log:*)",
      "Bash(git --no-pager diff:*)",
      "Bash(git --no-pager show:*)",
      "Bash(python3:*)",
      "Bash(find:*)",
      "Bash(rg:*)",
      "Bash(date:*)",
      "Edit(.cairn/**)",
      "Write(.cairn/**)"
    ]
  }
}
```

`Bash(python3:*)` is broader than path-pinned entries — deliberate trade-off: the finalizer may sit at an absolute, version-dependent path under a plugin install that no static prefix can match. If that concerns you, pin both Step 2 and Step 3b to a known fixed path and replace `Bash(python3:*)` with `Bash(python3 scripts/finalize.py:*)`.

`AGENTS.md`, `CLAUDE.md`, and `.cursorrules` are deliberately absent from the allowlist: the worker prompt already forbids writing them; omitting permission adds a technical backstop. Do not add them back.
