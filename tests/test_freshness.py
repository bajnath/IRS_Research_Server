"""Freshness policy: monthly refresh, and nothing from before the current year counts as current."""

from datetime import datetime, timezone

from irs_research_server.services.freshness import catalog_freshness, is_fresh, stale_reason

NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)
CURRENT_PDF = "https://www.irs.gov/pub/irs-pdf/f1040.pdf"
PRIOR_PDF = "https://www.irs.gov/pub/irs-prior/f1040--2023.pdf"


def _days_ago(n: int) -> datetime:
    return datetime.fromordinal(NOW.toordinal() - n).replace(tzinfo=timezone.utc)


def test_recent_fetch_is_fresh():
    assert stale_reason(_days_ago(10), NOW, 30) is None


def test_older_than_a_month_is_stale():
    assert "days old" in stale_reason(_days_ago(31), NOW, 30)


def test_fetch_from_previous_year_is_stale_even_if_recent():
    jan_1 = datetime(2027, 1, 1, tzinfo=timezone.utc)
    dec_31 = datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc)
    assert "before the current year 2027" in stale_reason(dec_31, jan_1, 30)


def test_prior_year_pdfs_never_expire():
    assert is_fresh(PRIOR_PDF, datetime(2020, 1, 1, tzinfo=timezone.utc), NOW, 30)
    assert not is_fresh(CURRENT_PDF, datetime(2020, 1, 1, tzinfo=timezone.utc), NOW, 30)


def test_catalog_status_levels():
    assert catalog_freshness(_days_ago(5).isoformat(), NOW, 30)["status"] == "fresh"
    assert catalog_freshness(_days_ago(45).isoformat(), NOW, 30)["status"] == "refresh_recommended"
    assert catalog_freshness("2025-12-15T00:00:00+00:00", NOW, 30)["status"] == "outdated"
    assert catalog_freshness(None, NOW, 30)["status"] == "unknown"


def test_sqlite_timestamp_format_is_accepted():
    assert catalog_freshness("2026-11-20 08:00:00", NOW, 30)["status"] == "fresh"
