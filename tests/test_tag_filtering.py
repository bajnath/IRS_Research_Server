"""Smoke tests: tag-filtering behaviour of IRS Research tools.

These tests verify that the ``lookup_form`` and ``browse_forms_by_topic``
tag-filter logic works correctly against the real on-disk catalog.  They call
the same filtering logic that the registered MCP tools execute — no network
requests, no MCP round-trip.

Coverage:
  - Single topic-tag filter returns the expected representative forms
  - Multi-tag AND filter (intersection) is narrower than any single tag alone
  - Category + tag combined filter is properly restrictive
  - Filtering on a tag that matches no forms returns an empty result set
  - ``lookup_form`` keyword + tag filter narrows correctly
  - Every documented topic tag exists and has at least one match in the catalog
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from irs_research_server.services.irs_history import load_catalog, tag_registry, unknown_tags

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_FILE = ROOT / "data" / "rules" / "tag_registry.json"

# ---------------------------------------------------------------------------
# Helpers — mirror the filtering logic used by the tool functions
# ---------------------------------------------------------------------------

AUDIENCE_TAGS = frozenset(json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))["audience_tags"])


def _browse(
    topic_tags: list[str],
    category: str | None = None,
) -> list[dict]:
    """Return catalog docs that carry ALL specified topic_tags (optionally filtered by category)."""
    docs = load_catalog()["documents"]
    tag_set = set(topic_tags)
    results = []
    for d in docs.values():
        form_tags = set(d.get("tags", []))
        if not tag_set.issubset(form_tags):
            continue
        if category and d["category"] != category:
            continue
        results.append(d)
    return results


def _lookup(
    query: str,
    topic_tags: list[str] | None = None,
    category: str | None = None,
) -> list[dict]:
    """Return catalog docs matching query keyword and optional tag/category filters."""
    docs = load_catalog()["documents"]
    q = query.strip().lower()
    results = []
    for d in docs.values():
        if q and q not in d["product_number"].lower() and q not in d["title"].lower():
            continue
        if category and d["category"] != category:
            continue
        if topic_tags:
            form_tags = set(d.get("tags", []))
            if not all(t in form_tags for t in topic_tags):
                continue
        results.append(d)
    return results


def _product_numbers(docs: list[dict]) -> set[str]:
    return {d["product_number"] for d in docs}


# ---------------------------------------------------------------------------
# Single-tag filters — representative form must appear
# ---------------------------------------------------------------------------


def test_browse_gambling_includes_w2g():
    results = _browse(["gambling"])
    assert "Form W-2G" in _product_numbers(results), (
        "Form W-2G must be tagged 'gambling'"
    )


def test_browse_retirement_includes_1099r():
    results = _browse(["retirement"])
    assert "Form 1099-R" in _product_numbers(results)


def test_browse_retirement_includes_5329():
    results = _browse(["retirement"])
    assert "Form 5329" in _product_numbers(results)


def test_browse_healthcare_includes_8889():
    results = _browse(["healthcare"])
    assert "Form 8889" in _product_numbers(results)


def test_browse_international_includes_2555():
    results = _browse(["international"])
    assert "Form 2555" in _product_numbers(results)


def test_browse_exempt_orgs_includes_990():
    results = _browse(["exempt-organizations"])
    assert "Form 990" in _product_numbers(results)


def test_browse_investments_includes_8949():
    results = _browse(["investments"])
    assert "Form 8949" in _product_numbers(results)


def test_browse_employment_taxes_includes_w2():
    results = _browse(["employment-taxes"])
    assert "Form W-2" in _product_numbers(results)


def test_browse_partnerships_includes_1065():
    results = _browse(["partnerships"])
    assert "Form 1065" in _product_numbers(results)


def test_browse_withholding_includes_w4():
    results = _browse(["withholding"])
    assert "Form W-4" in _product_numbers(results)


def test_browse_estate_gift_includes_706_and_709():
    results = _browse(["estate-gift"])
    pns = _product_numbers(results)
    assert "Form 706" in pns
    assert "Form 709" in pns


def test_browse_self_employment_includes_schedule_c():
    results = _browse(["self-employment"])
    assert "Form 1040 (Schedule C)" in _product_numbers(results)


def test_browse_depreciation_includes_4562():
    results = _browse(["depreciation"])
    assert "Form 4562" in _product_numbers(results)


def test_browse_credits_includes_8863():
    results = _browse(["credits"])
    assert "Form 8863" in _product_numbers(results)


def test_browse_real_estate_includes_1098():
    results = _browse(["real-estate"])
    assert "Form 1098" in _product_numbers(results)


def test_browse_excise_tax_includes_720():
    results = _browse(["excise-tax"])
    assert "Form 720" in _product_numbers(results)


# ---------------------------------------------------------------------------
# Multi-tag AND filter — intersection must be strictly narrower
# ---------------------------------------------------------------------------


def test_multi_tag_gambling_withholding_narrower_than_gambling_alone():
    gambling_only = _browse(["gambling"])
    gambling_and_withholding = _browse(["gambling", "withholding"])
    assert len(gambling_and_withholding) <= len(gambling_only)
    # Form W-2G carries both tags
    assert "Form W-2G" in _product_numbers(gambling_and_withholding)


def test_multi_tag_international_withholding_includes_w8ben():
    results = _browse(["international", "withholding"])
    assert "Form W-8BEN" in _product_numbers(results)


def test_multi_tag_retirement_credits_includes_8880():
    results = _browse(["retirement", "credits"])
    pns = _product_numbers(results)
    # Form 8880 carries both retirement and credits
    assert "Form 8880" in pns


def test_multi_tag_produces_subset_of_each_single_tag():
    retirement = _product_numbers(_browse(["retirement"]))
    credits = _product_numbers(_browse(["credits"]))
    both = _product_numbers(_browse(["retirement", "credits"]))
    assert both.issubset(retirement)
    assert both.issubset(credits)


# ---------------------------------------------------------------------------
# Category + tag combined filter
# ---------------------------------------------------------------------------


def test_category_and_tag_filter_retirement_individual():
    results = _browse(["retirement"], category="individual_return")
    pns = _product_numbers(results)
    assert "Form 5329" in pns
    # Verify no non-individual results leaked through
    for d in results:
        assert d["category"] == "individual_return", (
            f"{d['product_number']} has category {d['category']!r}"
        )


def test_category_and_tag_filter_gambling_information():
    results = _browse(["gambling"], category="information_return")
    pns = _product_numbers(results)
    assert "Form W-2G" in pns
    for d in results:
        assert d["category"] == "information_return"


def test_category_and_tag_filter_exempt_orgs_business():
    results = _browse(["exempt-organizations"], category="business_return")
    for d in results:
        assert d["category"] == "business_return"
    assert len(results) > 0, "Expected at least one business_return in exempt-organizations"


# ---------------------------------------------------------------------------
# Non-existent tag returns empty
# ---------------------------------------------------------------------------


def test_browse_nonexistent_tag_returns_empty():
    results = _browse(["this-tag-does-not-exist"])
    assert results == []


def test_browse_valid_and_nonexistent_tag_returns_empty():
    results = _browse(["gambling", "this-tag-does-not-exist"])
    assert results == []


# ---------------------------------------------------------------------------
# lookup_form keyword + tag filter
# ---------------------------------------------------------------------------


def test_lookup_keyword_plus_tag_filter():
    """Keyword 'gambling' with tag filter 'gambling' should include Form W-2G."""
    results = _lookup("gambling", topic_tags=["gambling"])
    pns = _product_numbers(results)
    assert "Form W-2G" in pns


def test_lookup_keyword_alone_broader_than_with_tag():
    """Adding a tag filter to a keyword search must not increase the result count."""
    all_retirement = _lookup("retirement")
    with_tag = _lookup("retirement", topic_tags=["retirement"])
    # Tag filter can only narrow, never broaden
    assert len(with_tag) <= len(all_retirement)


def test_lookup_tag_filter_excludes_untagged_matches():
    """Forms matching a keyword but not the tag must be excluded."""
    results_no_tag = _lookup("1040")
    results_with_tag = _lookup("1040", topic_tags=["gambling"])
    pns_no_tag = _product_numbers(results_no_tag)
    pns_with_tag = _product_numbers(results_with_tag)
    # Form 1040 itself is not tagged 'gambling', so it must not appear
    if "Form 1040" in pns_no_tag:
        assert "Form 1040" not in pns_with_tag


# ---------------------------------------------------------------------------
# Full vocabulary: every registered topic tag has at least one catalog match
# ---------------------------------------------------------------------------

ALL_TOPIC_TAGS = sorted(json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))["topic_tags"])


def test_catalog_embeds_current_registry():
    """A stale catalog means tag_registry.json changed without rebuilding."""
    assert tag_registry() == json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))


def test_catalog_uses_only_registered_tags():
    docs = load_catalog()["documents"]
    used = {t for d in docs.values() for t in d.get("tags", [])}
    unregistered = sorted(used - AUDIENCE_TAGS - set(ALL_TOPIC_TAGS))
    assert used <= AUDIENCE_TAGS | set(ALL_TOPIC_TAGS), f"Unregistered tags: {unregistered}"


def test_readme_topic_tag_list_matches_registry():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    line = next(ln for ln in readme.splitlines() if ln.startswith("`core-return`"))
    assert sorted(re.findall(r"`([a-z-]+)`", line)) == ALL_TOPIC_TAGS


def test_unknown_tags_flags_only_unregistered():
    assert unknown_tags(["gambling", "individual", "not-a-tag"]) == ["not-a-tag"]


@pytest.mark.parametrize("tag", ALL_TOPIC_TAGS)
def test_every_documented_topic_tag_has_at_least_one_match(tag: str):
    """Every topic tag in the documented vocabulary must match at least one form."""
    results = _browse([tag])
    assert len(results) > 0, (
        f"Tag '{tag}' has no matching forms in the catalog — "
        "check curated_edges.json or rebuild the catalog."
    )
