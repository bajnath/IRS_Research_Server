# IRS Research MCP Server

A Model Context Protocol server for professional research into the structure, history, and taxonomy of IRS tax forms.

**Target audience:** Academics, journalists, law practitioners, and policy researchers who need to understand the IRS form landscape — not individuals preparing tax returns.

---

## What it does

| Capability | Description |
|---|---|
| **Form taxonomy** | Browse all 2,800+ IRS forms classified by category (Individual Return, Business Return, Information Return, etc.) and curated subject-matter topic tags (gambling, retirement, international, etc.) |
| **Revision history** | Retrieve every indexed revision of any form, from earliest introduction through the current year |
| **Data-flow graph** | Trace which forms feed data into which returns, with IRS-sourced mechanism text for every edge |
| **Line-level diff** | Compare two historical revisions of a form line-by-line; also surfaces verbatim "What's New" text from instruction PDFs |
| **Relationship graph** | Follow schedule attachments, supersedure lineage (e.g. Form 1040-EZ → Form 1040), and form split history (e.g. 1099-NEC from 1099-MISC) |

**Scope guardrail:** This server quotes IRS-sourced facts. It does not give tax advice, help with tax preparation, or interpret legal obligations.

---

## Tools

### `lookup_form`
Find an IRS form by product number or title keyword. Optionally filter by category and/or topic tags.

```
lookup_form(query="1099-NEC")
lookup_form(query="gambling", topic_tags=["gambling"])
lookup_form(query="", category="information_return", topic_tags=["retirement"], limit=20)
```

### `browse_forms_by_topic`
Enumerate every form in the catalog tagged with one or more subject-matter topic tags. The primary research entry point for exploring the taxonomy by area.

```
browse_forms_by_topic(topic_tags=["gambling"])
browse_forms_by_topic(topic_tags=["retirement"], category="individual_return")
browse_forms_by_topic(topic_tags=["international", "withholding"])
```

### `get_form_history`
Full revision history for any IRS form: every indexed revision date and PDF URL, the computed earliest revision (approximate introduction date), and optional notes from the IRS About page.

```
get_form_history(product_number="Form 1099-NEC")
get_form_history(product_number="Form 1040", include_about_page=False)
```

### `trace_form_lineage`
Walk the data-flow graph upstream and downstream from any form. Returns schedule attachments, supersedure/split lineage, and IRS-sourced data-flow mechanism text.

```
trace_form_lineage(product_number="Form W-2G")
trace_form_lineage(product_number="Form 1065 (Schedule K-1)", max_depth=2)
```

### `compare_form_revisions`
Download and compare two historical revisions of a form section-by-section (Part / Line, scoped by copy on multi-copy information returns). Returns added, removed, and modified sections with word-level change spans; changes that are only the tax-year rollover are listed separately in `year_only_changes`, while any other year change is reported as a real modification. Also surfaces verbatim "What's New" text from the corresponding instruction PDF.

Each response includes `parse_quality` (text layer present/sparse/missing, anchor count, duplicate anchors, header/footer lines removed), `warnings` to relay to the user (e.g. scanned PDFs with no text layer), and `limitations`. This is a text comparison, not a legally authoritative comparison: layout, check boxes, and formatting-only changes are not detected.

```
compare_form_revisions(product_number="Form 1099-NEC", rev_from="2022", rev_to="2024")
compare_form_revisions(product_number="Form 1040 (Schedule C)", rev_from="2019", rev_to="2023")
```

---

## Resources

| URI | Contents |
|---|---|
| `irs://meta/usage-policy` | Scope statement and guardrails — read this at session start |
| `irs://catalog/summary` | Catalog statistics: product counts, category breakdown, edge counts, topic tag vocabulary |
| `irs://catalog/categories` | The 7 document categories with definitions and example forms |
| `irs://catalog/topic-tags` | Full topic-tag vocabulary with descriptions and representative forms |
| `irs://form/{product_number}` | Complete catalog node for a specific form (citation-ready) |

---

## Prompts

| Prompt | Use |
|---|---|
| `research_form` | Deep-dive into a single form: overview, history, relationships, or all |
| `explore_topic` | Survey all forms related to a subject area (e.g. "gambling") |
| `trace_data_pipeline` | Trace the data chain from a source form to a target return |
| `form_change_history` | Chronological change history across revisions with verbatim What's New text |

---

## Topic Tag Vocabulary

Every IRS form in the catalog carries a `tags` list containing two kinds of tags:

| Tag kind | Purpose | Examples |
|---|---|---|
| **Audience tags** | The filer type this document applies to | `individual`, `business`, `information`, `procedural`, `guidance`, `retired` |
| **Topic tags** | The subject-matter domain of the form | `gambling`, `retirement`, `international`, … |

Tags are always combined in the same `tags` field. Use topic tags as filter values in `lookup_form` and `browse_forms_by_topic`. Audience tags can be used with the `category` parameter instead.

**Single source of truth:** [`data/rules/tag_registry.json`](data/rules/tag_registry.json) defines every audience and topic tag. The catalog build fails if a keyword rule, curated override, or scraped record uses a tag that is not registered, and the registry is embedded in the catalog so the `irs://catalog/topic-tags` resource and the tools read exactly the same list. Tools reject unregistered tags with the valid vocabulary in the error.

Topic tags are **editorial metadata** assigned by this project — they are not classifications published by the IRS.

The supported topic tags (kept in sync with the registry by a test):

`core-return` · `retirement` · `healthcare` · `gambling` · `international` · `withholding` · `self-employment` · `employment-taxes` · `tips-overtime` · `real-estate` · `farming` · `investments` · `estate-gift` · `trusts-estates` · `exempt-organizations` · `charitable-contributions` · `excise-tax` · `credits` · `deductions` · `depreciation` · `partnerships` · `s-corporations` · `c-corporations` · `qualified-business-income` · `renewable-energy` · `education` · `debt-cancellation` · `government-payments` · `alternative-minimum-tax` · `estimated-tax` · `filing-procedures`

**How tags are assigned (three-tier pipeline in `scripts/build_catalog.py`):**

1. **Keyword inference** — patterns in the form title and product number trigger tags automatically (e.g. any title containing "gambling" gets the `gambling` tag).
2. **Curated overrides** — `data/rules/curated_edges.json` → `topic_tags` section lists tags for specific forms; these replace inferred tags.
3. **Inheritance** — a schedule with no tags inherits its parent form's tags.

Instructions and publications carry no topic tags. Instead, each one lists the forms and schedules it covers in `reverse_relationships.documented_by`.

All topic tags are merged into the same `tags` field as audience tags at catalog build time. See `irs://catalog/topic-tags` for definitions and representative forms.

### How the multi-tag filter works

Both `lookup_form` and `browse_forms_by_topic` accept a list of topic tags. The filter is an **AND** (intersection): a form must carry **all** listed tags to be included.

```
# Returns forms tagged with BOTH retirement AND credits (e.g. Form 8880)
browse_forms_by_topic(topic_tags=["retirement", "credits"])

# Returns gambling forms filed in the information_return category only
browse_forms_by_topic(topic_tags=["gambling"], category="information_return")
```

---

## Provenance

Every relationship, category, and tag in the catalog records **what supports it** in a `basis` field, so answers can distinguish IRS-published facts from project inference:

| `basis` | Meaning | Examples |
|---|---|---|
| `irs_naming` | Implied by the IRS's own product numbering or the forms listed in an IRS document title | `Form 1040 (Schedule 1)` attaches to Form 1040; `Instruction 8949` covers Form 8949 |
| `irs_text` | Machine-extracted from IRS instruction PDF text; includes the verbatim quote, page, and PDF URL | Candidate references in `referenced_forms` |
| `curated` | Entered by hand in `data/rules/curated_edges.json` | Data-flow edges, supersedure/split lineage, publication links, curated topic tags, manual products |
| `inferred` | Derived by a project rule; not stated by the IRS | Keyword topic tags, scraper categories/audience tags, inherited tags and instructions |

Where it is stored:
- Object edges (`flows_into`, `superseded_by`, `split_from`, `referenced_forms`, and their reverse edges) carry an inline `provenance` object.
- String edges (`attaches_to`, `related_instructions`, `related_publications`) are described in the node's `provenance` map, keyed by relationship then target. The node's category is under `provenance.category`.
- Tags are described in `tag_provenance`.

Curated edges also list `supporting_evidence`: matching IRS instruction sentences found by the reference extractor (15 of the 34 curated data-flow edges currently have one). To cite a source by hand, add a `citation` object (`document`, `page`, `pdf_url`, `quote`) to the entry in `curated_edges.json`.

---

## Data Pipeline

The catalog is built offline in two stages:

```
Stage 1 — Scrape
  uv run irs-scrape [--prior-years] [--find "Form 1099"]
  → data/staging/irs_raw_index.json

Stage 2 — Build catalog
  uv run irs-build-catalog [--prior data/staging/irs_prior_index.json]
  → data/catalog/irs_catalog.json  (2,816 products, typed relationship graph)

Stage 3 — Extract candidate references from instruction PDFs (optional)
  python scripts/extract_references.py [--only "Instruction 8949"] [--refresh]
  → data/rules/candidate_edges.json  (re-run Stage 2 to load them)
  Machine-extracted, unreviewed. Loaded into `referenced_forms` / `referenced_by`
  (relation: attaches_to | flows_into | mentions, with the verbatim instruction
  sentence as evidence) and never mixed with curated edges. Promote a reviewed
  edge by copying it into curated_edges.json.

Runtime — MCP server reads irs_catalog.json on startup (cached in memory).
Live PDF fetches (get_form_history, compare_form_revisions) are cached in
  data/cache/irs_history.db (SQLite).
```

The catalog in `data/catalog/irs_catalog.json` is committed to the repository so the server works out of the box. Re-run Stage 1 + 2 to pick up new IRS forms.

### Freshness policy

Refresh **monthly** (re-run Stage 1 + 2). Data is treated as current only if it was fetched within the last 30 days (`CACHE_MAX_AGE_DAYS`) **and** during the current calendar year — e.g. in 2026, the last update must be from 2026 so it covers tax year 2025 forms.

- **Catalog:** the scraper records `scraped_at`; the build stores it as `source_scraped_at`. `irs://catalog/summary` reports `catalog_freshness` as `fresh`, `refresh_recommended` (older than 30 days), `outdated` (scraped before Jan 1 of the current year), or `unknown`. `get_form_history` and `compare_form_revisions` include it whenever it is not `fresh`.
- **Runtime cache:** About pages and current/draft PDFs (which the IRS replaces in place) are re-fetched once stale. Prior-year PDFs under `/pub/irs-prior/` are immutable and never expire. If IRS.gov is unreachable, the stale copy is served with a `cache_warning`.

---

## Local Development

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/)

```bash
# 1. Clone and install
git clone https://github.com/bajnath/IRS_Research_Server
cd IRS_Research_Server
uv sync

# 2. Run the server (stdio mode — for use with Claude Desktop / Claude Code)
uv run python main.py

# 3. Run tests (fully offline: IRS.gov is simulated and each test uses a temp cache DB)
uv run pytest

# 4. Lint
uv run ruff check src/ tests/
```

### Connect with Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "irs-research": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/IRS_Research_Server", "python", "main.py"]
    }
  }
}
```

---

## Configuration

All settings are via environment variables (copy `.env.irs_research` to `.env`):

| Variable | Default | Purpose |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio` for local clients, `streamable-http` for HTTP deploys |
| `MCP_HOST` | `0.0.0.0` | Bind host (HTTP mode only) |
| `MCP_PORT` | `8000` | Bind port (HTTP mode only) |
| `CATALOG_PATH` | `data/catalog/irs_catalog.json` | Path to the enriched catalog |
| `DB_PATH` | `data/cache/irs_history.db` | SQLite cache for PDFs and About pages |
| `CACHE_MAX_AGE_DAYS` | `30` | Monthly refresh window for cached About pages, current/draft PDFs, and the catalog |
| `AUTH_MODE` | `none` | `jwt` to require bearer tokens over HTTP (see `deploy/README.md` for `JWT_*` settings) |
| `ALLOW_UNAUTHENTICATED_HTTP` | `false` | HTTP refuses to start without auth unless this is `true` (only behind a platform that enforces auth) |
| `ALLOWED_HOSTS` | *(empty)* | Public hostnames; enables Host/Origin validation |
| `MAX_REQUEST_BYTES` / `MAX_RESPONSE_BYTES` | `262144` / `1000000` | Request-body and tool-response size limits |
| `RATE_LIMIT_PER_SECOND` / `RATE_LIMIT_BURST` | `5` / `20` | Per-process token bucket |
| `MAX_PDF_BYTES` / `MAX_CONCURRENT_FETCHES` | `25000000` / `4` | Outbound IRS download limits |

On ephemeral container hosts where the project directory is read-only, set `DB_PATH=/tmp/irs_history.db`.

---

## Deployment

See `deploy/` for IBM Code Engine and Databricks Apps deployment kits.

For cloud.gov (Cloud Foundry):
```bash
cf login -a api.fr.cloud.gov --sso
cf target -o <your-org> -s <your-space>
cf push  # uses manifest.yaml
```

The `manifest.yaml` configures 512M memory and 2G disk — PyMuPDF and the catalog need more than Cloud Foundry's defaults.

---

## Adding Topic Tags

To assign existing tags to more forms:

1. Edit `data/rules/curated_edges.json` — add entries to the `"topic_tags"` section
2. Rebuild the catalog: `uv run irs-build-catalog --prior data/staging/irs_prior_index.json`
3. Restart the server (the catalog is loaded once at startup)

To introduce a **new** tag, first add it (with a description and representative forms) to `data/rules/tag_registry.json`, then add it to the README list above. The build rejects unregistered tags.

No code changes are needed.

IRS products that have no PDF in the IRS index (e.g. the online-only Form 990-N e-Postcard) can be added under `"manual_products"` in the same file.

---

## License

See [LICENSE](LICENSE).
