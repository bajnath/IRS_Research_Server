"""Guard against deployment targets shipping without the prebuilt catalog."""

from pathlib import Path

from irs_research_server.config import settings

ROOT = Path(__file__).resolve().parent.parent


def test_catalog_exists_at_configured_path():
    assert settings.catalog_path.exists(), f"Catalog missing at {settings.catalog_path}"


def test_dockerfile_copies_catalog():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY data/catalog/irs_catalog.json ./data/catalog/irs_catalog.json" in dockerfile


def test_local_toolkit_stages_catalog_and_correct_package():
    script = (ROOT / "deploy/ibm/local-mcp-toolkit/register-toolkit.sh").read_text(encoding="utf-8")
    assert 'PACKAGE_NAME="${PACKAGE_NAME:-irs_research_server}"' in script
    assert "data/catalog/irs_catalog.json" in script
