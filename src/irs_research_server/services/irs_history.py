"""IRS Research data layer.

Responsibilities:
  - Load and cache the enriched IRS catalog (irs_catalog.json).
  - Resolve product numbers to catalog nodes.
  - Fetch and cache IRS About pages (HTML) for recent-developments notes.
  - Download and parse form PDFs (parsing/diff logic lives in form_parser.py).
  - Extract "What's New" / "Future Developments" sections from instruction PDFs.

All network calls are constrained to https://www.irs.gov/ and all responses
are cached in a local SQLite database so the server remains fast and
offline-capable after the first fetch.

Paths are configured via settings (config.py) rather than hardcoded, so they
can be overridden with CATALOG_PATH / DB_PATH environment variables.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from irs_research_server.config import settings
from irs_research_server.services.form_parser import PARSER_VERSION, parse_form_text
from irs_research_server.services.freshness import (
    catalog_freshness,
    is_immutable,
    parse_timestamp,
    stale_reason,
    utcnow,
)

ALLOWED_IRS_PREFIX = "https://www.irs.gov/"
USER_AGENT = "IRS-Research-MCP/0.1 (deterministic-catalog-and-diff)"

WHATS_NEW_START_RE = re.compile(r"^(?:What['']s New|Future Developments)\b", re.IGNORECASE)
NEXT_SECTION_RE = re.compile(
    r"^(?:Reminders|General Instructions|Specific Instructions|Purpose of Form|Who Must File)\b",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    """Load the enriched IRS catalog from disk. Result is cached for the process lifetime."""
    path: Path = settings.catalog_path
    return json.loads(path.read_text(encoding="utf-8"))


def tag_registry() -> dict:
    """The authoritative tag vocabulary embedded in the catalog by build_catalog.py."""
    return load_catalog()["tag_registry"]


def catalog_status() -> dict:
    """Freshness of the catalog's IRS source data under the monthly / current-year policy."""
    cat = load_catalog()
    status = catalog_freshness(cat.get("source_scraped_at"), utcnow(), settings.cache_max_age_days)
    status["source_scraped_at_basis"] = cat.get("source_scraped_at_basis")
    status["built_at"] = cat.get("built_at")
    return status


def unknown_tags(tags: list[str]) -> list[str]:
    """Return any tags that are neither registered topic tags nor audience tags."""
    registry = tag_registry()
    allowed = set(registry["topic_tags"]) | set(registry["audience_tags"])
    return [t for t in tags if t not in allowed]


def resolve_product(query: str) -> dict | None:
    """Exact (case-insensitive) product lookup with normalised 'Form ' fallback.

    Returns the full catalog node dict or None if not found.
    """
    docs = load_catalog()["documents"]
    q = query.strip()
    lower_map = {k.lower(): k for k in docs}

    for candidate in (
        q,
        f"Form {q}" if not q.lower().startswith(("form ", "inst", "publ")) else q,
    ):
        hit = lower_map.get(candidate.lower())
        if hit:
            return docs[hit]
    return None


def _get_db() -> sqlite3.Connection:
    """Open (and initialise) the SQLite cache database."""
    db_path: Path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS parsed_pdfs (
            pdf_url   TEXT NOT NULL,
            doc_kind  TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            fetched_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (pdf_url, doc_kind)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS about_pages (
            about_url        TEXT PRIMARY KEY,
            developments_json TEXT NOT NULL,
            fetched_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    return conn


def _validate_irs_url(url: str) -> None:
    """Raise ValueError for any URL that is not rooted at https://www.irs.gov/."""
    if not url or not url.startswith(ALLOWED_IRS_PREFIX):
        raise ValueError(f"Blocked non-IRS URL: {url!r}")


def _read_cache(sql: str, params: tuple, url: str) -> tuple[object | None, str | None]:
    """Return (cached payload or None, stale reason or None) for a ``SELECT payload, fetched_at`` query."""
    with _get_db() as conn:
        row = conn.execute(sql, params).fetchone()
    if not row:
        return None, None
    if is_immutable(url):
        return json.loads(row[0]), None
    return json.loads(row[0]), stale_reason(parse_timestamp(row[1]), utcnow(), settings.cache_max_age_days)


def _stale_note(reason: str | None) -> str:
    return f"IRS.gov could not be reached; serving a cached copy that is {reason}."


class DownloadTooLarge(ValueError):
    """An IRS document exceeded MAX_PDF_BYTES."""


# Bump when the About-page payload changes so older cached payloads are refetched.
ABOUT_PARSER_VERSION = 2

_fetch_slots: asyncio.Semaphore | None = None
# Tests substitute an httpx.MockTransport here; None means the real network.
_transport: httpx.AsyncBaseTransport | None = None


def _extract_pages(pdf_bytes: bytes, max_pages: int | None = None) -> list[str]:
    """Extract page text with PyMuPDF (imported lazily so the module loads without it)."""
    import pymupdf

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    pages = list(doc)[:max_pages] if max_pages else list(doc)
    return [page.get_text("text", sort=True) for page in pages]


def parse_recent_developments(html: bytes | str) -> dict:
    """Extract only the 'Recent developments' section of an IRS About-Form page.

    Returns {"found": bool, "highlights": [...]}; collection stops at the next heading of
    the same or higher level, so general page content is not included.
    """
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    heading = next(
        (h for h in main.find_all(["h2", "h3"]) if "recent developments" in h.get_text(" ", strip=True).lower()),
        None,
    )
    if heading is None:
        return {"found": False, "highlights": []}
    level = int(heading.name[1])
    highlights: list[str] = []
    for el in heading.find_all_next():
        if el.name in ("h1", "h2", "h3") and int(el.name[1]) <= level:
            break
        if el.name not in ("p", "li") or el.find_parent("li"):
            continue
        text = " ".join(el.get_text(" ", strip=True).split())
        if text and text not in highlights:
            highlights.append(text)
        if len(highlights) >= 25:
            break
    return {"found": True, "highlights": highlights}


async def _validate_hop(request: httpx.Request) -> None:
    # Runs for the initial request and every redirect, so a redirect cannot leave irs.gov.
    _validate_irs_url(str(request.url))


async def _download(url: str) -> bytes:
    """GET an irs.gov document with bounded size and bounded concurrent fetches."""
    global _fetch_slots
    if _fetch_slots is None:
        _fetch_slots = asyncio.Semaphore(settings.max_concurrent_fetches)
    async with _fetch_slots, httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        max_redirects=5,
        headers={"User-Agent": USER_AGENT},
        event_hooks={"request": [_validate_hop]},
        transport=_transport,
    ) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > settings.max_pdf_bytes:
                raise DownloadTooLarge(f"{url} is larger than {settings.max_pdf_bytes} bytes")
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body.extend(chunk)
                if len(body) > settings.max_pdf_bytes:
                    raise DownloadTooLarge(f"{url} is larger than {settings.max_pdf_bytes} bytes")
    return bytes(body)


async def fetch_about_developments(about_url: str) -> dict:
    """Fetch and cache the 'Recent developments' section of an IRS About-Form page.

    Returns a dict with keys: about_url, section_found, highlights (list[str]), and
    cache_warning when a stale copy had to be served. Cached copies are refreshed
    monthly and whenever they predate the current year.
    """
    _validate_irs_url(about_url)
    cached, reason = _read_cache(
        "SELECT developments_json, fetched_at FROM about_pages WHERE about_url = ?", (about_url,), about_url
    )
    if cached is not None and cached.get("parser_version") != ABOUT_PARSER_VERSION:
        cached, reason = None, None
    if cached is not None and reason is None:
        return cached
    try:
        html = await _download(about_url)
    except httpx.HTTPError:
        if cached is not None:
            return {**cached, "cache_warning": _stale_note(reason)}
        raise

    section = parse_recent_developments(html)
    payload = {
        "about_url": about_url,
        "parser_version": ABOUT_PARSER_VERSION,
        "section_found": section["found"],
        "highlights": section["highlights"],
    }
    with _get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO about_pages (about_url, developments_json) VALUES (?, ?)",
            (about_url, json.dumps(payload)),
        )
    return payload


async def get_parsed_form(pdf_url: str) -> dict:
    """Download (once) and parse a form PDF into {"sections": {anchor: text}, "quality": {...}}.

    Text is kept verbatim; tax-year rollovers are separated at diff time. Parses are
    cached in SQLite under a parser-versioned key, so parser changes invalidate them.
    Prior-year PDFs never expire; current/draft PDFs (replaced in place by the IRS) are
    refreshed monthly. Malformed PDFs return empty sections with
    quality.text_layer == "unreadable" (not cached).
    """
    _validate_irs_url(pdf_url)
    doc_kind = f"form_v{PARSER_VERSION}"
    cached, reason = _read_cache(
        "SELECT payload_json, fetched_at FROM parsed_pdfs WHERE pdf_url = ? AND doc_kind = ?",
        (pdf_url, doc_kind),
        pdf_url,
    )
    if cached is not None and reason is None:
        return cached
    try:
        pdf_bytes = await _download(pdf_url)
    except httpx.HTTPError:
        if cached is not None:
            return {**cached, "cache_warning": _stale_note(reason)}
        raise

    try:
        pages = _extract_pages(pdf_bytes)
    except Exception:
        parsed = parse_form_text([])
        parsed["quality"]["text_layer"] = "unreadable"
        return parsed

    parsed = parse_form_text(pages)
    with _get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO parsed_pdfs (pdf_url, doc_kind, payload_json) VALUES (?, ?, ?)",
            (pdf_url, doc_kind, json.dumps(parsed)),
        )
    return parsed


async def get_inst_whats_new(inst_pdf_url: str, max_pages: int = 4) -> dict:
    """Download (once) and extract 'Future Developments' / 'What's New' from an Inst PDF.

    Returns {"notes": verbatim text} plus cache_warning when a stale copy had to be
    served. Same freshness rules as get_parsed_form.
    """
    _validate_irs_url(inst_pdf_url)
    cached, reason = _read_cache(
        "SELECT payload_json, fetched_at FROM parsed_pdfs WHERE pdf_url = ? AND doc_kind = 'whats_new'",
        (inst_pdf_url,),
        inst_pdf_url,
    )
    if cached is not None and reason is None:
        return {"notes": cached}
    try:
        pdf_bytes = await _download(inst_pdf_url)
    except httpx.HTTPError:
        if cached is not None:
            return {"notes": cached, "cache_warning": _stale_note(reason)}
        raise

    summary = extract_whats_new(_extract_pages(pdf_bytes, max_pages))
    with _get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO parsed_pdfs (pdf_url, doc_kind, payload_json) VALUES (?, 'whats_new', ?)",
            (inst_pdf_url, json.dumps(summary)),
        )
    return {"notes": summary}


def extract_whats_new(pages: list[str]) -> str:
    """Verbatim 'What's New' / 'Future Developments' text, ending at the next standard section."""
    captured: list[str] = []
    recording = False
    for page in pages:
        for raw_line in page.splitlines():
            line = " ".join(raw_line.split())
            if not line:
                continue
            if WHATS_NEW_START_RE.match(line):
                recording = True
                captured.append(f"\n### {line}")
                continue
            if recording and NEXT_SECTION_RE.match(line):
                return "\n".join(captured).strip()
            if recording:
                captured.append(line)
    return "\n".join(captured).strip()
