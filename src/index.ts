#!/usr/bin/env node

import * as fs from "fs";
import * as path from "path";
import { execSync, spawn } from "child_process";

type Trigger = "commit" | "push" | "merge" | "manual" | "ci";

interface Config {
  version: string;
  triggers: Trigger[];
  contextPath: string;
  initialized: boolean;
}

interface LastUpdate {
  timestamp: string;
  gitHead: string;
  mode: "init" | "update";
}

class Contexit {
  private readonly root: string;
  private readonly configPath: string;
  private config: Config;

  constructor(root = process.cwd()) {
    this.root = root;
    this.configPath = path.join(root, ".context/contexit.json");
    this.config = this.loadConfig();
  }

  private loadConfig(): Config {
    if (fs.existsSync(this.configPath)) {
      try {
        const existing = JSON.parse(fs.readFileSync(this.configPath, "utf-8"));
        return {
          version: existing.version || "3.0.0",
          triggers: existing.triggers || ["push"],
          contextPath: existing.contextPath || ".context",
          initialized: existing.initialized || false,
        };
      } catch {
        // fall through to default
      }
    }
    return { version: "3.0.0", triggers: ["push"], contextPath: ".context", initialized: false };
  }

  private saveConfig(): void {
    fs.writeFileSync(this.configPath, JSON.stringify(this.config, null, 2));
  }

  private contextDir(): string {
    return path.join(this.root, this.config.contextPath);
  }

  private lastUpdatePath(): string {
    return path.join(this.contextDir(), ".last-update.json");
  }

  private loadLastUpdate(): LastUpdate | null {
    try {
      return JSON.parse(fs.readFileSync(this.lastUpdatePath(), "utf-8"));
    } catch {
      return null;
    }
  }

  private saveLastUpdate(mode: "init" | "update"): void {
    const record: LastUpdate = {
      timestamp: new Date().toISOString(),
      gitHead: this.git("rev-parse HEAD") || "unknown",
      mode,
    };
    fs.mkdirSync(this.contextDir(), { recursive: true });
    fs.writeFileSync(this.lastUpdatePath(), JSON.stringify(record, null, 2));
  }

  private git(cmd: string): string {
    try {
      return execSync(`git ${cmd}`, {
        cwd: this.root,
        encoding: "utf-8",
        stdio: ["pipe", "pipe", "pipe"],
      }).trim();
    } catch {
      return "";
    }
  }

  // Run scripts/finalize.py — deterministic post-processor (no AI involved).
  // mode "snapshot": hash existing pages before AI writes (records baseline)
  // mode "finalize": fix frontmatter, rebuild index, annotate links, stamp provenance
  private runFinalize(mode: "snapshot" | "finalize"): void {
    const scriptPath = path.join(path.dirname(process.argv[1]), "..", "scripts", "finalize.py");
    if (!fs.existsSync(scriptPath)) return;
    try {
      const args = mode === "snapshot"
        ? `python3 "${scriptPath}" "${this.config.contextPath}" --snapshot`
        : `python3 "${scriptPath}" "${this.config.contextPath}"`;
      execSync(args, { cwd: this.root, encoding: "utf-8", stdio: ["pipe", "pipe", "pipe"] });
    } catch {
      // finalize always exits 0; ignore shell errors
    }
  }

  private collectGitEvidence(mode: "init" | "update"): string {
    const last = this.loadLastUpdate();
    const head = this.git("rev-parse --short HEAD");
    const branch = this.git("rev-parse --abbrev-ref HEAD");
    const status = this.git("status --short");
    const diffStat = this.git("diff --stat HEAD~1 HEAD") || "initial commit";

    let commits = "";
    if (mode === "init") {
      commits = this.git("log --oneline --name-only -20");
    } else if (last?.gitHead) {
      commits = this.git(`log --oneline --name-only ${last.gitHead}..HEAD`);
    }

    return [
      `branch: ${branch}  head: ${head}`,
      status ? `\nchanged:\n${status}` : "",
      `\ndiff summary:\n${diffStat}`,
      commits ? `\nrecent commits:\n${commits}` : "",
    ]
      .filter(Boolean)
      .join("\n");
  }

  private analyzeFiles(): string {
    const ignore = new Set([
      ".git", "node_modules", ".context", "dist", "build",
      "__pycache__", ".venv", ".env", "coverage", ".nyc_output",
    ]);
    const lines: string[] = [];

    const walk = (dir: string, depth = 0) => {
      if (depth > 2) return;
      try {
        for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
          if (ignore.has(e.name) || e.name.startsWith(".")) continue;
          const indent = "  ".repeat(depth);
          if (e.isDirectory()) {
            lines.push(`${indent}${e.name}/`);
            walk(path.join(dir, e.name), depth + 1);
          } else {
            lines.push(`${indent}${e.name}`);
          }
        }
      } catch {
        // skip unreadable
      }
    };
    walk(this.root);
    return lines.join("\n");
  }

  private readKey(file: string, maxLen = 1000): string {
    try {
      const c = fs.readFileSync(path.join(this.root, file), "utf-8");
      return c.length > maxLen ? c.slice(0, maxLen) + "\n...[truncated]" : c;
    } catch {
      return "";
    }
  }

  private getInstructions(): string {
    try {
      return fs.readFileSync(
        path.join(this.contextDir(), "INSTRUCTIONS.md"),
        "utf-8"
      );
    } catch {
      return "";
    }
  }

  private buildPrompt(mode: "init" | "update", instruction?: string): string {
    const now = new Date().toISOString();
    const head = this.git("rev-parse --short HEAD") || "unknown";
    const gitEvidence = this.collectGitEvidence(mode);
    const fileTree = this.analyzeFiles();
    const readme = this.readKey("README.md", 1500);
    const pkg = this.readKey("package.json", 800);
    const pyproject = this.readKey("pyproject.toml", 500);
    const gomod = this.readKey("go.mod", 300);
    const instructions = this.getInstructions();

    const existingPages =
      mode === "update"
        ? fs
            .readdirSync(this.contextDir())
            .filter((f) => f.endsWith(".md") && f !== "INSTRUCTIONS.md")
            .join(", ")
        : "";

    return `Generate repository context wiki pages. Output ONLY valid JSON — no surrounding text, no code fences.

MODE: ${mode}${instruction ? ` | instruction: ${instruction}` : ""}
DATE: ${now}  HEAD: ${head}

GIT EVIDENCE:
${gitEvidence}

FILE TREE:
${fileTree}

${readme ? `README.md:\n${readme}\n` : ""}${pkg ? `package.json:\n${pkg}\n` : ""}${pyproject ? `pyproject.toml:\n${pyproject}\n` : ""}${gomod ? `go.mod:\n${gomod}\n` : ""}${instructions ? `USER INSTRUCTIONS:\n${instructions}\n` : ""}${existingPages ? `EXISTING PAGES (re-read them; only rewrite pages affected by the diff or instruction):\n${existingPages}\n` : ""}
DECIDE which pages this repo warrants. quickstart.md is always required.
Additional pages only if the repo actually has that content:
architecture, setup, components, api, workflows, contributing, testing, deployment, configuration.
Minimum 1 page, maximum 8.

OUTPUT FORMAT — pure JSON, keys = filenames, values = page content (body text only, no frontmatter):
{
  "quickstart.md": "# <Project Name>\\n\\n<what it does, 2-3 sentences>\\n\\n## Install\\n...",
  "<other>.md": "# <Title>\\n..."
}

Do NOT write YAML frontmatter — scripts/finalize.py adds it deterministically after this step.

HARD LINE LIMITS (count newlines per value before returning; trim to fit):
  quickstart.md  ≤ 40 lines  — what it is, install, one first-run command only
  all other pages ≤ 80 lines  — one tight topic per page, zero overlap between pages

Evidence-based: only what is verifiable in files/git. No fluff.`;
  }

  private async callClaude(prompt: string): Promise<Record<string, string>> {
    return new Promise((resolve) => {
      const proc = spawn("claude", ["-p", prompt], {
        cwd: this.root,
        stdio: ["pipe", "pipe", "pipe"],
        env: { ...process.env },
      });

      let out = "";
      let err = "";
      proc.stdout.on("data", (d: Buffer) => (out += d.toString()));
      proc.stderr.on("data", (d: Buffer) => (err += d.toString()));

      const timer = setTimeout(() => {
        proc.kill();
        console.warn("⚠️  Claude Code timed out after 120s.");
        resolve({});
      }, 120000);

      proc.on("close", (code) => {
        clearTimeout(timer);
        if (code !== 0) {
          console.warn(`⚠️  Claude exited ${code}: ${err.slice(0, 300)}`);
          resolve({});
          return;
        }
        // Extract JSON — handle code fence or bare object
        const cleaned = out
          .replace(/```json\n?/g, "")
          .replace(/```\n?/g, "")
          .trim();
        const match = cleaned.match(/\{[\s\S]*\}/);
        if (!match) {
          console.warn("⚠️  No JSON found in Claude output.");
          resolve({});
          return;
        }
        try {
          resolve(JSON.parse(match[0]));
        } catch (e) {
          console.warn("⚠️  JSON parse failed:", String(e).slice(0, 100));
          resolve({});
        }
      });
    });
  }

  private writePages(pages: Record<string, string>): string[] {
    const dir = this.contextDir();
    fs.mkdirSync(dir, { recursive: true });
    const written: string[] = [];
    for (const [name, content] of Object.entries(pages)) {
      if (!content?.trim()) continue;
      fs.writeFileSync(path.join(dir, name), content);
      written.push(`  ${name} (${content.split("\n").length} lines)`);
    }
    return written;
  }

  private writeFallbackDocs(): void {
    const now = new Date().toISOString();
    const head = this.git("rev-parse --short HEAD") || "unknown";
    const dir = this.contextDir();
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(
      path.join(dir, "quickstart.md"),
      `---
type: quickstart
title: Quick Start
description: Repository overview placeholder
git_hash: ${head}
last_updated: ${now}
---

# Repository

AI documentation not yet generated.

To generate: ensure Claude Code is installed, then run:
  npm run init
or type \`/contexit:wiki init\` in Claude Code.
`
    );
  }

  private writeBoundaryFiles(): void {
    const claudeMd = `# contexIT

Wiki in \`.context/\` — start at \`.context/quickstart.md\`.

## Agent Rules
- Read \`.context/quickstart.md\` before any repo exploration
- Prefer \`.context/\` docs over filesystem searches
- Refresh: \`/contexit:wiki update\` or \`npm run sync\`

## Config
Triggers: ${this.config.triggers.join(", ")} | Config: \`.contexit.json\`
`;
    const agentsMd = `# contexIT
Wiki: \`.context/\` (entry: quickstart.md)
Refresh: \`/contexit:wiki update\` or \`npm run sync\`
Config: \`.contexit.json\`
`;
    const cursorRules = `Read .context/quickstart.md for repo overview.
Refresh docs: /contexit:wiki update
`;

    for (const [file, content] of [
      ["CLAUDE.md", claudeMd],
      ["AGENTS.md", agentsMd],
      [".cursorrules", cursorRules],
    ] as [string, string][]) {
      const p = path.join(this.root, file);
      if (!fs.existsSync(p)) {
        fs.writeFileSync(p, content);
        console.log(`  created ${file}`);
      }
    }
  }

  private async setupGitHooks(): Promise<void> {
    const gitDir = path.join(this.root, ".git");
    if (!fs.existsSync(gitDir)) return;

    const hooksDir = path.join(gitDir, "hooks");
    fs.mkdirSync(hooksDir, { recursive: true });
    const toolPath = process.argv[1];

    const hookMap: Record<string, string> = {
      commit: "post-commit",
      push: "post-push",
      merge: "post-merge",
    };

    for (const trigger of this.config.triggers) {
      const hookName = hookMap[trigger];
      if (!hookName) continue;
      const hookPath = path.join(hooksDir, hookName);
      fs.writeFileSync(
        hookPath,
        `#!/bin/bash
# contexIT hook — guards against recursive invocation
[ "\${CONTEXIT_HOOK:-0}" = "1" ] && exit 0
CONTEXIT_HOOK=1 node "${toolPath}" --update
`
      );
      fs.chmodSync(hookPath, 0o755);
    }
  }

  async init(): Promise<void> {
    console.log("Initializing repository context...");
    fs.mkdirSync(this.contextDir(), { recursive: true });

    // Phase 1: snapshot existing pages before AI writes
    this.runFinalize("snapshot");

    const prompt = this.buildPrompt("init");
    console.log("Generating wiki via Claude Code...");
    const pages = await this.callClaude(prompt);

    if (Object.keys(pages).length === 0) {
      console.warn("No pages generated — Claude Code unavailable or misconfigured.");
      this.writeFallbackDocs();
    } else {
      const written = this.writePages(pages);
      written.forEach((l) => console.log(l));
    }

    // Phase 2: deterministic post-processing (frontmatter, index, links, provenance)
    this.runFinalize("finalize");

    this.saveLastUpdate("init");
    this.config.initialized = true;
    this.saveConfig();
    await this.setupGitHooks();
    this.writeBoundaryFiles();

    console.log(`\nDone. Context: ${this.config.contextPath}/quickstart.md`);
  }

  async update(instruction?: string): Promise<void> {
    if (!this.config.initialized) {
      console.error("Not initialized. Run: npm run init");
      process.exit(1);
    }

    const last = this.loadLastUpdate();
    const currentHead = this.git("rev-parse HEAD");

    // No-op: same HEAD, clean tree, no instruction
    if (!instruction && last?.gitHead === currentHead) {
      const status = this.git("status --short");
      if (!status) {
        console.log("No changes since last update. Skipping.");
        return;
      }
    }

    // Phase 1: snapshot before AI writes
    this.runFinalize("snapshot");

    const prompt = this.buildPrompt("update", instruction);
    console.log("Updating wiki via Claude Code...");
    const pages = await this.callClaude(prompt);

    if (Object.keys(pages).length === 0) {
      console.warn("Update failed — Claude Code unavailable.");
      return;
    }

    const written = this.writePages(pages);

    // Phase 2: deterministic post-processing
    this.runFinalize("finalize");

    if (written.length === 0) {
      console.log("No content changes.");
    } else {
      console.log("Wiki updated:");
      written.forEach((l) => console.log(l));
    }

    this.saveLastUpdate("update");
  }

  configureTriggers(triggers: Trigger[]): void {
    this.config.triggers = triggers;
    this.saveConfig();
    this.setupGitHooks();
    console.log(`Triggers set: ${triggers.join(", ")}`);
  }
}

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  const ctx = new Contexit();

  if (args.includes("--init")) {
    await ctx.init();
  } else if (args.includes("--update") || args.includes("--sync-docs")) {
    const instrIdx = args.indexOf("--instruction");
    const instruction = instrIdx > -1 ? args[instrIdx + 1] : undefined;
    await ctx.update(instruction);
  } else if (args.includes("--configure-triggers")) {
    const val = args[args.indexOf("--configure-triggers") + 1];
    if (val) ctx.configureTriggers(val.split(",") as Trigger[]);
    else console.error("Usage: --configure-triggers commit,push,merge");
  } else if (
    args.includes("--help") ||
    args.includes("-h") ||
    args.length === 0
  ) {
    console.log(`
contexIT v3.0.0 — AI-powered repository wiki

CLI:
  --init                              Initialize wiki (full generation)
  --update [--instruction "<text>"]   Surgical update (changed pages only)
  --configure-triggers <list>         Set triggers: commit,push,merge,ci,manual

Slash commands (in Claude Code):
  /contexit:wiki                       Auto-route: init if new, update if exists
  /contexit:wiki init                  Force full init
  /contexit:wiki update                Force update
  /contexit:wiki update <instruction>  Update with extra guidance

Generated pages (in .context/):
  quickstart.md   ≤40 lines  — entry point
  + thematic pages based on repo complexity (max 8 total)

Config: .contexit.json
`);
  } else {
    console.log('Unknown command. Run with --help.');
  }
}

main().catch((e) => {
  console.error("Fatal:", e instanceof Error ? e.message : String(e));
  process.exit(1);
});
