"""Freshness rules for cached IRS content and the catalog (stdlib only, no I/O).

Policy:
  - Content is refreshed monthly: anything older than ``max_age_days`` is stale.
  - Anything fetched before January 1 of the current year is stale regardless of age,
    because it cannot reflect forms issued for the most recent tax year
    (e.g. in 2026, data must come from 2026 to cover tax year 2025).
  - Prior-year PDFs under /pub/irs-prior/ are immutable archives and never expire.
"""

from __future__ import annotations

from datetime import datetime, timezone

IMMUTABLE_URL_MARKER = "/pub/irs-prior/"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: str) -> datetime:
    """Parse ISO or SQLite CURRENT_TIMESTAMP strings; naive values are UTC."""
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_immutable(url: str) -> bool:
    return IMMUTABLE_URL_MARKER in url


def stale_reason(fetched_at: datetime, now: datetime, max_age_days: int) -> str | None:
    """Why content fetched at ``fetched_at`` is stale, or None if it is fresh."""
    if fetched_at.year < now.year:
        return (
            f"fetched in {fetched_at.year}, before the current year {now.year}; "
            f"may not reflect tax year {now.year - 1} forms"
        )
    age = (now - fetched_at).days
    if age > max_age_days:
        return f"{age} days old (monthly refresh limit is {max_age_days} days)"
    return None


def is_fresh(url: str, fetched_at: datetime, now: datetime, max_age_days: int) -> bool:
    return is_immutable(url) or stale_reason(fetched_at, now, max_age_days) is None


def catalog_freshness(scraped_at: str | None, now: datetime, max_age_days: int) -> dict:
    """Status of the catalog's source data: fresh, refresh_recommended, outdated, or unknown."""
    if not scraped_at:
        return {"status": "unknown", "message": "Catalog has no source scrape date; rebuild it."}
    when = parse_timestamp(scraped_at)
    reason = stale_reason(when, now, max_age_days)
    if reason is None:
        status = "fresh"
    elif when.year < now.year:
        status = "outdated"
    else:
        status = "refresh_recommended"
    return {
        "status": status,
        "source_scraped_at": when.isoformat(),
        "age_days": (now - when).days,
        "message": reason or "IRS index scraped within the monthly refresh window.",
    }
