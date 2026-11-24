"""IRS Research MCP tools.

Five tools for professional research use (academics, journalists, law
practitioners, policy researchers). All data is sourced directly from IRS
publications and form documents. No tax advice is given; no eligibility or
legal interpretation is provided.

Tool inventory:
  1. lookup_form              — find a form by number, keyword, category, or topic tag
  2. browse_forms_by_topic    — enumerate all forms matching one or more topic tags
  3. get_form_history         — full revision history + IRS About-page notes
  4. trace_form_lineage       — data-flow graph, schedule attachments, supersedures
  5. compare_form_revisions   — line-by-line diff + verbatim What's New text
"""

from __future__ import annotations

from typing import Annotated, Literal, get_args

from fastmcp import FastMCP
from pydantic import Field

from irs_research_server.services.form_parser import (
    LIMITATIONS,
    compute_line_diff,
    quality_warnings,
    tax_year_of,
)
from irs_research_server.services.irs_history import (
    catalog_status,
    fetch_about_developments,
    get_inst_whats_new,
    get_parsed_form,
    load_catalog,
    resolve_product,
    tag_registry,
    unknown_tags,
)

# Valid document categories (mirrors scrape_catalog.py BUCKET_TAGS keys)
Category = Literal[
    "individual_return",
    "business_return",
    "information_return",
    "shared",
    "administrative_and_procedural",
    "additional_information",
    "unclassified",
]
VALID_CATEGORIES = frozenset(get_args(Category))

ProductNumber = Annotated[
    str, Field(min_length=1, max_length=200, description="IRS product number, e.g. 'Form 1040' or '1099-NEC'.")
]
TopicTags = Annotated[
    list[Annotated[str, Field(max_length=60)]],
    Field(max_length=10, description="Registered topic or audience tags (see irs://catalog/topic-tags)."),
]
RevisionQuery = Annotated[str, Field(min_length=1, max_length=50)]


def _match_revision(revisions: list[dict], rev_query: str) -> dict | None:
    """Match a revision by partial date string or URL fragment (case-insensitive)."""
    rq = rev_query.strip().lower()
    for rev in revisions:
        if rq in rev.get("revision_date", "").lower() or rq in (rev.get("pdf_url") or "").lower():
            return rev
    return None


def _covers(doc: dict) -> list[str] | None:
    """Forms/schedules an instruction or publication covers; None for other documents."""
    if not doc["product_number"].lower().startswith(("instruction", "publication")):
        return None
    return doc["reverse_relationships"]["documented_by"]


def _catalog_notice() -> dict | None:
    """Catalog freshness, included in revision-sensitive responses only when it is not fresh."""
    status = catalog_status()
    return None if status["status"] == "fresh" else status


def _unknown_tag_error(tags: list[str]) -> dict | None:
    """Error payload listing the valid vocabulary when a caller passes unregistered tags."""
    bad = unknown_tags(tags)
    if not bad:
        return None
    registry = tag_registry()
    return {
        "error": f"Unknown tag(s): {bad}. See the irs://catalog/topic-tags resource.",
        "valid_topic_tags": sorted(registry["topic_tags"]),
        "valid_audience_tags": sorted(registry["audience_tags"]),
        "results": [],
    }


def register(mcp: FastMCP) -> None:
    """Register all five IRS research tools with the MCP server."""

    # ─── Tool 1: lookup_form ─────────────────────────────────────────────────

    @mcp.tool(
        annotations={
            "title": "Look Up IRS Form",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    def lookup_form(
        query: Annotated[
            str, Field(max_length=200, description="Product number (e.g. 'Form 1099-NEC') or title keyword.")
        ],
        category: Annotated[Category | None, Field(description="Optional document category filter.")] = None,
        topic_tags: Annotated[
            TopicTags | None,
            Field(
                description="Optional tags to filter by (e.g. ['gambling', 'withholding']). "
                "All specified tags must be present on a form for it to match."
            ),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum number of search results.")] = 10,
    ) -> dict:
        """Look up an IRS form, instruction, or publication by product number or title keyword.

        Returns the full catalog node for an exact match, or a list of matching
        documents for a keyword/filter search. Each result includes the form's
        category, all audience and topic tags, latest revision date, and
        relationship counts. Instructions and publications also include
        ``covers``: the forms and schedules they document.

        This tool returns IRS-sourced structural facts only. It does not give
        tax advice or interpret filing obligations.
        """
        if topic_tags and (error := _unknown_tag_error(topic_tags)):
            return error

        # Exact product-number lookup — only bypass search when no filters are requested
        exact = resolve_product(query)
        if exact and not category and not topic_tags:
            result = {"match_type": "exact", "document": exact}
            covers = _covers(exact)
            if covers is not None:
                result["covers"] = covers
            return result

        q = query.strip().lower()
        docs = load_catalog()["documents"]

        results = []
        for d in docs.values():
            # Text match
            if q and q not in d["product_number"].lower() and q not in d["title"].lower():
                continue
            # Category filter
            if category and d["category"] != category:
                continue
            # Topic-tag filter — ALL requested tags must be present
            if topic_tags:
                form_tags = set(d.get("tags", []))
                if not all(t in form_tags for t in topic_tags):
                    continue
            entry = {
                "product_number": d["product_number"],
                "title": d["title"],
                "category": d["category"],
                "tags": d.get("tags", []),
                "latest_revision": d["revisions"][0] if d["revisions"] else None,
                "revision_count": len(d["revisions"]),
                "related_instructions": d["relationships"]["related_instructions"],
                "related_publications": d["relationships"]["related_publications"],
            }
            covers = _covers(d)
            if covers is not None:
                entry["covers"] = covers
            results.append(entry)

        return {
            "match_type": "search",
            "count": len(results),
            "results": results[:limit],
        }

    # ─── Tool 2: browse_forms_by_topic ───────────────────────────────────────

    @mcp.tool(
        annotations={
            "title": "Browse IRS Forms by Topic",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    def browse_forms_by_topic(
        topic_tags: Annotated[
            TopicTags,
            Field(
                min_length=1,
                description="One or more tags (e.g. ['gambling'] or ['retirement', 'credits']). Forms must "
                "carry ALL listed tags. See the irs://catalog/topic-tags resource for the vocabulary.",
            ),
        ],
        category: Annotated[Category | None, Field(description="Optional document category filter.")] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum number of results.")] = 50,
    ) -> dict:
        """Enumerate all IRS forms in the catalog that carry the specified topic tag(s).

        This is the primary research entry point for exploring the IRS form
        taxonomy by subject area. For example, querying topic_tags=['gambling']
        returns every form related to gambling income reporting.

        Each result includes the form's product number, title, category, full
        tag list, latest revision date, and links to related instructions and
        publications.

        Results are sourced from the pre-built IRS catalog. Topic tags are
        editorial metadata assigned by this project, not IRS classifications.
        This tool does not interpret legal obligations.
        """
        if not topic_tags:
            return {"error": "At least one topic tag is required.", "results": []}
        if error := _unknown_tag_error(topic_tags):
            return error

        docs = load_catalog()["documents"]
        tag_set = set(topic_tags)

        matches = []
        for d in docs.values():
            form_tags = set(d.get("tags", []))
            if not tag_set.issubset(form_tags):
                continue
            if category and d["category"] != category:
                continue
            matches.append(
                {
                    "product_number": d["product_number"],
                    "title": d["title"],
                    "category": d["category"],
                    "tags": d.get("tags", []),
                    "latest_revision": d["revisions"][0] if d["revisions"] else None,
                    "revision_count": len(d["revisions"]),
                    "related_instructions": d["relationships"]["related_instructions"],
                    "related_publications": d["relationships"]["related_publications"],
                    "irs_about_url": d.get("irs_about_url"),
                }
            )

        # Sort: individual_return first, then business_return, then others; alpha within each
        priority = {
            "individual_return": 0,
            "business_return": 1,
            "information_return": 2,
            "shared": 3,
        }
        matches.sort(key=lambda x: (priority.get(x["category"], 9), x["product_number"]))

        return {
            "query_tags": list(tag_set),
            "category_filter": category,
            "total_matches": len(matches),
            "results": matches[:limit],
        }

    # ─── Tool 3: get_form_history ────────────────────────────────────────────

    @mcp.tool(
        annotations={
            "title": "Get IRS Form History",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def get_form_history(
        product_number: ProductNumber,
        include_about_page: Annotated[
            bool,
            Field(
                description="If True, also fetch the IRS About-Form page for recent-developments notes "
                "(requires a live IRS.gov request; cached and refreshed monthly)."
            ),
        ] = True,
    ) -> dict:
        """Return the full indexed revision history of an IRS form, instruction, or publication.

        Includes every revision date and PDF URL recorded in the IRS index,
        a computed earliest_revision (approximate introduction date), and —
        if include_about_page is True — highlights from the IRS About-Form
        page (Recent Developments, Future Developments notices).

        All text is quoted verbatim from IRS sources. This tool does not
        interpret the significance of revisions or advise on filing requirements.
        """
        node = resolve_product(product_number)
        if not node:
            return {"error": f"Product not found: {product_number!r}"}

        revisions = node["revisions"]
        # Newest-first order is guaranteed by build_catalog.py; oldest is last.
        earliest = revisions[-1] if revisions else None

        about_data = None
        if include_about_page and node.get("irs_about_url"):
            try:
                about_data = await fetch_about_developments(node["irs_about_url"])
            except Exception as exc:
                about_data = {"about_url": node["irs_about_url"], "error": str(exc)}

        return {
            "product_number": node["product_number"],
            "title": node["title"],
            "category": node["category"],
            "tags": node.get("tags", []),
            "irs_about_url": node.get("irs_about_url"),
            "revision_count": len(revisions),
            "latest_revision": revisions[0] if revisions else None,
            "earliest_revision": earliest,
            "revisions": revisions,
            "about_page": about_data,
            "catalog_freshness": _catalog_notice(),
        }

    # ─── Tool 4: trace_form_lineage ──────────────────────────────────────────

    @mcp.tool(
        annotations={
            "title": "Trace IRS Form Lineage",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    def trace_form_lineage(
        product_number: ProductNumber,
        max_depth: Annotated[
            int,
            Field(ge=1, le=5, description="How many hops to follow in upstream and downstream data-flow graphs."),
        ] = 3,
    ) -> dict:
        """Traverse the data-flow graph, schedule attachments, and historical lineage of an IRS form.

        Returns:
          - attaches_to / attachments: schedule parent/child relationships
          - superseded_by / superseded_forms: retirement lineage with effective tax year
          - split_from / split_children: form split history (e.g. 1099-NEC from 1099-MISC)
          - downstream_flows: forms this form feeds data into, with IRS-sourced mechanism text
          - upstream_flows: forms that feed data into this form
          - candidate_references / candidate_referenced_by: machine-extracted references from
            IRS instruction PDFs (relation: attaches_to, flows_into, or mentions), each with the
            verbatim instruction sentence as evidence. These are unreviewed candidates and must
            be presented as such, separately from the curated edges above.

        Every edge carries ``provenance.basis``: irs_naming (implied by IRS product
        numbering), irs_text (extracted from IRS instruction text, with quote),
        curated (hand-entered), or inferred (a project rule). Report that basis
        alongside each relationship.
        This tool does not advise on which forms a user must file.
        """
        root = resolve_product(product_number)
        if not root:
            return {"error": f"Product not found: {product_number!r}"}

        docs = load_catalog()["documents"]

        def walk_flows(prod: str, direction: str, depth: int, seen: set[str]) -> list[dict]:
            if depth <= 0 or prod not in docs or prod in seen:
                return []
            seen.add(prod)
            node = docs[prod]
            edges = (
                node["relationships"]["flows_into"]
                if direction == "downstream"
                else node["reverse_relationships"]["incoming_flows"]
            )
            chain = []
            for edge in edges:
                nxt = edge["target"] if direction == "downstream" else edge["source"]
                chain.append(
                    {
                        "product_number": nxt,
                        "mechanism": edge.get("mechanism", ""),
                        "provenance": edge.get("provenance"),
                        "next": walk_flows(nxt, direction, depth - 1, seen.copy()),
                    }
                )
            return chain

        return {
            "product_number": root["product_number"],
            "title": root["title"],
            "category": root["category"],
            "tags": root.get("tags", []),
            "attaches_to": root["relationships"]["attaches_to"],
            "attaches_to_provenance": root.get("provenance", {}).get("attaches_to", {}),
            "attachments": root["reverse_relationships"]["attachments"],
            "superseded_by": root["relationships"]["superseded_by"],
            "superseded_forms": root["reverse_relationships"]["superseded_forms"],
            "split_from": root["relationships"]["split_from"],
            "split_children": root["reverse_relationships"]["split_children"],
            "downstream_flows": walk_flows(root["product_number"], "downstream", max_depth, set()),
            "upstream_flows": walk_flows(root["product_number"], "upstream", max_depth, set()),
            "candidate_references": root["relationships"].get("referenced_forms", []),
            "candidate_referenced_by": root["reverse_relationships"].get("referenced_by", []),
        }

    # ─── Tool 5: compare_form_revisions ──────────────────────────────────────

    @mcp.tool(
        annotations={
            "title": "Compare IRS Form Revisions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def compare_form_revisions(
        product_number: ProductNumber,
        rev_from: Annotated[
            RevisionQuery,
            Field(description="Partial revision date or year of the older revision, e.g. '2022' or 'Dec 2022'."),
        ],
        rev_to: Annotated[
            RevisionQuery,
            Field(description="Partial revision date or year of the newer revision, e.g. '2023' or 'Dec 2023'."),
        ],
    ) -> dict:
        """Compare two historical revisions of an IRS form line-by-line.

        Downloads (and caches) both form PDFs from IRS.gov, parses their text into
        Part/Line sections (scoped by copy on multi-copy information returns), and returns:
          - added / removed: sections present in only one revision
          - modified: sections whose text changed, with word-level change spans
          - year_only_changes: sections that differ only by the tax-year rollover
          - parse_quality, warnings, limitations: how reliable this comparison is
            (e.g. scanned PDFs without a text layer, duplicate line anchors)
          - whats_new_summary: verbatim 'What's New' / 'Future Developments' text
            extracted from the corresponding instruction PDF (if available)

        This is a text comparison of IRS source documents, not a legally
        authoritative form comparison. Always relay any warnings to the user.
        This tool does not interpret the legal significance of the changes.
        """
        node = resolve_product(product_number)
        if not node:
            return {"error": f"Product not found: {product_number!r}"}

        old_rev = _match_revision(node["revisions"], rev_from)
        new_rev = _match_revision(node["revisions"], rev_to)
        if not old_rev or not new_rev:
            return {
                "error": "Could not match the requested revisions.",
                "hint": "Use get_form_history to see the available revision dates for this form.",
                "available_revisions": [r["revision_date"] for r in node["revisions"]],
            }

        old_parse = await get_parsed_form(old_rev["pdf_url"])
        new_parse = await get_parsed_form(new_rev["pdf_url"])
        line_diff = compute_line_diff(
            old_parse["sections"],
            new_parse["sections"],
            tax_year_of(old_rev["revision_date"]),
            tax_year_of(new_rev["revision_date"]),
        )
        warnings = quality_warnings("from_revision", old_parse["quality"]) + quality_warnings(
            "to_revision", new_parse["quality"]
        )
        warnings += [p["cache_warning"] for p in (old_parse, new_parse) if p.get("cache_warning")]

        whats_new = None
        docs = load_catalog()["documents"]
        for inst_prod in node["relationships"]["related_instructions"]:
            inst_node = docs.get(inst_prod)
            if not inst_node:
                continue
            inst_rev = _match_revision(inst_node["revisions"], rev_to)
            if inst_rev and inst_rev.get("pdf_url"):
                try:
                    whats_new = {
                        "instruction": inst_prod,
                        "revision": inst_rev["revision_date"],
                        **await get_inst_whats_new(inst_rev["pdf_url"]),
                    }
                    break
                except Exception:
                    continue

        return {
            "product_number": node["product_number"],
            "title": node["title"],
            "from_revision": old_rev,
            "to_revision": new_rev,
            "warnings": warnings,
            "parse_quality": {"from_revision": old_parse["quality"], "to_revision": new_parse["quality"]},
            "limitations": LIMITATIONS,
            "catalog_freshness": _catalog_notice(),
            "whats_new_summary": whats_new,
            "line_diff": line_diff,
        }
