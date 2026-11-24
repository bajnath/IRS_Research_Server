"""Parser fidelity tests built from text patterns seen in real IRS form PDFs (no network)."""

from irs_research_server.services.form_parser import (
    compute_line_diff,
    parse_form_text,
    quality_warnings,
    tax_year_of,
)

# Shape of Schedule B (Form 1040) page text as extracted from the IRS PDF.
SCHEDULE_B = """SCHEDULE B
(Form 1040)
{year}
Interest and Ordinary Dividends
Part I
Interest
1
List name of payer. If any interest is from a seller-financed mortgage
Amount
1
2 Add the amounts on line 1 . . . . . . . . . . . . 2
3 Excludable interest on series EE and I U.S. savings bonds issued after {bond_year}.
Attach Form 8815 . . . . . . . . 3
Part III
Foreign
Accounts
file FinCEN Form
114 may result in
substantial penalties.
8 During {year}, did you receive a distribution from a foreign trust?
For Paperwork Reduction Act Notice, see your tax return instructions. Cat. No. 17146N Schedule B (Form 1040) {year}
"""


def _schedule_b(year: int, bond_year: int = 1989) -> list[str]:
    return [SCHEDULE_B.format(year=year, bond_year=bond_year) * 1]


def test_parts_and_lines_become_scoped_anchors():
    sections = parse_form_text(_schedule_b(2024))["sections"]
    assert list(sections) == [
        "header",
        "Part I",
        "Part I > Line 1",
        "Part I > Line 2",
        "Part I > Line 3",
        "Part III",
        "Part III > Line 8",
    ]


def test_dot_leaders_and_amount_column_numbers_are_removed():
    sections = parse_form_text(_schedule_b(2024))["sections"]
    assert sections["Part I > Line 2"] == "Add the amounts on line 1"
    assert sections["Part I > Line 3"].endswith("Attach Form 8815")


def test_margin_numbers_are_not_line_anchors():
    sections = parse_form_text(_schedule_b(2024))["sections"]
    assert "Part III > Line 114" not in sections
    assert "114 may result in" in sections["Part III"]


def test_footer_is_removed():
    parsed = parse_form_text(_schedule_b(2024))
    assert all("Cat. No." not in text for text in parsed["sections"].values())
    assert parsed["quality"]["header_footer_lines_removed"] == 1


def test_tax_year_rollover_is_year_only_not_modified():
    old = parse_form_text(_schedule_b(2023))["sections"]
    new = parse_form_text(_schedule_b(2024))["sections"]
    diff = compute_line_diff(old, new, 2023, 2024)
    assert diff["modified"] == {}
    assert "Part III > Line 8" in diff["year_only_changes"]


def test_non_rollover_year_change_is_reported():
    """A change to a substantive year (bond issue date) must not be hidden by year normalization."""
    old = parse_form_text(_schedule_b(2023, bond_year=1989))["sections"]
    new = parse_form_text(_schedule_b(2024, bond_year=1990))["sections"]
    diff = compute_line_diff(old, new, 2023, 2024)
    assert diff["modified"]["Part I > Line 3"]["changes"] == [
        {"op": "replace", "before": "1989.", "after": "1990."}
    ]


def test_year_glued_to_text_is_normalized():
    diff = compute_line_diff({"header": "Form1040 2022U.S. Return"}, {"header": "Form1040 2024U.S. Return"}, 2022, 2024)
    assert diff["modified"] == {} and "header" in diff["year_only_changes"]


def test_duplicate_anchors_are_suffixed_not_overwritten():
    parsed = parse_form_text(["1 First text\n1 Repeated first\n2 Second\n"])
    assert parsed["sections"]["Line 1"] == "First text"
    assert parsed["sections"]["Line 1 [2]"] == "Repeated first"
    assert parsed["quality"]["duplicate_anchors"] == ["Line 1"]


def test_backward_cross_reference_does_not_break_numbering():
    """Schedule 1 (2024): '1a or 1d' is a cross-reference, not a new Line 1a."""
    page = (
        "Part I Additional Income\n1 Taxable refunds\n2a Alimony received\n3 Business income\n"
        "8 Other income:\n1a or 1d 8s\n9 Total other income\n10 Combine lines\n"
    )
    page2 = "Part II Adjustments\n11 Educator expenses\n12 Certain business expenses\n"
    sections = parse_form_text([page, page2])["sections"]
    assert "Part I > Line 1a" not in sections
    assert {"Part I > Line 9", "Part I > Line 10", "Part II > Line 11", "Part II > Line 12"} <= set(sections)


def test_numbering_may_restart_in_a_new_part():
    """Form 8949 restarts at line 1 in Part II."""
    page = "Part I Short-Term\n1 Description\n2 Totals\nPart II Long-Term\n1 Description\n2 Totals\n"
    sections = parse_form_text([page])["sections"]
    assert {"Part I > Line 2", "Part II > Line 1", "Part II > Line 2"} <= set(sections)


def test_multi_copy_forms_are_scoped_by_copy():
    copy_a = "Copy A For Internal Revenue Service Center\n1 Nonemployee compensation\n2 Payer made direct sales\n"
    copy_b = "Copy B For Recipient\n1 Nonemployee compensation\n2 Payer made direct sales\n"
    parsed = parse_form_text([copy_a, copy_b])
    assert "Copy A > Line 1" in parsed["sections"] and "Copy B > Line 1" in parsed["sections"]
    assert parsed["quality"]["duplicate_anchors"] == []


def test_dropped_copy_shows_as_removed():
    page = "Copy {c} For Recipient\n1 Nonemployee compensation\n"
    old = parse_form_text([page.format(c="A"), page.format(c="B"), page.format(c="C")])["sections"]
    new = parse_form_text([page.format(c="A"), page.format(c="B")])["sections"]
    diff = compute_line_diff(old, new)
    assert set(diff["removed"]) == {"Copy C", "Copy C > Line 1"}


def test_running_headers_repeated_on_each_page_are_removed():
    pages = [f"Form 9999 (2024) Page {n}\n{n} Line text {n}\nContinued on next page" for n in (1, 2, 3)]
    parsed = parse_form_text(pages)
    assert all("Continued on next page" not in t for t in parsed["sections"].values())


def test_scanned_pdf_is_flagged():
    parsed = parse_form_text(["", ""])
    assert parsed["quality"]["text_layer"] == "missing"
    assert any("no extractable text" in w for w in quality_warnings("from", parsed["quality"]))


def test_sparse_text_layer_is_flagged():
    parsed = parse_form_text(["Form 1040 1943"])
    assert parsed["quality"]["text_layer"] == "sparse"


def test_tax_year_of_revision_dates():
    assert tax_year_of("2024") == 2024
    assert tax_year_of("Dec 2026") == 2026
    assert tax_year_of("") is None
