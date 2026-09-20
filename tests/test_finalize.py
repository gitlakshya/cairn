#!/usr/bin/env python3
"""Golden-fixture tests for scripts/finalize.py.

Run with: python3 -m unittest discover -s tests -v
No third-party dependencies — stdlib unittest only, matching finalize.py itself.
"""

import importlib.util
import pathlib
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FINALIZE_PATH = REPO_ROOT / "scripts" / "finalize.py"

spec = importlib.util.spec_from_file_location("finalize", FINALIZE_PATH)
finalize = importlib.util.module_from_spec(spec)
sys.modules["finalize"] = finalize
spec.loader.exec_module(finalize)


class FinalizeTestCase(unittest.TestCase):
    """Each test gets its own throwaway repo root with a `.cairn/` wiki dir."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="cairn-test-"))
        self.wiki = self.tmp / ".cairn"
        self.wiki.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, content):
        p = self.wiki / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def write_source(self, rel, lines):
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p


class FrontmatterTests(FinalizeTestCase):
    def test_backfills_missing_frontmatter(self):
        self.write("quickstart.md", "# My Project\n\nDoes a thing.\n")
        finalize.pass_frontmatter(self.wiki)
        text = (self.wiki / "quickstart.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("type: Reference", text)
        self.assertIn("title: My Project", text)

    def test_injects_type_when_frontmatter_present_but_untyped(self):
        self.write("page.md", "---\ntitle: Existing\n---\n\nBody text.\n")
        finalize.pass_frontmatter(self.wiki)
        text = (self.wiki / "page.md").read_text(encoding="utf-8")
        self.assertIn("type: Reference", text)
        self.assertIn("title: Existing", text)  # preserved, not overwritten

    def test_leaves_valid_frontmatter_untouched(self):
        original = "---\ntype: quickstart\ntitle: Hi\n---\n\nBody.\n"
        self.write("page.md", original)
        finalize.pass_frontmatter(self.wiki)
        self.assertEqual((self.wiki / "page.md").read_text(encoding="utf-8"), original)

    def test_idempotent(self):
        self.write("page.md", "# Title\n\nSome content.\n")
        finalize.pass_frontmatter(self.wiki)
        first = (self.wiki / "page.md").read_text(encoding="utf-8")
        finalize.pass_frontmatter(self.wiki)
        second = (self.wiki / "page.md").read_text(encoding="utf-8")
        self.assertEqual(first, second)


class GroundedClaimsLiteTests(FinalizeTestCase):
    def test_valid_citation_gets_no_stale_annotation(self):
        self.write_source("src/auth.ts", ["line1", "line2", "line3", "line4"])
        self.write(
            "page.md",
            "---\ntype: guide\n---\n\n"
            "The handler validates tokens ([source](repo://src/auth.ts#L2-L3)).\n",
        )
        finalize.pass_sources(self.wiki)
        text = (self.wiki / "page.md").read_text(encoding="utf-8")
        self.assertNotIn("cairn: stale evidence", text)
        self.assertIn("sources: [repo://src/auth.ts#L2-L3]", text)

    def test_changed_source_gets_stale_annotation_next_run(self):
        src = self.write_source("src/auth.ts", ["line1", "line2", "line3"])
        self.write(
            "page.md",
            "---\ntype: guide\n---\n\nBehavior ([source](repo://src/auth.ts#L1-L2)).\n",
        )
        finalize.pass_sources(self.wiki)  # first run: records hash
        src.write_text("line1\nCHANGED\nline3\n", encoding="utf-8")
        finalize.pass_sources(self.wiki)  # second run: detects drift
        text = (self.wiki / "page.md").read_text(encoding="utf-8")
        self.assertIn("cairn: stale evidence", text)
        self.assertIn("source changed since last verified", text)

    def test_missing_source_is_annotated(self):
        self.write(
            "page.md",
            "---\ntype: guide\n---\n\nClaim ([source](repo://src/missing.ts#L1-L2)).\n",
        )
        finalize.pass_sources(self.wiki)
        text = (self.wiki / "page.md").read_text(encoding="utf-8")
        self.assertIn("cairn: stale evidence", text)
        self.assertIn("source not found", text)


class MermaidTests(FinalizeTestCase):
    def test_valid_diagram_untouched(self):
        original = "---\ntype: architecture\n---\n\n```mermaid\ngraph TD\nA-->B\n```\n"
        self.write("page.md", original)
        finalize.pass_mermaid(self.wiki)
        self.assertEqual((self.wiki / "page.md").read_text(encoding="utf-8"), original)

    def test_invalid_diagram_degrades_to_text(self):
        self.write(
            "page.md",
            "---\ntype: architecture\n---\n\n```mermaid\nnot a real diagram (((\n```\n",
        )
        finalize.pass_mermaid(self.wiki)
        text = (self.wiki / "page.md").read_text(encoding="utf-8")
        self.assertIn("```text", text)
        self.assertIn("cairn: mermaid validation failed", text)
        self.assertNotIn("```mermaid", text)


class LinkTests(FinalizeTestCase):
    def test_broken_relative_link_annotated(self):
        self.write("page.md", "---\ntype: guide\n---\n\nSee [other](missing.md).\n")
        finalize.pass_links(self.wiki)
        text = (self.wiki / "page.md").read_text(encoding="utf-8")
        self.assertIn("cairn: broken internal link", text)
        self.assertIn("target not found", text)

    def test_valid_relative_link_untouched(self):
        self.write("other.md", "# Other\n")
        original = "---\ntype: guide\n---\n\nSee [other](other.md).\n"
        self.write("page.md", original)
        finalize.pass_links(self.wiki)
        self.assertEqual((self.wiki / "page.md").read_text(encoding="utf-8"), original)


class ProvenanceTests(FinalizeTestCase):
    def test_new_page_gets_stamped(self):
        self.write("page.md", "---\ntype: guide\n---\n\nBody.\n")
        finalize.pass_provenance(self.wiki, "test-actor", "2026-01-01T00:00:00Z")
        text = (self.wiki / "page.md").read_text(encoding="utf-8")
        self.assertIn("generated: { by: test-actor, at: 2026-01-01T00:00:00Z }", text)

    def test_unchanged_page_keeps_prior_stamp_across_runs(self):
        self.write("page.md", "---\ntype: guide\n---\n\nBody.\n")
        finalize.pass_provenance(self.wiki, "actor-one", "2026-01-01T00:00:00Z")
        # Simulate a later run's snapshot recording this page's current body hash.
        finalize.write_snapshot(self.wiki)
        finalize.pass_provenance(self.wiki, "actor-two", "2026-02-02T00:00:00Z")
        text = (self.wiki / "page.md").read_text(encoding="utf-8")
        self.assertIn("generated: { by: actor-one, at: 2026-01-01T00:00:00Z }", text)

    def test_changed_body_restamps_with_new_actor(self):
        self.write("page.md", "---\ntype: guide\n---\n\nOriginal body.\n")
        finalize.write_snapshot(self.wiki)
        self.write("page.md", "---\ntype: guide\n---\n\nEdited body.\n")
        finalize.pass_provenance(self.wiki, "actor-two", "2026-02-02T00:00:00Z")
        text = (self.wiki / "page.md").read_text(encoding="utf-8")
        self.assertIn("generated: { by: actor-two, at: 2026-02-02T00:00:00Z }", text)


class IndexTests(FinalizeTestCase):
    def test_root_index_lists_pages(self):
        self.write("quickstart.md", "# Quickstart\n")
        self.write("architecture.md", "# Architecture\n")
        finalize.pass_indexes(self.wiki)
        text = (self.wiki / "index.md").read_text(encoding="utf-8")
        self.assertIn('okf_version: "0.2"', text)
        self.assertIn("architecture.md", text)
        self.assertIn("quickstart.md", text)


class BoundaryFilesTests(FinalizeTestCase):
    def test_creates_missing_boundary_files(self):
        finalize.pass_boundary_files(self.wiki)
        for name in ("CLAUDE.md", "AGENTS.md", ".cursorrules"):
            text = (self.tmp / name).read_text(encoding="utf-8")
            self.assertIn("<!-- cairn:start -->", text)
            self.assertIn(".cairn/quickstart.md", text)
            self.assertIn("<!-- cairn:end -->", text)

    def test_appends_to_existing_file_without_marker(self):
        (self.tmp / "AGENTS.md").write_text("# My repo\nCustom agent notes.\n", encoding="utf-8")
        finalize.pass_boundary_files(self.wiki)
        text = (self.tmp / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Custom agent notes.", text)
        self.assertIn("<!-- cairn:start -->", text)

    def test_leaves_file_untouched_once_marker_present(self):
        finalize.pass_boundary_files(self.wiki)
        (self.tmp / "AGENTS.md").write_text(
            "# Before\n<!-- cairn:start -->\nstale content\n<!-- cairn:end -->\n# After\n",
            encoding="utf-8",
        )
        finalize.pass_boundary_files(self.wiki)
        text = (self.tmp / "AGENTS.md").read_text(encoding="utf-8")
        # Self-gating: once the marker exists, the file is left completely alone —
        # even stale content between the markers is not refreshed automatically.
        self.assertEqual(
            text,
            "# Before\n<!-- cairn:start -->\nstale content\n<!-- cairn:end -->\n# After\n",
        )

    def test_idempotent(self):
        finalize.pass_boundary_files(self.wiki)
        first = (self.tmp / "CLAUDE.md").read_text(encoding="utf-8")
        finalize.pass_boundary_files(self.wiki)
        second = (self.tmp / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual(first, second)


class CLIBoundaryFilesAreSelfGatingTests(FinalizeTestCase):
    """No --init flag or caller-known mode exists: main() always calls pass_boundary_files,
    and the pass itself decides per-file whether there's anything to do based on the marker."""

    def _run_cli(self):
        import subprocess
        subprocess.run(
            [sys.executable, str(FINALIZE_PATH), str(self.wiki)],
            cwd=self.tmp, check=True, capture_output=True, text=True,
        )

    def test_first_run_bootstraps_boundary_files(self):
        self.write("quickstart.md", "# Quickstart\n")
        self._run_cli()
        for name in ("CLAUDE.md", "AGENTS.md", ".cursorrules"):
            text = (self.tmp / name).read_text(encoding="utf-8")
            self.assertIn("<!-- cairn:start -->", text)

    def test_later_run_never_touches_an_already_bootstrapped_file(self):
        self.write("quickstart.md", "# Quickstart\n")
        self._run_cli()
        # Simulate a repo owner editing the file after bootstrap (e.g. adding their
        # own notes above/below the block) — a later run must not revert or resync it.
        claude_md = self.tmp / "CLAUDE.md"
        claude_md.write_text(claude_md.read_text(encoding="utf-8") + "\nMy own notes.\n", encoding="utf-8")
        before = claude_md.read_text(encoding="utf-8")
        self._run_cli()
        after = claude_md.read_text(encoding="utf-8")
        self.assertEqual(before, after)


class FullRunIdempotenceTests(FinalizeTestCase):
    """The headline claim in scripts/finalize.py's docstring: run twice, no diff."""

    def _run_full(self, actor="cairn", at="2026-01-01T00:00:00Z"):
        finalize.pass_frontmatter(self.wiki)
        finalize.pass_sources(self.wiki)
        finalize.pass_mermaid(self.wiki)
        finalize.pass_indexes(self.wiki)
        finalize.pass_links(self.wiki)
        finalize.pass_provenance(self.wiki, actor, at)
        finalize.pass_boundary_files(self.wiki)

    def _snapshot_wiki(self):
        # All persisted state, not just *.md — a regression in .sources-state.json
        # (or any other sidecar) must fail this test just as loudly as a body diff.
        wiki_files = {
            p.relative_to(self.wiki).as_posix(): p.read_text(encoding="utf-8")
            for p in self.wiki.rglob("*")
            if p.is_file()
        }
        boundary_files = {
            name: (self.tmp / name).read_text(encoding="utf-8")
            for name in ("CLAUDE.md", "AGENTS.md", ".cursorrules")
            if (self.tmp / name).exists()
        }
        return {**wiki_files, **boundary_files}

    def test_second_run_is_a_no_op(self):
        self.write_source("src/app.ts", ["export const x = 1;"])
        self.write(
            "quickstart.md",
            "# App\n\nDoes a thing ([source](repo://src/app.ts#L1-L1)).\n\n"
            "```mermaid\ngraph TD\nA-->B\n```\n",
        )
        finalize.write_snapshot(self.wiki)
        self._run_full()
        first_pass = self._snapshot_wiki()

        finalize.write_snapshot(self.wiki)
        self._run_full()
        second_pass = self._snapshot_wiki()

        self.assertEqual(first_pass, second_pass)


if __name__ == "__main__":
    unittest.main()
