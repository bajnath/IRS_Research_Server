"""
Stage 2 Catalog & Knowledge-Graph Builder for IRS Forms, Instructions, and Publications.

Merges raw Stage 1 index files (current + optional prior-year index), deduplicates
multi-revision entries per product_number, infers structural relationships, applies
curated statutory/data-flow edges, and computes bidirectional reverse edges.

Usage:
    python3 scripts/build_catalog.py
    python3 scripts/build_catalog.py --prior data/staging/irs_prior_index.json
"""

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCHEDULE_OF_RE = re.compile(
    r"^Form\s+(.+?)\s*\((?:Schedule|Sch\.?)\s+([^)]+)\)\s*$",
    re.IGNORECASE,
)
INSTRUCTION_FOR_RE = re.compile(
    r"^Inst(?:ruction)?s?\s+(.+)$",
    re.IGNORECASE,
)
PUBLICATION_RE = re.compile(
    r"^Publ?(?:ication)?s?\s+([A-Z0-9-]+)",
    re.IGNORECASE,
)
# Broader than SCHEDULE_OF_RE (used for About URLs): also Spanish Anexo/Anejo and "FY" suffixes
SCHEDULE_PARENT_RE = re.compile(
    r"^Form\s+(.+?)\s*\((?:Schedules?|Sch\.?|Anexo|Anejo)\s+([^)]+)\)\s*(?:\(?FY\)?)?\s*$",
    re.IGNORECASE,
)
LIST_SPLIT_RE = re.compile(r"\s*,\s*(?:and\s+)?|\s+or\s+|\s+and\s+|\s*&\s*|\s*/\s*", re.IGNORECASE)
TITLE_FORMS_LIST_RE = re.compile(
    r"\bForms\s+((?:[0-9A-Z][\w-]*(?:\s*,\s*(?:and\s+)?|\s+and\s+|\s*&\s*)?)+)"
)

def _parse_mdy(date_str: str) -> tuple[str, str, str]:
    parts = date_str.split("/")
    return (parts[2], parts[0], parts[1]) if len(parts) == 3 else ("", "", date_str)


PROVENANCE_BASES = {
    "irs_naming": "Implied by the IRS's own product numbering (e.g. 'Form 1040 (Schedule 1)', 'Instruction 8949') or the forms listed in an IRS document title.",
    "irs_text": "Machine-extracted from IRS instruction PDF text; carries the verbatim sentence, page, and PDF URL.",
    "curated": "Entered by hand in data/rules/curated_edges.json.",
    "inferred": "Derived by a rule in this project (e.g. keyword tagging, scraper classification, inheritance); not stated by the IRS.",
}


def _prov(basis: str, rule: str | None = None, **extra) -> dict:
    assert basis in PROVENANCE_BASES, basis
    out = {"basis": basis}
    if rule:
        out["rule"] = rule
    out.update({k: v for k, v in extra.items() if v})
    return out


def _irs_text_source(cand: dict) -> dict:
    return {
        "document": cand.get("instruction"),
        "page": cand.get("page"),
        "pdf_url": cand.get("pdf_url"),
        "quote": cand.get("evidence", ""),
    }


def _new_node(prod: str, title: str, category: str, tags: list[str], about_url: str | None) -> dict:
    return {
        "product_number": prod,
        "title": title,
        "category": category,
        "tags": list(tags),
        "irs_about_url": about_url,
        "revisions": [],
        "relationships": {
            "attaches_to": [],
            "flows_into": [],
            "superseded_by": [],
            "split_from": [],
            "related_instructions": [],
            "related_publications": [],
            "referenced_forms": [],
        },
        "reverse_relationships": {
            "attachments": [],
            "incoming_flows": [],
            "superseded_forms": [],
            "split_children": [],
            "documented_by": [],
            "referenced_by": [],
        },
        # Provenance for string-valued relationships, keyed by relationship type then target
        "provenance": {"attaches_to": {}, "related_instructions": {}, "related_publications": {}},
        "tag_provenance": {},
    }


def compute_irs_about_url(product_number: str) -> str | None:
    """Derive the canonical irs.gov/forms-pubs/about-* landing page URL."""
    prod = product_number.strip()

    sched_match = SCHEDULE_OF_RE.match(prod)
    if sched_match:
        parent_raw = sched_match.group(1).split(",")[0].strip()
        sched_raw = sched_match.group(2).strip()
        parent_slug = re.sub(r"[^a-z0-9]+", "-", parent_raw.lower()).strip("-")
        sched_slug = re.sub(r"[^a-z0-9]+", "-", sched_raw.lower()).strip("-")
        return f"https://www.irs.gov/forms-pubs/about-schedule-{sched_slug}-form-{parent_slug}"

    form_match = re.match(r"^Form\s+([A-Z0-9-]+)\s*$", prod, re.IGNORECASE)
    if form_match:
        slug = re.sub(r"[^a-z0-9]+", "-", form_match.group(1).lower()).strip("-")
        return f"https://www.irs.gov/forms-pubs/about-form-{slug}"

    pub_match = PUBLICATION_RE.match(prod)
    if pub_match:
        slug = re.sub(r"[^a-z0-9]+", "-", pub_match.group(1).lower()).strip("-")
        return f"https://www.irs.gov/forms-pubs/about-publication-{slug}"

    return None


def normalize_parent_token(token: str, known_products: set[str]) -> str:
    """Normalize shorthand parent tokens like '990PF' -> 'Form 990-PF' if present in catalog."""
    cleaned = token.strip()
    if not cleaned.lower().startswith("form "):
        cleaned = f"Form {cleaned}"

    if cleaned in known_products:
        return cleaned

    # Handle missing hyphen before trailing letter suffix, e.g., Form 990PF -> Form 990-PF
    hyphenated = re.sub(r"^(Form\s+\d+)([A-Z]+)$", r"\1-\2", cleaned, flags=re.IGNORECASE)
    for candidate in (cleaned, hyphenated):
        for prod in known_products:
            if prod.lower() == candidate.lower():
                return prod
    return cleaned


def _split_list(text: str) -> list[str]:
    return [t.strip() for t in LIST_SPLIT_RE.split(text) if t and t.strip()]


def _expand_form_tokens(text: str) -> list[str]:
    """Split '1040-EZ & A' -> ['1040-EZ', '1040-A'] (bare suffixes reuse the first token's root)."""
    toks = _split_list(text)
    if not toks:
        return []
    root = toks[0].split("-")[0]
    return [
        f"{root}-{t}" if i and re.fullmatch(r"[A-Z]{1,3}", t, re.IGNORECASE) else t
        for i, t in enumerate(toks)
    ]


def resolve_instruction_targets(
    prod: str,
    title: str,
    nodes: dict[str, dict],
    lower_lookup: dict[str, str],
    sched_index: dict[tuple[str, str], list[str]],
) -> tuple[list[str], str]:
    """Return (forms/schedules an 'Instruction ...' product documents, how they were matched)."""
    inst_match = INSTRUCTION_FOR_RE.match(prod)
    if not inst_match:
        return [], ""
    suffix = inst_match.group(1).strip()
    known = set(nodes)
    targets: list[str] = []

    def add(p: str | None) -> None:
        if p and p in nodes and p not in targets:
            targets.append(p)

    whole = lower_lookup.get(f"form {suffix}".lower())
    if whole:
        return [whole], "IRS product number 'Instruction X' names Form X"

    sched_match = re.match(
        r"^(.+?)\s*\((?:Schedules?|Sch\.?|Anexo|Anejo)\s+([^)]+)\)\s*$", suffix, re.IGNORECASE
    )
    if sched_match:
        scheds = _split_list(sched_match.group(2))
        for base in _expand_form_tokens(sched_match.group(1)):
            parent = normalize_parent_token(base, known)
            for s in scheds:
                exact = lower_lookup.get(f"{parent} (Schedule {s})".lower())
                if exact:
                    add(exact)
                else:
                    for p in sched_index.get((parent.lower(), s.lower()), []):
                        add(p)
    else:
        for base in _expand_form_tokens(suffix):
            candidate = lower_lookup.get(normalize_parent_token(base, known).lower())
            if not candidate:
                # Drop qualifiers like "(Tax Tables)" or "(General Instruction.)"
                stripped = re.sub(r"\s*\([^)]*\)\s*$", "", base)
                candidate = lower_lookup.get(normalize_parent_token(stripped, known).lower())
            add(candidate)

    if not targets:
        # Fallback for general instructions, e.g. "(Forms 1096, 1097, 1098, 1099, ... and W-2G)"
        title_match = TITLE_FORMS_LIST_RE.search(title)
        if title_match:
            for tok in _split_list(title_match.group(1)):
                add(lower_lookup.get(f"form {tok}".lower()))
                for p in sorted(nodes):
                    if p.startswith(f"Form {tok}-") and nodes[p]["category"] == "information_return":
                        add(p)
            return targets, "forms listed in the IRS instruction title (form families expanded)"
    return targets, "IRS product number 'Instruction X' names the forms/schedules covered"


TITLE_KEYWORD_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:foreign|nonresident|expatriat|treaty|treaties|boycott|territor|possessions)", re.I), "international"),
    (re.compile(r"\b(?:gambling|wagering)\b", re.I), "gambling"),
    (re.compile(r"\b(?:withholding|backup\s+withholding)\b", re.I), "withholding"),
    (re.compile(r"\b(?:self-employment|self-employed)\b", re.I), "self-employment"),
    (re.compile(r"\b(?:payroll|employment\s+tax|social\s+security\s+and\s+medicare|futa|unemployment\s+tax|household\s+employ)", re.I), "employment-taxes"),
    (re.compile(r"\b(?:tips?|overtime)\b", re.I), "tips-overtime"),
    (re.compile(r"\b(?:pension|retirement|ira\b|401\(k\)|403\(b\)|annuit|employee\s+benefit\s+plan)", re.I), "retirement"),
    (re.compile(r"\b(?:estate\s+tax|estate\s+\(and|generation-skipping|gift\s+tax|gifts?\b)", re.I), "estate-gift"),
    (re.compile(r"\b(?:decedent|fiduciary|trusts?\b)", re.I), "trusts-estates"),
    (re.compile(r"\b(?:exempt\s+organization|501\(c\)|private\s+foundation|political\s+organization|charitable)\b", re.I), "exempt-organizations"),
    (re.compile(r"\b(?:excise\s+tax|fuel|heavy\s+highway\s+vehicle|environmental\s+tax|wagering)\b", re.I), "excise-tax"),
    (re.compile(r"\b(?:depreciation|amortization|section\s+179)\b", re.I), "depreciation"),
    (re.compile(r"\b(?:capital\s+gain|straddle|installment\s+sale|like-kind\s+exchange|digital\s+asset|virtual\s+currency|dividends?|opportunity\s+(?:zone|fund))", re.I), "investments"),
    (re.compile(r"\b(?:real\s+estate|mortgage|low-income\s+housing|residential\s+rental|farmland)\b", re.I), "real-estate"),
    (re.compile(r"\b(?:farm|farmers?|farming|agricultur)", re.I), "farming"),
    (re.compile(r"\b(?:partnership|partner's\s+share)\b", re.I), "partnerships"),
    (re.compile(r"\b(?:s\s+corporation|shareholder's\s+share)\b", re.I), "s-corporations"),
    (re.compile(r"(?<!\bS )\bcorporations?\b", re.I), "c-corporations"),
    (re.compile(r"\b(?:health\s+coverage|health\s+savings|health\s+insurance|archer\s+msa|affordable\s+care|premium\s+tax\s+credit|medical)\b", re.I), "healthcare"),
    (re.compile(r"\b(?:clean\s+energy|clean\s+vehicle|biofuel|renewable|solar|hydrogen|nuclear|energy\s+efficient)\b", re.I), "renewable-energy"),
    (re.compile(r"\b(?:education|scholarship|tuition|student\s+loan)\b", re.I), "education"),
    (re.compile(r"\bminimum\s+tax\b", re.I), "alternative-minimum-tax"),
    (re.compile(r"\bestimated\s+tax\b", re.I), "estimated-tax"),
    (re.compile(r"\b(?:extension\s+of\s+time|amended|e-file|electronic\s+filing|electronically)\b", re.I), "filing-procedures"),
    (re.compile(r"\bcredits?\b", re.I), "credits"),
]

# Describes the return itself, not its schedules/instructions, so never inherited
NON_INHERITED_TAGS = {"core-return"}

DEFAULT_TAG_REGISTRY = Path("data/rules/tag_registry.json")


def validate_rule_tags(rules: dict, registry: dict) -> None:
    """Fail the build if keyword rules or curated overrides use a tag missing from the registry."""
    topic = set(registry["topic_tags"])
    problems = [f"keyword rule tag {tag!r}" for _, tag in TITLE_KEYWORD_RULES if tag not in topic]
    problems += [f"NON_INHERITED_TAGS entry {tag!r}" for tag in NON_INHERITED_TAGS if tag not in topic]
    for prod, tags in rules.get("topic_tags", {}).items():
        problems += [f"curated topic_tags[{prod!r}] uses {t!r}" for t in tags if t not in topic]
    if problems:
        raise SystemExit("Tags missing from tag_registry.json:\n  " + "\n  ".join(sorted(set(problems))))


def validate_node_tags(nodes: dict[str, dict], registry: dict) -> None:
    """Fail the build if any catalog node carries a tag outside the registry (e.g. a new scraper audience tag)."""
    allowed = set(registry["audience_tags"]) | set(registry["topic_tags"])
    unknown: dict[str, list[str]] = defaultdict(list)
    for prod, node in nodes.items():
        for tag in node["tags"]:
            if tag not in allowed:
                unknown[tag].append(prod)
    if unknown:
        lines = [f"{tag!r} on {len(prods)} products, e.g. {prods[0]}" for tag, prods in sorted(unknown.items())]
        raise SystemExit("Catalog tags missing from tag_registry.json:\n  " + "\n  ".join(lines))


def populate_topic_tags(nodes: dict[str, dict], rules: dict, lower_lookup: dict[str, str]) -> None:
    """Merge topic tags into each node's existing ``tags`` list (in-place).

    The ``tags`` field is the single source of truth consumed by all tools and
    resources.  Audience tags (``individual``, ``business``, etc.) already
    present from Stage 1 are preserved; topic tags are appended and the whole
    list is de-duplicated and sorted.

    Three-tier resolution:
      Tier 1 — keyword inference from product number + title.
      Tier 2 — curated overrides from ``curated_edges.json`` (authoritative;
                replaces inferred tags for the listed form).
      Tier 3 — child schedules inherit parent form tags when still untagged.

    Instructions/Publications get no topic tags; the forms they cover are
    listed in ``reverse_relationships.documented_by``.
    """
    curated_tags: dict[str, list[str]] = {
        lower_lookup.get(k.lower(), k): v for k, v in rules.get("topic_tags", {}).items()
    }

    # Tier 1 & 2: build a _topic_tags scratch dict, then merge into node["tags"]
    scratch: dict[str, list[str]] = {}
    origin: dict[str, dict[str, dict]] = defaultdict(dict)
    for prod, node in nodes.items():
        inferred: set[str] = set()
        # Only infer from title on actual forms/schedules
        if node["category"] != "additional_information":
            text = f"{prod} {node['title']}"
            for pattern, tag in TITLE_KEYWORD_RULES:
                if pattern.search(text):
                    inferred.add(tag)

        if prod in curated_tags:
            # Curated list is authoritative for topic tags
            scratch[prod] = sorted(set(curated_tags[prod]))
            origin[prod] = {t: _prov("curated", "curated_edges.json topic_tags") for t in scratch[prod]}
        else:
            scratch[prod] = sorted(inferred)
            origin[prod] = {t: _prov("inferred", "title keyword rule") for t in scratch[prod]}

    # Tier 3: Child schedules inherit parent form topic tags if still untagged
    for prod, node in nodes.items():
        if not scratch[prod] and node["relationships"]["attaches_to"]:
            inherited: set[str] = set()
            for parent in node["relationships"]["attaches_to"]:
                if parent in nodes:
                    for t in scratch.get(parent, []):
                        if t not in NON_INHERITED_TAGS:
                            inherited.add(t)
                            origin[prod][t] = _prov("inferred", f"inherited from parent form {parent}")
            scratch[prod] = sorted(inherited)

    # Merge topic tags into node["tags"], preserving existing audience tags
    for prod, node in nodes.items():
        audience = [t for t in node["tags"] if t not in scratch[prod]]
        node["tags"] = sorted(set(audience + scratch[prod]))
        node["tag_provenance"].update(origin[prod])

def source_scrape_time(current_index: Path) -> dict:
    """When the current IRS index was scraped; older staging files fall back to file mtime."""
    payload = json.loads(current_index.read_text(encoding="utf-8"))
    if payload.get("scraped_at"):
        basis = payload.get("scraped_at_basis", "scraper timestamp")
        return {"source_scraped_at": payload["scraped_at"], "source_scraped_at_basis": basis}
    mtime = datetime.fromtimestamp(current_index.stat().st_mtime, timezone.utc)
    return {
        "source_scraped_at": mtime.isoformat(timespec="seconds"),
        "source_scraped_at_basis": "staging file modification time (estimate; re-run irs-scrape to record it)",
    }


def load_staging_records(paths: list[Path]) -> list[tuple[str, dict]]:
    """Load (category, record) pairs from one or more Stage 1 staging JSON files."""
    rows: list[tuple[str, dict]] = []
    for path in paths:
        if not path or not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for category, items in payload.get("documents", {}).items():
            for record in items:
                rows.append((category, record))
    return rows


def build_catalog(
    staging_paths: list[Path],
    rules_path: Path,
    candidates_path: Path | None = None,
    registry_path: Path = DEFAULT_TAG_REGISTRY,
) -> dict:
    raw_rows = load_staging_records(staging_paths)
    rules = json.loads(rules_path.read_text(encoding="utf-8")) if rules_path.exists() else {}
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    validate_rule_tags(rules, registry)
    candidates = (
        json.loads(candidates_path.read_text(encoding="utf-8")).get("edges", [])
        if candidates_path and candidates_path.exists()
        else []
    )

    # 1. Merge multi-revision rows into canonical nodes keyed by product_number
    nodes: dict[str, dict] = {}
    for category, rec in raw_rows:
        prod = rec["product_number"].strip()
        if prod not in nodes:
            nodes[prod] = _new_node(
                prod, rec["title"], category, rec.get("tags", []), compute_irs_about_url(prod)
            )
            nodes[prod]["provenance"]["category"] = _prov(
                "inferred", "scraper title/number classification (scripts/scrape_catalog.py)"
            )
            for tag in nodes[prod]["tags"]:
                nodes[prod]["tag_provenance"][tag] = _prov("inferred", "scraper audience classification")

        rev_entry = {
            "revision_date": rec.get("revision_date", ""),
            "posted_date": rec.get("posted_date", ""),
            "pdf_url": rec.get("pdf_url"),
            "index_source": rec.get("index_source", ""),
        }
        if rev_entry not in nodes[prod]["revisions"]:
            nodes[prod]["revisions"].append(rev_entry)

    # Sort revisions newest-first
    for node in nodes.values():
        node["revisions"].sort(
            key=lambda r: (_parse_mdy(r.get("posted_date", "")), r.get("revision_date", "")),
            reverse=True,
        )

    # Products with no PDF in the IRS index (e.g. online-only filings like Form 990-N)
    for manual in rules.get("manual_products", []):
        prod = manual["product_number"]
        if prod not in nodes:
            nodes[prod] = _new_node(
                prod,
                manual["title"],
                manual["category"],
                manual.get("tags", []),
                manual["irs_about_url"] if "irs_about_url" in manual else compute_irs_about_url(prod),
            )
            if manual.get("note"):
                nodes[prod]["note"] = manual["note"]
            nodes[prod]["revisions"] = list(manual.get("revisions", []))
            nodes[prod]["provenance"]["category"] = _prov("curated", "curated_edges.json manual_products")
            for tag in nodes[prod]["tags"]:
                nodes[prod]["tag_provenance"][tag] = _prov("curated", "curated_edges.json manual_products")

    known_products = set(nodes.keys())
    lower_lookup = {k.lower(): k for k in known_products}

    # 2. Automated structural edges: Schedule -> Parent Form & Instruction -> Form
    sched_index: dict[tuple[str, str], list[str]] = defaultdict(list)
    unresolved_parents: list[tuple[str, str]] = []
    for prod, node in nodes.items():
        sched_match = SCHEDULE_PARENT_RE.match(prod)
        if not sched_match:
            continue
        sched_id = sched_match.group(2).strip().lower()
        for part in _split_list(sched_match.group(1)):
            parent = normalize_parent_token(part, known_products)
            if parent not in nodes:
                unresolved_parents.append((prod, parent))
                continue
            sched_index[(parent.lower(), sched_id)].append(prod)
            if parent != prod and parent not in node["relationships"]["attaches_to"]:
                node["relationships"]["attaches_to"].append(parent)
                node["provenance"]["attaches_to"][parent] = _prov(
                    "irs_naming", "IRS product number 'Form X (Schedule Y)' names parent Form X"
                )

    unresolved_instructions: list[str] = []
    for prod, node in nodes.items():
        if not INSTRUCTION_FOR_RE.match(prod):
            continue
        targets, how = resolve_instruction_targets(prod, node["title"], nodes, lower_lookup, sched_index)
        if not targets:
            unresolved_instructions.append(prod)
        for target in targets:
            if prod not in nodes[target]["relationships"]["related_instructions"]:
                nodes[target]["relationships"]["related_instructions"].append(prod)
                nodes[target]["provenance"]["related_instructions"][prod] = _prov("irs_naming", how)

    if unresolved_parents:
        print(f"Schedules whose parent form is not in the catalog ({len(unresolved_parents)}):")
        for prod, parent in unresolved_parents:
            print(f"    {prod} -> {parent}")
    if unresolved_instructions:
        print(f"Instructions with no resolvable target ({len(unresolved_instructions)}):")
        for prod in unresolved_instructions:
            print(f"    {prod}")
    
    # Inherit parent form's main instruction when a schedule lacks its own standalone Inst PDF
    for prod, node in nodes.items():
        if not node["relationships"]["related_instructions"] and node["relationships"]["attaches_to"]:
            for parent in node["relationships"]["attaches_to"]:
                if parent in nodes:
                    parent_insts = nodes[parent]["relationships"]["related_instructions"]
                    main = [i for i in parent_insts if i.lower() == f"instruction {parent[5:]}".lower()]
                    for inst in main or parent_insts:
                        if inst not in node["relationships"]["related_instructions"]:
                            node["relationships"]["related_instructions"].append(inst)
                            node["provenance"]["related_instructions"][inst] = _prov(
                                "inferred", f"schedule has no standalone instructions; inherits {parent}'s"
                            )

    # Instruction-text evidence for any (source, target) pair, used to support curated edges
    evidence_by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for cand in candidates:
        if cand["relation"] != "mentions":
            evidence_by_pair[(cand["source"], cand["target"])].append(
                _prov("irs_text", f"instruction text implies '{cand['relation']}'", source=_irs_text_source(cand))
            )

    def curated_prov(edge: dict, src: str, tgt: str) -> dict:
        # Optional hand-entered "citation" ({document, page, pdf_url, quote}); "source" is the edge's source form
        return _prov(
            "curated",
            "curated_edges.json",
            source=edge.get("citation"),
            supporting_evidence=evidence_by_pair.get((src, tgt), [])[:3],
        )

    # 3. Apply curated edges (flows_into, superseded_by, split_from, related_publications)
    for edge in rules.get("flows_into", []):
        src = lower_lookup.get(edge["source"].lower())
        tgt = lower_lookup.get(edge["target"].lower(), edge["target"])
        if src:
            nodes[src]["relationships"]["flows_into"].append(
                {"target": tgt, "mechanism": edge.get("mechanism", ""), "provenance": curated_prov(edge, src, tgt)}
            )

    for edge in rules.get("superseded_by", []):
        src = lower_lookup.get(edge["source"].lower(), edge["source"])
        tgt = lower_lookup.get(edge["target"].lower(), edge["target"])
        if src not in nodes:
            nodes[src] = _new_node(
                src,
                f"{src} (Retired)",
                edge.get("category", "individual_return"),
                ["individual", "retired"],
                compute_irs_about_url(src),
            )
            nodes[src]["provenance"]["category"] = _prov("curated", "curated_edges.json superseded_by")
            nodes[src]["tag_provenance"] = {
                t: _prov("curated", "curated_edges.json superseded_by") for t in ("individual", "retired")
            }
            lower_lookup[src.lower()] = src
        nodes[src]["relationships"]["superseded_by"].append(
            {
                "target": tgt,
                "effective_tax_year": edge.get("effective_tax_year"),
                "note": edge.get("note", ""),
                "provenance": curated_prov(edge, src, tgt),
            }
        )

    for edge in rules.get("split_from", []):
        src = lower_lookup.get(edge["source"].lower())
        parent = lower_lookup.get(edge["parent"].lower(), edge["parent"])
        if src:
            nodes[src]["relationships"]["split_from"].append(
                {
                    "parent": parent,
                    "effective_tax_year": edge.get("effective_tax_year"),
                    "note": edge.get("note", ""),
                    "provenance": curated_prov(edge, src, parent),
                }
            )

    for form_key, pubs in rules.get("related_publications", {}).items():
        canonical_form = lower_lookup.get(form_key.lower())
        if not canonical_form:
            continue
        for pub in pubs:
            canonical_pub = lower_lookup.get(pub.lower(), pub)
            if canonical_pub not in nodes[canonical_form]["relationships"]["related_publications"]:
                nodes[canonical_form]["relationships"]["related_publications"].append(canonical_pub)
                nodes[canonical_form]["provenance"]["related_publications"][canonical_pub] = _prov(
                    "curated", "curated_edges.json related_publications"
                )

    # Machine-extracted candidates stay separate from curated edges; skip ones already curated
    curated_pairs = {
        (prod, edge if isinstance(edge, str) else edge["target"])
        for prod, node in nodes.items()
        for key in ("attaches_to", "flows_into")
        for edge in node["relationships"][key]
    }
    for cand in candidates:
        src, tgt = cand["source"], cand["target"]
        if src not in nodes or tgt not in nodes or src == tgt:
            continue
        if cand["relation"] != "mentions" and (src, tgt) in curated_pairs:
            continue
        nodes[src]["relationships"]["referenced_forms"].append(
            {
                "target": tgt,
                "relation": cand["relation"],
                "status": "candidate",
                "provenance": _prov("irs_text", "scripts/extract_references.py", source=_irs_text_source(cand)),
            }
        )

    # 4. Compute bidirectional reverse edges across the graph
    for prod, node in nodes.items():
        rels = node["relationships"]

        for parent in rels["attaches_to"]:
            if parent in nodes and prod not in nodes[parent]["reverse_relationships"]["attachments"]:
                nodes[parent]["reverse_relationships"]["attachments"].append(prod)

        for flow in rels["flows_into"]:
            tgt = flow["target"]
            if tgt in nodes:
                nodes[tgt]["reverse_relationships"]["incoming_flows"].append(
                    {"source": prod, "mechanism": flow.get("mechanism", ""), "provenance": flow["provenance"]}
                )

        for sup in rels["superseded_by"]:
            tgt = sup["target"]
            if tgt in nodes:
                nodes[tgt]["reverse_relationships"]["superseded_forms"].append(
                    {
                        "source": prod,
                        "effective_tax_year": sup.get("effective_tax_year"),
                        "note": sup.get("note", ""),
                        "provenance": sup["provenance"],
                    }
                )

        for spl in rels["split_from"]:
            parent = spl["parent"]
            if parent in nodes:
                nodes[parent]["reverse_relationships"]["split_children"].append(
                    {
                        "child": prod,
                        "effective_tax_year": spl.get("effective_tax_year"),
                        "note": spl.get("note", ""),
                        "provenance": spl["provenance"],
                    }
                )

        for inst in rels["related_instructions"]:
            if inst in nodes and prod not in nodes[inst]["reverse_relationships"]["documented_by"]:
                nodes[inst]["reverse_relationships"]["documented_by"].append(prod)

        for pub in rels["related_publications"]:
            if pub in nodes and prod not in nodes[pub]["reverse_relationships"]["documented_by"]:
                nodes[pub]["reverse_relationships"]["documented_by"].append(prod)

        for ref in rels["referenced_forms"]:
            nodes[ref["target"]]["reverse_relationships"]["referenced_by"].append(
                {**{k: v for k, v in ref.items() if k != "target"}, "source": prod}
            )
    
    populate_topic_tags(nodes, rules, lower_lookup)
    validate_node_tags(nodes, registry)

    # 5. Compute summary counts
    category_counts: dict[str, int] = defaultdict(int)
    edge_counts: dict[str, int] = defaultdict(int)
    for node in nodes.values():
        category_counts[node["category"]] += 1
        for edge_type, items in node["relationships"].items():
            edge_counts[edge_type] += len(items)

    return {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **source_scrape_time(staging_paths[0]),
        "total_unique_products": len(nodes),
        "category_counts": dict(category_counts),
        "edge_counts": dict(edge_counts),
        "provenance_bases": PROVENANCE_BASES,
        "tag_registry": registry,
        "documents": dict(sorted(nodes.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build enriched IRS catalog with typed edges.")
    parser.add_argument(
        "--current",
        type=Path,
        default=Path("data/staging/irs_raw_index.json"),
        help="Current Stage 1 staging JSON",
    )
    parser.add_argument(
        "--prior",
        type=Path,
        default=None,
        help="Optional prior-year Stage 1 staging JSON",
    )
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path("data/rules/curated_edges.json"),
        help="Declarative curated edges JSON",
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=Path("data/rules/candidate_edges.json"),
        help="Machine-extracted candidate edges (from scripts/extract_references.py)",
    )
    parser.add_argument(
        "--tags",
        type=Path,
        default=DEFAULT_TAG_REGISTRY,
        help="Authoritative tag registry JSON",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/catalog/irs_catalog.json"),
        help="Output enriched catalog JSON",
    )
    args = parser.parse_args()

    catalog = build_catalog([args.current, args.prior], args.rules, args.candidates, args.tags)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(catalog, indent=2), encoding="utf-8")

    print(f"Built catalog with {catalog['total_unique_products']} unique products -> {args.out}")
    print("Categories:", catalog["category_counts"])
    print("Edges:", catalog["edge_counts"])
    topic = set(catalog["tag_registry"]["topic_tags"])
    tagged = sum(1 for n in catalog["documents"].values() if topic.intersection(n.get("tags", [])))
    print(f"Topic-tagged products: {tagged} / {catalog['total_unique_products']}")


if __name__ == "__main__":
    main()