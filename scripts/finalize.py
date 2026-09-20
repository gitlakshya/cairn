#!/usr/bin/env python3
"""Deterministic post-run pass over a generated .cairn/ directory.

Ported from openwiki-cc/scripts/openwiki-finalize.py (upstream: langchain-ai/openwiki v0.5.0).
Runs in two modes:

  --snapshot   Before AI writes: hash every page body, write .cairn-run.json
  (default)    After AI writes: fix frontmatter, regenerate index.md,
               track cited source evidence (Grounded-Claims-lite), validate
               Mermaid diagrams, annotate broken links, stamp provenance from
               hash diff, then delete .cairn-run.json.

Grounded-Claims-lite: pages may cite evidence inline as Markdown links with a
`repo://<path>#L<start>-L<end>` href. Each run this script backfills the page's
`sources` frontmatter field from those citations and compares cited-range
content hashes against `.cairn/.sources-state.json` (committed with the
wiki). A missing path, an out-of-bounds range, or a hash that no longer
matches gets an inline `cairn: stale evidence` comment for the agent to
resolve on the next run — deliberately one run behind, same as broken links.

Mermaid diagrams: every ```mermaid fence is checked with a lightweight,
zero-dependency validator (known diagram keyword, balanced brackets/quotes).
A fence that fails is degraded in place to a ```text fence with a leading
`cairn: mermaid validation failed` comment explaining why, so it renders as
readable text instead of a broken diagram until the agent repairs it.

Rules:
  1. Never fail — always exits 0.
  2. Be idempotent — running twice on unchanged files produces no diff.
"""

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import sys
import urllib.parse

RESERVED = {"index.md", "INSTRUCTIONS.md"}
GENERATED_FIELD = "cairn_generated"
FALLBACK_TYPE = "Reference"
STATE_FILENAME = ".cairn-run.json"
ACTOR = "cairn"


# ---------------------------------------------------------------------------
# Front matter helpers (ported verbatim from openwiki-finalize.py)
# ---------------------------------------------------------------------------

def split_frontmatter(text):
    if text.startswith("---\n"):
        nl, skip = "\n", 4
    elif text.startswith("---\r\n"):
        nl, skip = "\r\n", 5
    else:
        return None, text
    close = nl + "---" + nl
    end = text.find(close, skip)
    if end == -1:
        return None, text
    return text[skip:end + len(nl)], text[end + len(close):]


def parse_fields(fields_text):
    out = {}
    for line in (fields_text or "").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1] in (" ", "\t"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def derive_title(body):
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or None
    return None


FENCE = "`" * 3


def derive_description(body):
    in_fence = False
    for line in body.splitlines():
        s = line.strip()
        if s.startswith(FENCE):
            in_fence = not in_fence
            continue
        if in_fence or not s:
            continue
        if s[0] in "#-*>|":
            continue
        return s
    return None


def _escape(value):
    if value and (value[0] in "[{&*!|>%@`\"'" or ": " in value or value.endswith(":")):
        return '"%s"' % value.replace('"', '\\"')
    return value


def ensure_frontmatter(text, fallback_title):
    fields_text, body = split_frontmatter(text)
    if fields_text is not None:
        if parse_fields(fields_text).get("type"):
            return text
        nl = "\r\n" if fields_text.endswith("\r\n") else "\n"
        injected = "type: %s%s%s: true%s" % (FALLBACK_TYPE, nl, GENERATED_FIELD, nl)
        return "---" + nl + injected + fields_text + "---" + nl + body
    title = derive_title(body) or fallback_title
    description = derive_description(body)
    nl = "\r\n" if "\r\n" in body else "\n"
    lines = ["---", "type: %s" % FALLBACK_TYPE, "title: %s" % _escape(title)]
    if description:
        lines.append("description: %s" % _escape(description))
    lines += ["%s: true" % GENERATED_FIELD, "---", ""]
    return nl.join(lines) + nl + body.lstrip("\r\n")


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------

def read_text(path):
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()
    except (OSError, UnicodeDecodeError) as e:
        print("finalize: skipping %s (%s)" % (path, type(e).__name__))
        return None


def write_text(path, content):
    try:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return True
    except OSError as e:
        print("finalize: skipping write to %s (%s)" % (path, type(e).__name__))
        return False


def markdown_files(wiki):
    files = []
    for p in sorted(wiki.rglob("*.md")):
        if p.is_file() and not p.is_symlink():
            files.append(p)
    return sorted(files)


# ---------------------------------------------------------------------------
# Pass 1: frontmatter backfill
# ---------------------------------------------------------------------------

def pass_frontmatter(wiki):
    changed = []
    for path in markdown_files(wiki):
        if path.name in RESERVED:
            continue
        text = read_text(path)
        if text is None:
            continue
        updated = ensure_frontmatter(text, path.stem.replace("-", " ").title())
        if updated != text and write_text(path, updated):
            changed.append(str(path))
    return changed


# ---------------------------------------------------------------------------
# Pass: Grounded-Claims-lite source tracking
# ---------------------------------------------------------------------------

SOURCE_RE = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<href>repo://[^)\s]+)\)")
SOURCE_MARKER_PREFIX = "cairn: stale evidence"
_SOURCE_MARKER_LINE_RE = re.compile(r"^\s*<!--\s*%s[^\n]*?-->\r?$" % re.escape(SOURCE_MARKER_PREFIX))
SOURCES_STATE_FILENAME = ".sources-state.json"


def sources_state_path(wiki):
    return wiki / SOURCES_STATE_FILENAME


def _parse_source_uri(uri):
    rest = uri[len("repo://"):]
    path_part, _, frag = rest.partition("#")
    start = end = None
    if frag.startswith("L"):
        nums = frag[1:].split("-L")
        try:
            start = int(nums[0])
            end = int(nums[1]) if len(nums) > 1 else start
        except ValueError:
            start = end = None
    return path_part, start, end


def _hash_source_range(target, start, end):
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None
    if start is None:
        text = content
    else:
        lines = content.splitlines()
        if start < 1 or end < start or end > len(lines):
            return None
        text = "\n".join(lines[start - 1:end])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_sources(body):
    uris, seen, fence = [], set(), None
    for line in body.split("\n"):
        s = line.lstrip()
        if s.startswith("```") or s.startswith("~~~"):
            fence = None if fence else ("`" if s.startswith("`") else "~")
            continue
        if fence:
            continue
        for m in SOURCE_RE.finditer(line):
            uri = m.group("href")
            if uri not in seen:
                seen.add(uri)
                uris.append(uri)
    return uris


def _annotate_sources(body, annotations):
    if not annotations:
        return body
    out_lines, fence = [], None
    for line in body.split("\n"):
        s = line.lstrip()
        if s.startswith("```") or s.startswith("~~~"):
            fence = None if fence else ("`" if s.startswith("`") else "~")
            out_lines.append(line)
            continue
        out_lines.append(line)
        if fence:
            continue
        indent = line[: len(line) - len(line.lstrip())]
        for m in SOURCE_RE.finditer(line):
            reason = annotations.get(m.group("href"))
            if reason:
                out_lines.append("%s<!-- %s - %s - %s -->" % (indent, SOURCE_MARKER_PREFIX, m.group("href"), reason))
    return "\n".join(out_lines)


def _set_list_field(text, key, items):
    fields_text, _ = split_frontmatter(text)
    if fields_text is None:
        return text
    nl, lines, body = _split_fields(text)
    if items:
        line = "%s: [%s]" % (key, ", ".join(items))
        out = [line if _is_key_line(l, key) else l for l in lines]
        if not any(_is_key_line(l, key) for l in lines):
            out.append(line)
    else:
        out = [l for l in lines if not _is_key_line(l, key)]
    return "---" + nl + nl.join(out) + nl + "---" + nl + body


def pass_sources(wiki):
    repo_root = wiki.resolve().parent
    sp = sources_state_path(wiki)
    try:
        prior_state = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prior_state = {}

    new_state = {}
    changed = []
    for path in markdown_files(wiki):
        if path.name in RESERVED:
            continue
        text = read_text(path)
        if text is None:
            continue
        fields_text, body = split_frontmatter(text)
        uris = _extract_sources(body)
        rel = path.relative_to(wiki).as_posix()
        prior_hashes = prior_state.get(rel, {})
        page_hashes, annotations = {}, {}

        for uri in uris:
            path_part, start, end = _parse_source_uri(uri)
            target = (repo_root / path_part).resolve()
            if not target.exists() or target.is_dir():
                annotations[uri] = "source not found"
                continue
            digest = _hash_source_range(target, start, end)
            if digest is None:
                annotations[uri] = "line range out of bounds"
                continue
            page_hashes[uri] = digest
            prior = prior_hashes.get(uri)
            if prior is not None and prior != digest:
                annotations[uri] = "source changed since last verified"

        if page_hashes:
            new_state[rel] = page_hashes

        new_body, _ = _strip_markers(body, _SOURCE_MARKER_LINE_RE)
        new_body = _annotate_sources(new_body, annotations)
        nl = "\r\n" if fields_text and "\r\n" in fields_text else "\n"
        rebuilt = ("---" + nl + fields_text + "---" + nl + new_body) if fields_text is not None else new_body
        updated = _set_list_field(rebuilt, "sources", sorted(set(uris)))

        if updated != text and write_text(path, updated):
            changed.append(str(path))

    try:
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(new_state, f, indent=2, sort_keys=True)
            f.write("\n")
    except OSError as e:
        print("finalize: could not write sources state (%s)" % e)
    return changed


# ---------------------------------------------------------------------------
# Pass: Mermaid diagram validation and self-healing degrade
# ---------------------------------------------------------------------------

MERMAID_MARKER_PREFIX = "cairn: mermaid validation failed"
_MERMAID_BLOCK_RE = re.compile(r"(^[ \t]*```)(mermaid|text)([ \t]*\r?\n)(.*?)(^[ \t]*```[ \t]*\r?\n?)", re.S | re.M)
_MERMAID_KEYWORDS = (
    "graph", "flowchart", "sequenceDiagram", "classDiagram", "stateDiagram-v2",
    "stateDiagram", "erDiagram", "journey", "gantt", "pie", "mindmap",
    "timeline", "quadrantChart", "requirementDiagram", "gitGraph", "C4Context",
    "C4Container", "C4Component", "C4Dynamic", "sankey-beta", "block-beta",
    "xychart-beta", "packet-beta",
)


def _validate_mermaid(src):
    stripped = src.strip()
    if not stripped:
        return "empty diagram"
    first_line = stripped.splitlines()[0].strip()
    if not any(first_line == kw or first_line.startswith(kw + " ") or first_line.startswith(kw + ":")
               for kw in _MERMAID_KEYWORDS):
        return "unrecognized diagram type"
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack = []
    for ch in stripped:
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in pairs.values():
            if not stack or stack.pop() != ch:
                return "unbalanced brackets"
    if stack:
        return "unbalanced brackets"
    if stripped.count('"') % 2 != 0:
        return "unbalanced quotes"
    return None


def pass_mermaid(wiki):
    changed = []
    for path in markdown_files(wiki):
        if path.name in RESERVED:
            continue
        text = read_text(path)
        if text is None:
            continue
        fields_text, body = split_frontmatter(text)

        def _fix(m):
            fence, lang, nl_after, content, close = m.groups()
            if lang != "mermaid":
                return m.group(0)
            reason = _validate_mermaid(content)
            if reason is None:
                return m.group(0)
            comment = "%s: %s - degraded to text, repair on next update\n" % (MERMAID_MARKER_PREFIX, reason)
            return fence + "text" + nl_after + comment + content + close

        new_body = _MERMAID_BLOCK_RE.sub(_fix, body)
        if new_body == body:
            continue
        nl = "\r\n" if fields_text and "\r\n" in fields_text else "\n"
        updated = ("---" + nl + fields_text + "---" + nl + new_body) if fields_text is not None else new_body
        if updated != text and write_text(path, updated):
            changed.append(str(path))
    return changed


# ---------------------------------------------------------------------------
# Pass 2: index.md generation
# ---------------------------------------------------------------------------

ROOT_INDEX_FM = '---\nokf_version: "0.2"\n---\n\n'


def _label(path):
    try:
        _, body = split_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return path.stem.replace("-", " ").title()
    return derive_title(body) or path.stem.replace("-", " ").title()


def _encode(name):
    _unsafe = " ()"
    return "".join("%%%02X" % ord(c) if c in _unsafe else c for c in name)


def _escape_label(label):
    return label.replace("[", "\\[").replace("]", "\\]")


def _has_pages(directory):
    for p in directory.rglob("*.md"):
        if p.is_file() and p.name not in RESERVED:
            return True
    return False


def render_index(directory, wiki):
    entries = []
    try:
        children = sorted(directory.iterdir(), key=lambda p: p.name)
    except OSError:
        return ""
    for child in children:
        try:
            is_dir = child.is_dir() and not child.is_symlink()
        except OSError:
            continue
        if is_dir:
            if _has_pages(child):
                entries.append(("%s/index.md" % _encode(child.name),
                                child.name.replace("-", " ").title()))
        elif child.suffix == ".md" and child.name not in RESERVED:
            entries.append((_encode(child.name), _label(child)))

    is_root = directory.resolve() == wiki.resolve()
    title = "Cairn Wiki" if is_root else directory.name.replace("-", " ").title()
    lines = ["# %s" % title, ""]
    lines += ["- [%s](%s)" % (_escape_label(lbl), href)
              for href, lbl in sorted(entries)]
    body = "\n".join(lines) + "\n"
    return (ROOT_INDEX_FM + body) if is_root else body


def pass_indexes(wiki):
    changed = []
    for directory in sorted(wiki.rglob("*"), key=str):
        if not (directory.is_dir() and not directory.is_symlink()):
            continue
        if not _has_pages(directory):
            continue
        target = directory / "index.md"
        rendered = render_index(directory, wiki)
        existing = read_text(target) if target.exists() else None
        if existing != rendered and write_text(target, rendered):
            changed.append(str(target))
    # also update root index
    target = wiki / "index.md"
    rendered = render_index(wiki, wiki)
    existing = read_text(target) if target.exists() else None
    if existing != rendered and write_text(target, rendered):
        if str(target) not in changed:
            changed.append(str(target))
    return changed


# ---------------------------------------------------------------------------
# Pass 3: broken link annotation
# ---------------------------------------------------------------------------

MARKER_PREFIX = "cairn: broken internal link"
LINK_RE = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<href>[^)\s]+)\)")
ATX_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.M)
_MARKER_LINE_RE = re.compile(r"^\s*<!--\s*%s[^\n]*?-->\r?$" % re.escape(MARKER_PREFIX))


def _slugify(heading):
    text = heading.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text).strip("-")


def _headings(text):
    _, body = split_frontmatter(text)
    slugs, seen = [], {}
    for m in ATX_RE.finditer(body):
        slug = _slugify(m.group(2))
        seen[slug] = seen.get(slug, -1) + 1
        slugs.append(slug if seen[slug] == 0 else "%s-%d" % (slug, seen[slug]))
    return set(slugs)


def _strip_markers(body, marker_re):
    kept, had, in_fence = [], False, False
    for line in body.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        if not in_fence and marker_re.match(line):
            had = True
            continue
        kept.append(line)
    return "\n".join(kept), had


def pass_links(wiki):
    changed = []
    for path in markdown_files(wiki):
        original = read_text(path)
        if original is None:
            continue
        fm_text, orig_body = split_frontmatter(original)
        body, had_markers = _strip_markers(orig_body, _MARKER_LINE_RE)
        out_lines, fence_char, found = [], None, False

        for line in body.split("\n"):
            s = line.lstrip()
            if s.startswith("```") or s.startswith("~~~"):
                fence_char = None if fence_char else ("`" if s.startswith("`") else "~")
            out_lines.append(line)
            problems = []
            if fence_char is None:
                for m in LINK_RE.finditer(line):
                    href = m.group("href")
                    if href.startswith(("http://", "https://", "mailto:", "//", "#", "/", "repo://")):
                        continue
                    target_part, _, anchor = href.partition("#")
                    if not target_part:
                        continue
                    target = (path.parent / urllib.parse.unquote(target_part)).resolve()
                    if not target.exists():
                        problems.append((href, "target not found"))
                        continue
                    if anchor and target.suffix == ".md":
                        tt = read_text(target)
                        if tt and _slugify(anchor) not in _headings(tt):
                            problems.append((href, "heading anchor not found"))

            indent = line[: len(line) - len(line.lstrip())]
            for href, reason in problems:
                found = True
                out_lines.append("%s<!-- %s: %s - %s -->" % (indent, MARKER_PREFIX, href, reason))

        if not found and not had_markers:
            continue
        updated_body = "\n".join(out_lines)
        nl = "\r\n" if fm_text and "\r\n" in fm_text else "\n"
        updated = ("---" + nl + fm_text + "---" + nl + updated_body) if fm_text else updated_body
        if updated != original and write_text(path, updated):
            changed.append(str(path))
    return changed


# ---------------------------------------------------------------------------
# Pass 4: provenance stamping (body-hash driven)
# ---------------------------------------------------------------------------

def body_hash(text):
    _, body = split_frontmatter(text)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


_GEN_RE = re.compile(
    r"^\s*generated:\s*\{\s*by:\s*(?P<by>[^,}]+?)\s*"
    r"(?:,\s*at:\s*(?P<at>[^}]+?)\s*)?\}\s*$"
)


def _read_gen(text):
    fields_text, _ = split_frontmatter(text)
    if not fields_text:
        return None
    for line in fields_text.splitlines():
        m = _GEN_RE.match(line)
        if m:
            by = m.group("by").strip().strip("'\"")
            at = (m.group("at") or "").strip().strip("'\"") or None
            return {"by": by, "at": at} if at else {"by": by}
    return None


def _is_key_line(line, key):
    s = line.strip()
    return s == key or s.startswith(key + ":") or s.startswith(key + " :")


def _split_fields(text):
    fields_text, body = split_frontmatter(text)
    nl = "\r\n" if "\r\n" in fields_text else "\n"
    lines = fields_text.split(nl)
    if lines and lines[-1] == "":
        lines.pop()
    return nl, lines, body


def _set_gen(text, by, at):
    line = "generated: { by: %s }" % by if at is None else "generated: { by: %s, at: %s }" % (by, at)
    fields_text, body = split_frontmatter(text)
    if fields_text is None:
        return "---\n%s\n---\n\n%s" % (line, body.lstrip("\r\n"))
    nl, lines, body = _split_fields(text)
    out = [line if _is_key_line(l, "generated") else l for l in lines]
    if not any(_is_key_line(l, "generated") for l in lines):
        out.append(line)
    return "---" + nl + nl.join(out) + nl + "---" + nl + body


def _remove_field(text, key):
    fields_text, _ = split_frontmatter(text)
    if fields_text is None:
        return text
    nl, lines, body = _split_fields(text)
    out = [l for l in lines if not _is_key_line(l, key)]
    return "---" + nl + nl.join(out) + nl + "---" + nl + body


def _canonicalize(text):
    return re.sub(r"[\r\n]*\Z", "", text) + "\n"


def _repair_fm(text):
    fields_text, _ = split_frontmatter(text)
    if not fields_text:
        return text
    nl, lines, body = _split_fields(text)
    out = []
    for line in lines:
        key = line.split(":", 1)[0].strip() if ":" in line else None
        if key == "generated" and not _GEN_RE.match(line):
            continue
        if key in ("title", "description") and not line.split(":", 1)[1].strip():
            continue
        out.append(line)
    return "---" + nl + nl.join(out) + nl + "---" + nl + body


def state_path(wiki):
    return wiki.resolve().parent / STATE_FILENAME


def pass_provenance(wiki, actor, at):
    snapshot_path = state_path(wiki)
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        entries = {e["page"]: e for e in snapshot}
    except (OSError, ValueError, KeyError):
        entries = None

    changed = []
    for path in markdown_files(wiki):
        if path.name in RESERVED:
            continue
        text = read_text(path)
        if text is None:
            continue
        rel = path.relative_to(wiki).as_posix()
        prior = entries.get(rel) if entries else None
        body_changed = prior is None or prior.get("bodyHash") != body_hash(text)

        if body_changed:
            if split_frontmatter(text)[0] is None:
                text = ensure_frontmatter(text, path.stem.replace("-", " ").title())
            candidate = _canonicalize(_remove_field(_set_gen(text, actor, at), "timestamp"))
        else:
            # restore pre-run event (preserve original authorship for unchanged pages)
            current_gen = _read_gen(text)
            prior_gen = prior.get("generated") if prior else None
            if prior_gen is None:
                candidate = text if current_gen is None else _remove_field(text, "generated")
            elif (current_gen and current_gen.get("by") == prior_gen.get("by")
                  and current_gen.get("at") == prior_gen.get("at")):
                candidate = text
            else:
                candidate = _set_gen(text, prior_gen["by"], prior_gen.get("at"))

        updated = _repair_fm(candidate)
        if updated != text and write_text(path, updated):
            changed.append(str(path))
    return changed


# ---------------------------------------------------------------------------
# Snapshot mode (--snapshot): run BEFORE AI writes pages
# ---------------------------------------------------------------------------

def write_snapshot(wiki):
    pass_frontmatter(wiki)   # migrate frontmatter before hashing
    entries = []
    for path in markdown_files(wiki):
        if path.name in RESERVED:
            continue
        text = read_text(path)
        if text is None:
            continue
        entry = {"page": path.relative_to(wiki).as_posix(), "bodyHash": body_hash(text)}
        gen = _read_gen(text)
        if gen:
            entry["generated"] = gen
        entries.append(entry)
    entries.sort(key=lambda e: e["page"])
    try:
        sp = state_path(wiki)
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
            f.write("\n")
    except OSError as e:
        print("finalize: could not write snapshot (%s)" % e)
    return len(entries)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wiki", nargs="?", default=".cairn")
    ap.add_argument("--snapshot", action="store_true",
                    help="pre-run mode: hash existing pages, write state file")
    ap.add_argument("--actor", default=ACTOR,
                    help="producer name in generated events")
    args = ap.parse_args()

    wiki = pathlib.Path(args.wiki)
    if not wiki.is_dir():
        print("finalize: no such directory: %s (nothing to do)" % wiki)
        return 0

    if args.snapshot:
        n = write_snapshot(wiki)
        print("finalize: snapshot %d page(s)" % n)
        return 0

    actor = (args.actor or "").strip() or ACTOR
    at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    fm = pass_frontmatter(wiki)
    src = pass_sources(wiki)
    mer = pass_mermaid(wiki)
    idx = pass_indexes(wiki)
    lnk = pass_links(wiki)
    prov = pass_provenance(wiki, actor, at)

    sp = state_path(wiki)
    try:
        sp.unlink()
    except OSError:
        pass

    print("finalize: frontmatter %d, sources %d, mermaid %d, indexes %d, links %d, provenance %d"
          % (len(fm), len(src), len(mer), len(idx), len(lnk), len(prov)))
    for p in fm + src + mer + idx + lnk + prov:
        print("  + %s" % p)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("finalize: skipped after error: %r" % e)
        sys.exit(0)
