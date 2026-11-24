"""Live-document retrieval, parsing, caching, and failure handling against a simulated IRS.gov.

No test touches the network or the real cache: IRS.gov is an httpx.MockTransport and every
test gets its own SQLite cache file. Tests that need real PDFs use PyMuPDF and are skipped
where it is not installed; the rest substitute a fake page extractor.
"""

from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from irs_research_server.config import settings
from irs_research_server.services import irs_history as h

CURRENT_PDF = "https://www.irs.gov/pub/irs-pdf/f1040sb.pdf"
PRIOR_PDF = "https://www.irs.gov/pub/irs-prior/f1040sb--2023.pdf"
INST_PDF = "https://www.irs.gov/pub/irs-pdf/i1040sb.pdf"
ABOUT_URL = "https://www.irs.gov/forms-pubs/about-schedule-b-form-1040"

# Shape of a real IRS About page (About Form 5498, fetched 2026-11).
ABOUT_HTML = """<html><body><main>
<h1>About Schedule B (Form 1040), Interest and Ordinary Dividends</h1>
<h2>Current revision</h2><p>Schedule B (Form 1040) PDF</p>
<h2>Recent developments</h2>
{developments}
<h2>Other items you may find useful</h2>
<ul><li>All revisions for Schedule B (Form 1040)</li><li>Other current products</li></ul>
</main></body></html>"""

SCHEDULE_B_PAGE = "Part I Interest\n1 List name of payer\n2 Add the amounts on line 1 . . . . 2\n"


class FakeIRS:
    """Minimal IRS.gov stand-in: routes map URL -> (status, content, headers) or an exception."""

    def __init__(self):
        self.routes: dict[str, object] = {}
        self.calls: list[str] = []

    def route(self, url, status=200, content=b"", headers=None):
        self.routes[url] = (status, content, headers or {})

    def fail(self, url, exc):
        self.routes[url] = exc

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        spec = self.routes.get(url)
        if spec is None:
            return httpx.Response(404)
        if isinstance(spec, Exception):
            raise spec
        status, content, headers = spec
        return httpx.Response(status, content=content, headers=headers)


def _fake_pdf(*pages: str) -> bytes:
    return b"%PDF-fake\n" + "\f".join(pages).encode()


def _fake_extract(data: bytes, max_pages: int | None = None) -> list[str]:
    if not data.startswith(b"%PDF-fake\n"):
        raise RuntimeError("not a PDF")
    pages = data[len(b"%PDF-fake\n"):].decode().split("\f")
    return pages[:max_pages] if max_pages else pages


@pytest.fixture
def irs(monkeypatch, tmp_path):
    fake = FakeIRS()
    monkeypatch.setattr(settings, "db_path", tmp_path / "cache.db")
    monkeypatch.setattr(h, "_transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(h, "_fetch_slots", None)
    return fake


@pytest.fixture
def fake_pdfs(monkeypatch):
    monkeypatch.setattr(h, "_extract_pages", _fake_extract)


def _backdate(table: str, when: str = "2020-01-01 00:00:00") -> None:
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute(f"UPDATE {table} SET fetched_at = ?", (when,))


# ── Form PDFs: download, parse, cache ─────────────────────────────────────────


async def test_form_pdf_is_downloaded_parsed_and_cached(irs, fake_pdfs):
    irs.route(PRIOR_PDF, content=_fake_pdf(SCHEDULE_B_PAGE))
    first = await h.get_parsed_form(PRIOR_PDF)
    second = await h.get_parsed_form(PRIOR_PDF)
    assert first["sections"]["Part I > Line 2"] == "Add the amounts on line 1"
    assert first["quality"]["text_layer"] in ("ok", "sparse")
    assert second == first
    assert irs.calls == [PRIOR_PDF]


async def test_prior_year_pdf_is_never_refetched(irs, fake_pdfs):
    irs.route(PRIOR_PDF, content=_fake_pdf(SCHEDULE_B_PAGE))
    await h.get_parsed_form(PRIOR_PDF)
    _backdate("parsed_pdfs")
    await h.get_parsed_form(PRIOR_PDF)
    assert irs.calls == [PRIOR_PDF]


async def test_stale_current_pdf_is_refetched(irs, fake_pdfs):
    irs.route(CURRENT_PDF, content=_fake_pdf(SCHEDULE_B_PAGE))
    await h.get_parsed_form(CURRENT_PDF)
    _backdate("parsed_pdfs")
    await h.get_parsed_form(CURRENT_PDF)
    assert irs.calls == [CURRENT_PDF, CURRENT_PDF]


async def test_stale_copy_is_served_with_warning_when_irs_is_unreachable(irs, fake_pdfs):
    irs.route(CURRENT_PDF, content=_fake_pdf(SCHEDULE_B_PAGE))
    fresh = await h.get_parsed_form(CURRENT_PDF)
    _backdate("parsed_pdfs")
    irs.fail(CURRENT_PDF, httpx.ConnectError("IRS.gov down"))
    stale = await h.get_parsed_form(CURRENT_PDF)
    assert stale["sections"] == fresh["sections"]
    assert "could not be reached" in stale["cache_warning"]


async def test_irs_error_without_cache_raises(irs, fake_pdfs):
    irs.route(CURRENT_PDF, status=500)
    with pytest.raises(httpx.HTTPStatusError):
        await h.get_parsed_form(CURRENT_PDF)


async def test_missing_pdf_raises_not_found(irs, fake_pdfs):
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await h.get_parsed_form(CURRENT_PDF)
    assert exc.value.response.status_code == 404


async def test_malformed_pdf_is_unreadable_and_not_cached(irs, fake_pdfs):
    irs.route(CURRENT_PDF, content=b"<html>Service unavailable</html>")
    first = await h.get_parsed_form(CURRENT_PDF)
    await h.get_parsed_form(CURRENT_PDF)
    assert first["sections"] == {} and first["quality"]["text_layer"] == "unreadable"
    assert len(irs.calls) == 2


# ── Outbound controls ─────────────────────────────────────────────────────────


async def test_non_irs_url_is_rejected_before_any_request(irs):
    with pytest.raises(ValueError):
        await h.get_parsed_form("https://example.com/f1040.pdf")
    assert irs.calls == []


async def test_redirect_off_irs_gov_is_blocked(irs, fake_pdfs):
    irs.route(CURRENT_PDF, status=302, headers={"location": "https://evil.example.com/f1040sb.pdf"})
    with pytest.raises(ValueError, match="Blocked non-IRS URL"):
        await h.get_parsed_form(CURRENT_PDF)
    assert "https://evil.example.com/f1040sb.pdf" not in irs.calls


async def test_redirect_within_irs_gov_is_followed(irs, fake_pdfs):
    irs.route(CURRENT_PDF, status=301, headers={"location": PRIOR_PDF})
    irs.route(PRIOR_PDF, content=_fake_pdf(SCHEDULE_B_PAGE))
    parsed = await h.get_parsed_form(CURRENT_PDF)
    assert "Part I > Line 1" in parsed["sections"]
    assert irs.calls == [CURRENT_PDF, PRIOR_PDF]


async def test_declared_oversize_download_is_rejected(irs, monkeypatch):
    monkeypatch.setattr(settings, "max_pdf_bytes", 10)
    irs.route(CURRENT_PDF, content=b"x" * 100)
    with pytest.raises(h.DownloadTooLarge):
        await h.get_parsed_form(CURRENT_PDF)


async def test_streamed_oversize_download_is_rejected(irs, monkeypatch):
    monkeypatch.setattr(settings, "max_pdf_bytes", 10)

    async def body():
        yield b"x" * 6
        yield b"x" * 6

    irs.routes[CURRENT_PDF] = (200, body(), {})
    with pytest.raises(h.DownloadTooLarge):
        await h.get_parsed_form(CURRENT_PDF)


# ── About pages ───────────────────────────────────────────────────────────────


def test_recent_developments_section_only():
    html = ABOUT_HTML.format(developments="<p>None at this time.</p>")
    assert h.parse_recent_developments(html) == {"found": True, "highlights": ["None at this time."]}


def test_recent_developments_with_list_items_and_no_duplicates():
    html = ABOUT_HTML.format(
        developments="<ul><li><p>Draft Schedule B posted for 2026</p></li><li>Line 7 clarified</li></ul>"
    )
    assert h.parse_recent_developments(html)["highlights"] == ["Draft Schedule B posted for 2026", "Line 7 clarified"]


def test_page_without_recent_developments_section():
    html = "<main><h2>Current revision</h2><p>Form PDF</p></main>"
    assert h.parse_recent_developments(html) == {"found": False, "highlights": []}


async def test_about_page_fetch_and_cache(irs):
    irs.route(ABOUT_URL, content=ABOUT_HTML.format(developments="<p>None at this time.</p>").encode())
    first = await h.fetch_about_developments(ABOUT_URL)
    second = await h.fetch_about_developments(ABOUT_URL)
    assert first["section_found"] is True and first["highlights"] == ["None at this time."]
    assert second == first and irs.calls == [ABOUT_URL]


async def test_old_format_about_cache_is_refetched(irs):
    with h._get_db() as conn:
        conn.execute(
            "INSERT INTO about_pages (about_url, developments_json) VALUES (?, ?)",
            (ABOUT_URL, json.dumps({"about_url": ABOUT_URL, "highlights": ["whole page text"]})),
        )
    irs.route(ABOUT_URL, content=ABOUT_HTML.format(developments="<p>None at this time.</p>").encode())
    result = await h.fetch_about_developments(ABOUT_URL)
    assert result["highlights"] == ["None at this time."] and irs.calls == [ABOUT_URL]


async def test_stale_about_page_served_with_warning_when_unreachable(irs):
    irs.route(ABOUT_URL, content=ABOUT_HTML.format(developments="<p>None at this time.</p>").encode())
    await h.fetch_about_developments(ABOUT_URL)
    _backdate("about_pages")
    irs.fail(ABOUT_URL, httpx.ReadTimeout("slow"))
    result = await h.fetch_about_developments(ABOUT_URL)
    assert result["highlights"] == ["None at this time."] and "cache_warning" in result


# ── Instruction "What's New" ──────────────────────────────────────────────────


def test_whats_new_extraction_stops_at_next_section():
    pages = [
        "Instructions for Schedule B\nWhat's New\nLine 7 now asks about digital accounts."
        "\nGeneral Instructions\nPurpose"
    ]
    assert h.extract_whats_new(pages) == "### What's New\nLine 7 now asks about digital accounts."


async def test_whats_new_fetch_and_cache(irs, fake_pdfs):
    irs.route(INST_PDF, content=_fake_pdf("What's New\nNew line 7.\nReminders\nKeep records."))
    first = await h.get_inst_whats_new(INST_PDF)
    second = await h.get_inst_whats_new(INST_PDF)
    assert first == {"notes": "### What's New\nNew line 7."} and second == first
    assert irs.calls == [INST_PDF]


# ── Real PDFs (PyMuPDF) ───────────────────────────────────────────────────────


def _real_pdf(pages: list[list[str]]) -> bytes:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        for i, line in enumerate(lines):
            page.insert_text((72, 72 + 14 * i), line, fontsize=10)
    return doc.tobytes()


async def test_real_pdf_text_layer_is_parsed(irs):
    lines = ["SCHEDULE B (Form 1040) 2024", "Part I Interest", "1 List name of payer", "2 Add the amounts on line 1"]
    irs.route(PRIOR_PDF, content=_real_pdf([lines * 4]))
    parsed = await h.get_parsed_form(PRIOR_PDF)
    assert "Part I > Line 2" in parsed["sections"]
    assert parsed["quality"]["text_layer"] == "ok"


async def test_scanned_pdf_without_text_layer_is_flagged(irs):
    irs.route(PRIOR_PDF, content=_real_pdf([[], []]))
    parsed = await h.get_parsed_form(PRIOR_PDF)
    assert parsed["quality"]["text_layer"] == "missing"


async def test_corrupt_pdf_with_real_parser_is_unreadable(irs):
    pytest.importorskip("pymupdf")
    irs.route(PRIOR_PDF, content=b"%PDF-1.7\n this is not really a pdf")
    parsed = await h.get_parsed_form(PRIOR_PDF)
    assert parsed["quality"]["text_layer"] == "unreadable"


# ── Through the MCP tool layer ────────────────────────────────────────────────


def _tool_json(result) -> dict:
    return json.loads(result.content[0].text)


async def test_compare_tool_end_to_end_with_simulated_irs(irs, fake_pdfs):
    fastmcp = pytest.importorskip("fastmcp")
    from irs_research_server.app import mcp

    revs = h.resolve_product("Form 1040 (Schedule B)")["revisions"]
    url_2023 = next(r["pdf_url"] for r in revs if r["revision_date"] == "2023" and "irs-prior" in r["pdf_url"])
    url_2024 = next(r["pdf_url"] for r in revs if r["revision_date"] == "2024" and "irs-prior" in r["pdf_url"])
    page = "Part III Foreign Accounts\n8 During {y}, did you receive a distribution from a foreign trust?\n"
    irs.route(url_2023, content=_fake_pdf(SCHEDULE_B_PAGE + page.format(y=2023)))
    irs.route(url_2024, content=_fake_pdf(SCHEDULE_B_PAGE.replace("payer", "payer or broker") + page.format(y=2024)))

    async with fastmcp.Client(mcp) as client:
        result = _tool_json(
            await client.call_tool(
                "compare_form_revisions",
                {"product_number": "Form 1040 (Schedule B)", "rev_from": "2023", "rev_to": "2024"},
            )
        )
    diff = result["line_diff"]
    assert diff["modified"]["Part I > Line 1"]["changes"] == [{"op": "insert", "before": "", "after": "or broker"}]
    assert "Part III > Line 8" in diff["year_only_changes"]
    assert result["limitations"]


async def test_tool_rejects_out_of_range_parameters():
    fastmcp = pytest.importorskip("fastmcp")
    from irs_research_server.app import mcp

    async with fastmcp.Client(mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("lookup_form", {"query": "1040", "limit": 500})
        with pytest.raises(Exception):
            await client.call_tool("trace_form_lineage", {"product_number": "Form 1040", "max_depth": 9})


async def test_tool_reports_unknown_tags_with_vocabulary():
    fastmcp = pytest.importorskip("fastmcp")
    from irs_research_server.app import mcp

    async with fastmcp.Client(mcp) as client:
        result = _tool_json(await client.call_tool("browse_forms_by_topic", {"topic_tags": ["not-a-tag"]}))
    assert "Unknown tag" in result["error"] and "gambling" in result["valid_topic_tags"]
