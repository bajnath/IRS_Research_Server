"""Functional tests for IRS Research tools and service layer.

Uses the real catalog on disk (data/catalog/irs_catalog.json). All tests
are pure in-memory and do not make any network requests.
"""

from __future__ import annotations

from irs_research_server.services.form_parser import compute_line_diff
from irs_research_server.services.irs_history import (
    load_catalog,
    resolve_product,
)

# ── Catalog & resolver ────────────────────────────────────────────────────────


def test_catalog_loads_and_has_expected_shape():
    cat = load_catalog()
    assert cat["total_unique_products"] > 2800
    assert "documents" in cat
    assert "category_counts" in cat
    assert "edge_counts" in cat


def test_catalog_has_expected_categories():
    cat = load_catalog()
    expected = {
        "individual_return",
        "business_return",
        "information_return",
        "shared",
        "administrative_and_procedural",
        "additional_information",
    }
    assert expected.issubset(cat["category_counts"].keys())


def test_resolve_product_exact_match():
    node = resolve_product("Form 1040")
    assert node is not None
    assert node["product_number"] == "Form 1040"


def test_resolve_product_bare_number():
    """Bare form number without 'Form ' prefix should resolve via fallback."""
    node = resolve_product("1099-NEC")
    assert node is not None
    assert node["product_number"] == "Form 1099-NEC"


def test_resolve_product_case_insensitive():
    node = resolve_product("form w-2g")
    assert node is not None
    assert node["product_number"] == "Form W-2G"


def test_resolve_product_not_found():
    assert resolve_product("Form ZZZZZZ-99") is None


# ── Topic tags ────────────────────────────────────────────────────────────────


def test_gambling_form_has_correct_topic_tags():
    node = resolve_product("Form W-2G")
    assert node is not None
    tags = set(node["tags"])
    assert "gambling" in tags
    assert "withholding" in tags


def test_retirement_form_has_correct_topic_tag():
    node = resolve_product("Form 1099-R")
    assert node is not None
    assert "retirement" in node["tags"]


def test_health_insurance_form_has_correct_topic_tag():
    node = resolve_product("Form 8889")
    assert node is not None
    assert "healthcare" in node["tags"]


def test_exempt_org_form_has_correct_topic_tag():
    node = resolve_product("Form 990")
    assert node is not None
    assert "exempt-organizations" in node["tags"]


def test_at_least_100_forms_have_topic_tags():
    """Sanity check that the topic_tags pipeline applied to the catalog."""
    cat = load_catalog()
    topic = set(cat["tag_registry"]["topic_tags"])
    tagged = sum(1 for d in cat["documents"].values() if topic.intersection(d.get("tags", [])))
    assert tagged >= 100


# ── compute_line_diff ─────────────────────────────────────────────────────────


def test_diff_added_lines():
    old = {"Line 1": "Income"}
    new = {"Line 1": "Income", "Line 2": "Deduction"}
    result = compute_line_diff(old, new)
    assert "Line 2" in result["added"]
    assert result["removed"] == {}
    assert result["modified"] == {}


def test_diff_removed_lines():
    old = {"Line 1": "Income", "Line 2": "Old deduction"}
    new = {"Line 1": "Income"}
    result = compute_line_diff(old, new)
    assert "Line 2" in result["removed"]
    assert result["added"] == {}


def test_diff_modified_lines():
    old = {"Line 1": "Report wages here"}
    new = {"Line 1": "Report wages and tips here"}
    result = compute_line_diff(old, new)
    assert "Line 1" in result["modified"]
    assert result["modified"]["Line 1"]["before"] == "Report wages here"
    assert result["modified"]["Line 1"]["after"] == "Report wages and tips here"
    assert result["modified"]["Line 1"]["changes"] == [{"op": "insert", "before": "", "after": "and tips"}]


def test_diff_identical_sections():
    sections = {"Line 1": "No change", "Part I": "Same text"}
    result = compute_line_diff(sections, sections)
    assert result["added"] == {}
    assert result["removed"] == {}
    assert result["modified"] == {}


def test_diff_empty_inputs():
    result = compute_line_diff({}, {})
    assert result == {"added": {}, "removed": {}, "modified": {}, "year_only_changes": {}}


# ── Tool functions (called directly) ─────────────────────────────────────────


def test_lookup_form_exact():

    # We test the underlying catalog directly — tool registration tested in test_server.py
    node = resolve_product("Form W-2G")
    assert node["product_number"] == "Form W-2G"
    assert "gambling" in node["tags"]


def test_browse_forms_by_topic_gambling():
    """browse_forms_by_topic logic: all gambling-tagged forms should include W-2G."""
    cat = load_catalog()
    docs = cat["documents"]
    gambling_forms = [
        d["product_number"]
        for d in docs.values()
        if "gambling" in d.get("tags", [])
    ]
    assert "Form W-2G" in gambling_forms


def test_browse_forms_by_topic_retirement_individual():
    """Retirement + individual_return filter should include Form 5329."""
    cat = load_catalog()
    docs = cat["documents"]
    matches = [
        d["product_number"]
        for d in docs.values()
        if "retirement" in d.get("tags", []) and d["category"] == "individual_return"
    ]
    assert "Form 5329" in matches


def test_lookup_form_tag_filter():
    """Tag filter: forms tagged international should include Form 2555."""
    cat = load_catalog()
    docs = cat["documents"]
    fi_forms = [
        d["product_number"]
        for d in docs.values()
        if "international" in d.get("tags", [])
    ]
    assert "Form 2555" in fi_forms


def test_trace_form_lineage_has_flows():
    """Form W-2 should have downstream flows into Form 1040."""
    cat = load_catalog()
    docs = cat["documents"]
    w2 = docs.get("Form W-2")
    assert w2 is not None
    targets = [f["target"] for f in w2["relationships"]["flows_into"]]
    assert "Form 1040" in targets


def test_form_history_earliest_revision():
    """The earliest revision should be the last element in the revisions list."""
    node = resolve_product("Form 1000")
    assert node is not None
    revisions = node["revisions"]
    assert len(revisions) > 1
    # Newest first — earliest is last
    earliest = revisions[-1]
    assert earliest["revision_date"] != ""


def test_superseded_form_exists_in_catalog():
    """Form 1040-EZ (retired) should exist as a node with a superseded_by edge."""
    node = resolve_product("Form 1040-EZ")
    assert node is not None
    assert len(node["relationships"]["superseded_by"]) > 0
    targets = [s["target"] for s in node["relationships"]["superseded_by"]]
    assert "Form 1040" in targets


def test_split_from_edge_exists():
    """Form 1099-NEC should have a split_from edge pointing to Form 1099-MISC."""
    node = resolve_product("Form 1099-NEC")
    assert node is not None
    parents = [s["parent"] for s in node["relationships"]["split_from"]]
    assert "Form 1099-MISC" in parents
