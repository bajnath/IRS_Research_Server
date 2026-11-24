"""IRS Research MCP prompts.

Pre-written conversation starters designed for professional research
(academics, journalists, law practitioners, policy researchers).

Each prompt frames the session correctly from the first message: it asks
for structural facts, quoted IRS text, and historical data — not tax
advice or eligibility interpretation.
"""

from __future__ import annotations

from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register all IRS research prompts with the MCP server."""

    @mcp.prompt()
    def research_form(
        product_number: str,
        focus: str = "overview",
    ) -> str:
        """Deep-dive research session for a specific IRS form.

        Args:
            product_number: The IRS product number to research, e.g. 'Form 1099-NEC'.
            focus: Research focus — 'overview', 'history', 'relationships', or 'all'.
        """
        focus_instructions = {
            "overview": (
                "Provide a complete structural overview: its category, subject-matter "
                "topic tags, all related instructions and publications, and what the "
                "form is used for according to its IRS title and About page."
            ),
            "history": (
                "Provide a complete revision history: when the form was first introduced "
                "(earliest indexed revision), every subsequent revision date, and any "
                "notes from the IRS About page on recent developments or future changes."
            ),
            "relationships": (
                "Map the form's structural relationships: which forms it attaches to as "
                "a schedule, which forms attach to it, what data flows into it and where "
                "that data flows downstream, and any supersedure or split history. "
                "Quote the IRS-defined data-flow mechanism text for each edge."
            ),
            "all": (
                "Provide a comprehensive research report covering: (1) structural "
                "overview including category and topic tags, (2) complete revision "
                "history from earliest to latest, (3) all data-flow and schedule "
                "relationships with IRS mechanism text, and (4) notes from the IRS "
                "About page on recent and future developments."
            ),
        }
        instruction = focus_instructions.get(
            focus,
            focus_instructions["overview"],
        )
        return (
            f"I am conducting professional research on {product_number}. "
            f"{instruction} "
            "Please present only IRS-sourced facts. Do not give tax advice, "
            "do not interpret eligibility, and do not recommend any action "
            "to any taxpayer."
        )

    @mcp.prompt()
    def explore_topic(
        topic: str,
        category: str = "",
    ) -> str:
        """Survey all IRS forms related to a subject-matter topic.

        Args:
            topic: The subject-matter topic to explore, e.g. 'gambling',
                   'retirement', 'international'. Use the irs://catalog/topic-tags
                   resource to see all supported values.
            category: Optional category to restrict to, e.g. 'information_return'
                      or 'individual_return'. Leave blank to see all categories.
        """
        category_clause = (
            f" within the {category} category" if category else " across all form categories"
        )
        return (
            f"I want to understand the complete landscape of IRS forms related to "
            f"'{topic}'{category_clause}. "
            "Please: (1) list every form in the catalog tagged with this topic, "
            "(2) describe each form using only its IRS title and official description, "
            "(3) note the category and latest revision year for each, "
            "(4) show how these forms relate to each other where applicable "
            "(e.g. which ones report to which return, which are third-party vs. "
            "taxpayer-filed). "
            "Present only structural facts from IRS sources. Do not give tax advice."
        )

    @mcp.prompt()
    def trace_data_pipeline(
        source_form: str,
        target_form: str = "Form 1040",
    ) -> str:
        """Trace the data-flow pipeline from one IRS form to another.

        Args:
            source_form: The form where data originates, e.g. 'Form W-2G'.
            target_form: The return or form where data ultimately arrives,
                         e.g. 'Form 1040'. Defaults to Form 1040.
        """
        return (
            f"I want to trace the data pipeline from {source_form} to {target_form}. "
            "Please: (1) show each step in the chain — the form at each node and "
            "the IRS-defined mechanism by which data flows to the next form, "
            "(2) note any intermediate schedules or worksheets in the chain, "
            "(3) quote the IRS mechanism text for each data-flow edge verbatim. "
            "If a direct path does not exist in the catalog, describe the closest "
            "documented path and note where the trail ends. "
            "Do not advise on filing obligations; present structural facts only."
        )

    @mcp.prompt()
    def form_change_history(
        product_number: str,
        from_year: str = "",
        to_year: str = "",
    ) -> str:
        """Chronological history of changes to an IRS form across revisions.

        Args:
            product_number: The IRS product number, e.g. 'Form 1040 (Schedule C)'.
            from_year: Starting year for the comparison range, e.g. '2020'.
                       Leave blank to request the full available history.
            to_year: Ending year for the comparison, e.g. '2023'.
                     Leave blank to use the most recent available revision.
        """
        range_clause = ""
        if from_year and to_year:
            range_clause = f" from {from_year} to {to_year}"
        elif from_year:
            range_clause = f" from {from_year} to the most recent revision"
        elif to_year:
            range_clause = f" up to {to_year}"

        return (
            f"I want a chronological history of changes to {product_number}"
            f"{range_clause}. "
            "Please: (1) list all available revision dates, noting the earliest "
            "indexed revision as the approximate introduction date, "
            "(2) for revision pairs where PDF data is available, compare them "
            "line-by-line and report added, removed, and modified lines, "
            "(3) quote verbatim any 'What's New' or 'Future Developments' text "
            "from the corresponding instruction document for each revision, "
            "(4) note any structural events such as the form being superseded, "
            "split from another form, or given a new schedule structure. "
            "All quoted text must be from IRS source documents. "
            "Do not interpret the legal significance of any change."
        )
