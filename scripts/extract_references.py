"""
Extract candidate form-to-form references from IRS instruction PDFs.

For each "Instruction ..." product in the catalog, downloads the latest PDF
(cached under data/cache/inst_text/), splits the text into sentences, and
records every reference to another catalog form or schedule along with the
verbatim sentence as evidence.

Each reference is classified by the wording of its sentence:
  attaches_to  - "attach Form A to Form B" (A -> B), "Attach Form A" (A -> documented
                 form), or "attach this form to Form B" (documented form -> B)
  flows_into   - "from Form A ... on Form B" (A -> B), "enter ... on Form B, line N"
                 (documented form -> B), or "from Form A, line N" (A -> documented form)
  mentions     - any other reference
Negated sentences ("Do not attach ...") are recorded as mentions.

Output is written to data/rules/candidate_edges.json. These edges are
machine-extracted candidates: build_catalog.py loads them into a separate
``referenced_forms`` relationship so they never mix with curated edges.
Promote a reviewed edge by copying it into curated_edges.json.

Usage:
    python scripts/extract_references.py                     # all instructions
    python scripts/extract_references.py --only "Instruction 1040" "Instruction 8949"
    python scripts/extract_references.py --refresh           # ignore text cache
"""

import argparse
import hashlib
import io
import json
import logging
import re
import time
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

ALLOWED_PREFIX = "https://www.irs.gov/"
USER_AGENT = "IRS-Research-MCP/0.1 (catalog maintenance)"
# General instructions covering many forms would attribute every reference to all of them
MAX_COVERED_FORMS = 3

SCHED_OF_FORM_RE = re.compile(r"\bSchedules?\s+([A-Z0-9]{1,4}(?:-[A-Z0-9]{1,4})?)\s*\(Form\s+([0-9]{2,5}(?:-[A-Z0-9]+)*|W-\d[A-Z]*)\)")
FORM_RE = re.compile(r"\bForms?\s+((?:[0-9]{2,5}[A-Z]?(?:-[A-Z0-9]+)*|W-\d{1,2}[A-Z]*(?:-[A-Z]+)?|SS-\d+)(?:\s*\((?:PR|SP|SS)\))?)")
BARE_SCHED_RE = re.compile(r"\bSchedules?\s+([A-Z0-9]{1,4}(?:-[A-Z0-9]{1,4})?)\b(?!\s*\(Form)")
# Extra forms listed after the first: "Forms 1099-INT, 1099-OID, and 1099-DIV"
FORM_LIST_TAIL_RE = re.compile(r"(?:\s*,\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+)([0-9]{2,5}[A-Z]?(?:-[A-Z0-9]+)*|W-\d{1,2}[A-Z]*)")

NEGATION_RE = re.compile(r"\b(?:do\s+not|don['’]t|can['’]t|cannot|never|no\s+need\s+to)\b[^.;]{0,30}\battach", re.I)
ATTACH_WORD_RE = re.compile(r"\battach(?:ed)?\b", re.I)
ATTACH_SELF_TO_RE = re.compile(r"\battach\s+(?:it|this\s+(?:form|schedule)|the\s+(?:form|schedule))\s+to\b", re.I)
FLOW_VERB_RE = re.compile(r"\b(?:enter|report|include|carry|transfer|add)\b", re.I)
FLOW_NEGATION_RE = re.compile(r"\b(?:not|cannot|never|instead\s+of)\b|n['’]t\b", re.I)
LINE_AFTER_RE = re.compile(r"^\s*\)?\s*,?\s*(?:Part\s+[IVX]+\s*,?\s*)?lines?\s+\d", re.I)
LINE_BEFORE_RE = re.compile(r"\blines?\s+\w+\s+(?:of|on)\s+(?:your\s+|the\s+)?$", re.I)


def _cache_path(cache_dir: Path, url: str) -> Path:
    return cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")


def _pdf_pages(data: bytes) -> list[str]:
    try:
        import pymupdf

        doc = pymupdf.open(stream=data, filetype="pdf")
        return [page.get_text("text") for page in doc]
    except ImportError:
        from pypdf import PdfReader

        return [page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages]


def fetch_pages(url: str, cache_dir: Path, refresh: bool) -> list[str]:
    if not url.startswith(ALLOWED_PREFIX):
        raise ValueError(f"Blocked non-IRS URL: {url!r}")
    path = _cache_path(cache_dir, url)
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    pages = _pdf_pages(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pages), encoding="utf-8")
    time.sleep(0.5)  # be polite to irs.gov
    return pages


def sentences(page_text: str) -> list[str]:
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", page_text)  # rejoin hyphenated line breaks
    text = re.sub(r"\.{3,}", " ", text)  # worksheet dot leaders
    text = " ".join(text.split())
    parts = re.split(r"(?<=[.;:])\s+(?=[A-Z(•])|\s*•\s*", text)
    return [s.strip() for s in parts if s and s.strip()]


def find_refs(sentence: str, source_parent: str, lower_lookup: dict[str, str]) -> list[tuple[str, int, int]]:
    """Return (product_number, start, end) for each catalog form/schedule referenced in a sentence."""
    found: list[tuple[str, int, int]] = []
    spans: list[tuple[int, int]] = []

    def add(candidate: str, start: int, end: int) -> None:
        hit = lower_lookup.get(candidate.lower())
        if hit and all(hit != f[0] for f in found):
            found.append((hit, start, end))

    for m in SCHED_OF_FORM_RE.finditer(sentence):
        add(f"Form {m.group(2)} (Schedule {m.group(1)})", *m.span())
        spans.append(m.span())
    for m in FORM_RE.finditer(sentence):
        if any(a <= m.start() < b for a, b in spans):
            continue
        members = [m.group(1)]
        pos = m.end()
        while tail := FORM_LIST_TAIL_RE.match(sentence, pos):
            members.append(tail.group(1))
            pos = tail.end()
        # List members share the whole list's span so "from Forms A, B, line 3" applies to each
        for member in members:
            add(f"Form {member}", m.start(), pos)
    if source_parent:
        for m in BARE_SCHED_RE.finditer(sentence):
            if not any(a <= m.start() < b for a, b in spans):
                add(f"{source_parent} (Schedule {m.group(1)})", *m.span())
    return sorted(found, key=lambda f: f[1])


def classify(sentence: str, refs: list[tuple[str, int, int]], source: str) -> list[tuple[str, str, str]]:
    """Return (source, target, relation) edges implied by one sentence."""
    def before(word_re: re.Pattern, pos: int, window: int = 40) -> bool:
        return bool(word_re.search(sentence[max(0, pos - window):pos]))

    def near_before(word: str, pos: int) -> bool:
        return bool(re.search(rf"\b{word}\s+(?:line\s+\S+\s+of\s+)?(?:your\s+|the\s+)?$", sentence[max(0, pos - 30):pos], re.I))

    def line_adjacent(r: tuple[str, int, int]) -> bool:
        return bool(LINE_AFTER_RE.match(sentence[r[2]:r[2] + 25]) or LINE_BEFORE_RE.search(sentence[max(0, r[1] - 30):r[1]]))

    negated = bool(NEGATION_RE.search(sentence))
    flow_ok = bool(FLOW_VERB_RE.search(sentence)) and not FLOW_NEGATION_RE.search(sentence)
    from_refs = [r for r in refs if near_before("from", r[1])]
    on_refs = [r for r in refs if near_before("on", r[1]) and line_adjacent(r)]
    to_refs = [r for r in refs if near_before("to", r[1])]
    attach_refs = [r for r in refs if before(ATTACH_WORD_RE, r[1], 25)]
    edges: list[tuple[str, str, str]] = []
    used: set[str] = set()

    if not negated and ATTACH_WORD_RE.search(sentence):
        if ATTACH_SELF_TO_RE.search(sentence) and to_refs:
            edges += [(source, r[0], "attaches_to") for r in to_refs]
            used |= {r[0] for r in to_refs}
        elif attach_refs:
            parents = [r for r in to_refs if r[1] > attach_refs[0][1]]
            for child in attach_refs:
                for parent in parents or [(source, 0, 0)]:
                    if child[0] != parent[0]:
                        edges.append((child[0], parent[0], "attaches_to"))
                used.add(child[0])
            used |= {p[0] for p in parents}
    elif flow_ok:
        if from_refs and on_refs:
            for a in from_refs:
                for b in on_refs:
                    if a[0] != b[0]:
                        edges.append((a[0], b[0], "flows_into"))
            used |= {r[0] for r in from_refs + on_refs}
        elif on_refs:
            edges += [(source, r[0], "flows_into") for r in on_refs]
            used |= {r[0] for r in on_refs}
        elif from_refs := [r for r in from_refs if line_adjacent(r)]:
            edges += [(r[0], source, "flows_into") for r in from_refs]
            used |= {r[0] for r in from_refs}

    edges += [(source, r[0], "mentions") for r in refs if r[0] not in used]
    return [e for e in edges if e[0] != e[1]]


def primary_source(inst: str, node: dict, docs: dict, lower_lookup: dict[str, str]) -> str | None:
    covered = node["reverse_relationships"]["documented_by"]
    if not covered or len(covered) > MAX_COVERED_FORMS:
        return None
    direct = lower_lookup.get(("Form " + inst.split(" ", 1)[1]).lower())
    if direct in covered:
        return direct
    return covered[0] if len(covered) == 1 else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract candidate form references from IRS instruction PDFs.")
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog/irs_catalog.json"))
    parser.add_argument("--out", type=Path, default=Path("data/rules/candidate_edges.json"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache/inst_text"))
    parser.add_argument("--only", nargs="*", help="Limit to these instruction product numbers")
    parser.add_argument("--refresh", action="store_true", help="Re-download PDFs even if cached")
    args = parser.parse_args()
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # font-encoding warnings are noise here

    docs = json.loads(args.catalog.read_text(encoding="utf-8"))["documents"]
    lower_lookup = {k.lower(): k for k in docs}
    instructions = sorted(p for p in docs if p.startswith("Instruction "))
    if args.only:
        wanted = {o.lower() for o in args.only}
        instructions = [p for p in instructions if p.lower() in wanted]

    edges: dict[tuple[str, str, str], dict] = {}
    skipped: dict[str, list[str]] = defaultdict(list)

    for i, inst in enumerate(instructions, 1):
        node = docs[inst]
        source = primary_source(inst, node, docs, lower_lookup)
        if not source:
            skipped["no single documented form"].append(inst)
            continue
        url = next((r["pdf_url"] for r in node["revisions"] if r.get("pdf_url")), None)
        if not url:
            skipped["no PDF"].append(inst)
            continue
        try:
            pages = fetch_pages(url, args.cache_dir, args.refresh)
        except Exception as exc:  # network/PDF errors shouldn't stop the batch
            skipped[f"fetch failed: {type(exc).__name__}"].append(inst)
            continue

        parent_match = re.match(r"^(Form\s+.+?)(?:\s*\(Schedule .+\))?$", source)
        source_parent = parent_match.group(1) if parent_match else ""
        for page_no, page in enumerate(pages, 1):
            for sentence in sentences(page):
                refs = [r for r in find_refs(sentence, source_parent, lower_lookup)]
                if not refs:
                    continue
                for src, tgt, relation in classify(sentence, refs, source):
                    key = (src, tgt, relation)
                    if key in edges:
                        edges[key]["occurrences"] += 1
                        continue
                    edges[key] = {
                        "source": src,
                        "target": tgt,
                        "relation": relation,
                        "evidence": sentence[:400],
                        "instruction": inst,
                        "page": page_no,
                        "pdf_url": url,
                        "occurrences": 1,
                    }
        print(f"[{i}/{len(instructions)}] {inst}: {len(pages)} pages")

    if args.only and args.out.exists():
        # Merge: replace edges from re-processed instructions, keep the rest
        previous = json.loads(args.out.read_text(encoding="utf-8"))["edges"]
        redone = set(instructions)
        for e in previous:
            key = (e["source"], e["target"], e["relation"])
            if e["instruction"] not in redone and key not in edges:
                edges[key] = e

    ordered = sorted(edges.values(), key=lambda e: (e["source"], e["relation"], e["target"]))
    payload = {
        "generated_on": date.today().isoformat(),
        "status": "candidate",
        "note": "Machine-extracted from IRS instruction PDFs. Review before promoting to curated_edges.json.",
        "edges": ordered,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    by_rel = defaultdict(int)
    for e in ordered:
        by_rel[e["relation"]] += 1
    print(f"Wrote {len(ordered)} candidate edges -> {args.out}: {dict(by_rel)}")
    for reason, items in skipped.items():
        print(f"Skipped ({reason}): {len(items)}")


if __name__ == "__main__":
    main()
