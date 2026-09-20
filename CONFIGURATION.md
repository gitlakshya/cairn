# Configuration

Config file: `.cairn/cairn.json` (auto-created by `npm run init`)

```json
{
  "version": "0.5.0",
  "triggers": ["push"],
  "contextPath": ".cairn",
  "initialized": true
}
```

## `triggers`

When to auto-regenerate. Valid values: `commit`, `push`, `merge`, `ci`, `manual`

| Value | Hook created | When it runs |
|-------|-------------|--------------|
| `commit` | `post-commit` | After `git commit` |
| `push` | `post-push` | After `git push` |
| `merge` | `post-merge` | After `git merge` |
| `ci` | none | CI/CD pipeline only |
| `manual` | none | `npm run sync` only |

```bash
npm run configure-triggers -- push,merge   # team default
npm run configure-triggers -- manual       # manual only
npm run configure-triggers -- ci           # CI/CD only
```

## `contextPath`

Directory for generated wiki. Default: `.cairn`. Changing after init requires manual migration.

## User scope override

Create `.cairn/INSTRUCTIONS.md` to direct generation:

```markdown
# Generation Instructions
Focus on the authentication and API modules.
Skip the legacy migration scripts.
Assume readers are senior engineers.
```

This file is never auto-modified.

## CI/CD (GitHub Actions)

Copy [`examples/cairn-update.yml`](examples/cairn-update.yml) into `.github/workflows/cairn-update.yml` (pin the checked-out ref and add the provider secret).

## What to commit

```
✅  .cairn/          generated wiki pages
✅  .cairn/cairn.json config (no secrets)
✅  CLAUDE.md, AGENTS.md, .cursorrules
❌  node_modules/, dist/
```
