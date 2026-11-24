# ruff: noqa: E501

"""IRS Research MCP resources.

Resources are static/semi-static data the MCP client can read directly
without calling a tool. They are ideal for reference material that the
LLM should load at the start of a session.

Resources registered here:
  irs://meta/usage-policy      — scope statement and guardrails (read at session start)
  irs://catalog/summary        — high-level catalog statistics
  irs://catalog/categories     — the 7 document categories with definitions
  irs://catalog/topic-tags     — full topic-tag vocabulary with representative forms
  irs://form/{product_number}  — complete catalog node for a specific form
"""

from __future__ import annotations

import json

from fastmcp import FastMCP

from irs_research_server.services.irs_history import catalog_status, load_catalog, resolve_product, tag_registry

# ── Category definitions (human-readable) ────────────────────────────────────
CATEGORY_DEFINITIONS: dict[str, dict] = {
    "individual_return": {
        "label": "Individual Return",
        "description": (
            "Tax returns, credit/deduction schedules, and related forms filed by "
            "individual taxpayers. Includes Form 1040 and all its schedules, estate "
            "and gift tax returns (706, 709), and forms related to individual income, "
            "credits, and deductions."
        ),
        "examples": ["Form 1040", "Form 1040 (Schedule A)", "Form 706", "Form 709"],
    },
    "business_return": {
        "label": "Business Return",
        "description": (
            "Tax returns filed by business entities: corporations (1120), partnerships "
            "(1065), trusts and estates (1041), S corporations (1120-S), tax-exempt "
            "organizations (990), and employment/excise tax returns (940, 941, 720)."
        ),
        "examples": ["Form 1120", "Form 1065", "Form 990", "Form 941"],
    },
    "information_return": {
        "label": "Information Return",
        "description": (
            "Third-party reporting forms filed by payers, brokers, and employers to "
            "report income paid to others. Includes the 1099 series, W-2, W-3, 1095 "
            "series, and FinCEN reports. These forms are not filed by the taxpayer "
            "receiving the income."
        ),
        "examples": ["Form W-2", "Form 1099-NEC", "Form 1099-R", "Form 1095-A"],
    },
    "shared": {
        "label": "Shared (Individual & Business)",
        "description": (
            "Forms used by both individual and business filers, such as withholding "
            "certificates (W-8 series, W-9), depreciation (4562), capital gains (4797), "
            "and passive activity loss (8582)."
        ),
        "examples": ["Form W-9", "Form 4562", "Form 4797", "Form 8582"],
    },
    "administrative_and_procedural": {
        "label": "Administrative & Procedural",
        "description": (
            "Applications, elections, extensions, authorizations, and other forms that "
            "facilitate IRS administrative processes — not returns or information "
            "reports. Includes power of attorney (2848), offers in compromise (656), "
            "installment agreements (9465), and e-file signature forms."
        ),
        "examples": ["Form 2848", "Form 656", "Form 9465", "Form 4506-T"],
    },
    "additional_information": {
        "label": "Additional Information (Guidance)",
        "description": (
            "IRS-to-filer guidance documents: Instructions for forms, Publications, "
            "Revenue Procedures, Revenue Rulings, and similar reference material. "
            "These are not filed with the IRS; they explain how to complete forms."
        ),
        "examples": ["Publication 17", "Instruction 1040", "Instruction 1099-MISC and 1099-NEC"],
    },
    "unclassified": {
        "label": "Unclassified",
        "description": "Documents that did not match any classification pattern.",
        "examples": [],
    },
}

# Topic-tag vocabulary lives in data/rules/tag_registry.json and is embedded in the catalog.


def register(mcp: FastMCP) -> None:
    """Register all IRS research resources with the MCP server."""

    # ── 1. Usage Policy ───────────────────────────────────────────────────────

    @mcp.resource("irs://meta/usage-policy")
    def usage_policy() -> str:
        """Scope statement and guardrails for the IRS Research MCP Server.

        Read this resource at the start of a session to understand what this
        server is for, what it is not for, and how to handle out-of-scope requests.
        """
        return """IRS Research MCP Server — Usage Policy
========================================

PURPOSE
-------
This server is a research and reference tool for professionals who need to
understand the structure, history, and taxonomy of IRS tax forms. Target
audiences include:

  - Academic researchers studying tax policy or fiscal administration
  - Journalists investigating IRS form changes or tax code evolution
  - Law practitioners researching the structure of tax obligations
  - Policy analysts studying the federal tax form landscape
  - Educators teaching tax law or accounting

WHAT THIS SERVER DOES
---------------------
  - Provides a searchable taxonomy of all IRS forms, instructions, and
    publications, classified by category and subject-matter topic tags
  - Returns complete revision histories for any IRS form, showing every
    indexed revision date and PDF link
  - Shows data-flow relationships between forms (e.g. which forms feed data
    into Form 1040) with IRS-sourced mechanism descriptions
  - Compares two historical revisions of a form line-by-line, quoting
    verbatim text from IRS PDFs
  - Quotes verbatim text from IRS "What's New" and "Future Developments"
    sections in instruction documents

WHAT THIS SERVER DOES NOT DO
-----------------------------
  - Give tax advice of any kind
  - Help individuals or businesses prepare, complete, or file tax returns
  - Interpret legal obligations ("Do I need to file this form?")
  - Determine eligibility for credits, deductions, or programs
  - Answer questions about specific taxpayer situations
  - Interpret statutory language or regulatory definitions

HANDLING OUT-OF-SCOPE REQUESTS
-------------------------------
When a user asks for tax advice, preparation help, or legal interpretation:
  1. Politely decline to answer the specific question.
  2. Explain that this server provides structural/historical research only.
  3. Direct the user to: a licensed CPA or tax attorney, the IRS website
     (irs.gov), IRS Free File, or the Taxpayer Advocate Service (TAS).

DATA SOURCES AND LIMITATIONS
-----------------------------
All data is sourced from publicly available IRS indexes and form PDFs.
  - The catalog is built from the IRS Forms & Instructions index pages.
  - Revision histories reflect what is indexed by IRS.gov, which may not
    capture every historical version.
  - The catalog is refreshed monthly. It is considered outdated if its IRS
    index scrape predates January 1 of the current year (it then cannot
    reflect the most recent tax year's forms). Check catalog_freshness in
    irs://catalog/summary and tell the user when it is not "fresh".
  - PDF-parsed line text is extracted programmatically and may contain
    minor OCR artefacts on older scanned forms.
  - Topic tags are editorial metadata assigned by this project (title keyword
    rules plus curated overrides), not classifications published by the IRS.

LABELING CLAIMS
---------------
Every relationship, category, and tag in the catalog carries a provenance
`basis`. When answering, label each claim with what supports it:
  - IRS-published fact: product numbers, titles, revision dates, PDF links,
    and `irs_naming` / `irs_text` relationships (quote the cited sentence).
  - Project inference: `inferred` basis (keyword tags, scraper categories,
    inherited tags/instructions).
  - Manually curated relationship: `curated` basis (cite its `source` or
    `supporting_evidence` when present).
  - Your own interpretation: anything not stated in a tool result. Say so
    explicitly.
See irs://catalog/summary for the definition of each basis.
"""

    # ── 2. Catalog Summary ────────────────────────────────────────────────────

    @mcp.resource("irs://catalog/summary")
    def catalog_summary() -> str:
        """High-level statistics about the IRS form catalog.

        Returns product counts by category, edge counts for the relationship
        graph, and the number of forms with topic tags. Use this to orient
        yourself before making tool calls.
        """
        cat = load_catalog()
        docs = cat["documents"]
        topic = set(tag_registry()["topic_tags"])

        tagged_count = sum(1 for d in docs.values() if topic.intersection(d.get("tags", [])))
        all_topic_tags = {t for d in docs.values() for t in d.get("tags", []) if t in topic}

        return json.dumps(
            {
                "total_unique_products": cat["total_unique_products"],
                "catalog_freshness": catalog_status(),
                "category_counts": cat["category_counts"],
                "relationship_edge_counts": cat["edge_counts"],
                "provenance_bases": cat.get("provenance_bases", {}),
                "forms_with_topic_tags": tagged_count,
                "topic_tag_vocabulary_size": len(all_topic_tags),
                "topic_tags_in_use": sorted(all_topic_tags),
            },
            indent=2,
        )

    # ── 3. Categories ─────────────────────────────────────────────────────────

    @mcp.resource("irs://catalog/categories")
    def catalog_categories() -> str:
        """The seven IRS document categories used in this catalog.

        Returns each category's label, description, and representative examples.
        Use these category names as filter values in lookup_form and
        browse_forms_by_topic.
        """
        cat = load_catalog()
        counts = cat["category_counts"]

        result = {}
        for key, defn in CATEGORY_DEFINITIONS.items():
            result[key] = {**defn, "count": counts.get(key, 0)}

        return json.dumps(result, indent=2)

    # ── 4. Topic Tags ─────────────────────────────────────────────────────────

    @mcp.resource("irs://catalog/topic-tags")
    def catalog_topic_tags() -> str:
        """The full subject-matter topic tag vocabulary with definitions and examples.

        Returns every supported topic tag, its description, and representative
        forms, plus the audience tags. Use these exact tag strings as values for
        the topic_tags parameter in lookup_form and browse_forms_by_topic.
        """
        docs = load_catalog()["documents"]
        registry = tag_registry()
        tag_counts: dict[str, int] = {}
        for d in docs.values():
            for tag in d.get("tags", []):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        return json.dumps(
            {
                "note": registry["note"],
                "topic_tags": {
                    tag: {**defn, "forms_in_catalog": tag_counts.get(tag, 0)}
                    for tag, defn in registry["topic_tags"].items()
                },
                "audience_tags": {
                    tag: {"description": desc, "forms_in_catalog": tag_counts.get(tag, 0)}
                    for tag, desc in registry["audience_tags"].items()
                },
            },
            indent=2,
        )

    # ── 5. Form node ─────────────────────────────────────────────────────────

    @mcp.resource("irs://form/{product_number}")
    def form_node(product_number: str) -> str:
        """Complete catalog node for a specific IRS form.

        Returns the full structured record: title, category, all tags,
        all historical revisions, all relationship edges, and the IRS About URL.
        This is a direct, citation-ready reference to the form's metadata.

        Use URL-encoded product numbers for forms with spaces, e.g.
        'Form%201040' for 'Form 1040'.
        """
        # Allow URL-encoded product numbers
        from urllib.parse import unquote

        decoded = unquote(product_number)
        node = resolve_product(decoded)
        if not node:
            return json.dumps({"error": f"Product not found: {decoded!r}"})
        return json.dumps(node, indent=2)
