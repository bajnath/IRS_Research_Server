import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://www.irs.gov"
CURRENT_INDEX_PATH = "/forms-instructions-and-publications"
PRIOR_INDEX_PATH = "/prior-year-forms-and-instructions"

# Matches IRS non-English & bilingual tags anywhere in product_number or title
NON_ENGLISH_TAG_RE = re.compile(
    r"\(\s*(?:"
    r"en-[a-z]{2}|"
    r"sp|es(?:-[a-z]{2,4})?|"
    r"zh(?:-(?:s|t|hans|hant|cn|tw))?|"
    r"vie|vi|kor|ko|rus|ru|hat|ht|pt(?:-[a-z]{2,4})?|"
    r"fr|ar|hi|ja|pl|tl|ur|bn|gu|km|lo|my|ne|pa|so|am|ti|de|fa|it|"
    r"(?:(?:simplified|traditional)\s+)?(?:spanish|chinese|vietnamese|korean|"
    r"russian|haitian\s+creole|portuguese|tagalog|polish|arabic|french|"
    r"hindi|japanese|urdu|bengali|gujarati|khmer|punjabi|somali)"
    r")(?:\s+version)?\s*\)",
    re.IGNORECASE,
)

# IRS-to-filer guidance documents + model trust/account templates (5305 series)
# IRS-to-filer guidance documents + model trust/account templates + FinCEN instruction forms
GUIDANCE_PREFIX_RE = re.compile(
    r"^(?:inst(?:ruction)?s?|publ?(?:ication)?s?|notice|rev\.?\s*(?:proc|rul)\.?|announcement|td)\b",
    re.IGNORECASE,
)
GUIDANCE_FORM_RE = re.compile(r"^(?:5305(?:-[A-Z]+)*|8160-T|FinCEN\d+A)\b", re.IGNORECASE)

# Filer-to-IRS applications, elections, extensions, authorizations, questionnaires, e-file sigs & claims
ADMIN_PROCEDURAL_RE = re.compile(
    r"^(?:"
    r"\d{5}|9\d{3}|"  # All 5-digit IRS forms (10xxx–15xxx) and 4-digit 9xxx questionnaires/appeals/enrollment
    r"23|56(?:-F)?|433(?:-[A-Z]+)?|434|637|656(?:-[A-Z]+)?|843|872-B|886-[A-Z-]+|"
    r"907|911|921(?:-A)?|928|952|1000|1023(?:-[A-Z]+)?|1024(?:-A)?|1028|1045|1115|1117|1122|1126|1127|1128|1138|1139|1310|1363|"
    r"2032|2063|2120|2159|2240|2350|2553|2587|2624|2678|2688|2758|2848|3115|3491|3881(?:-A)?|3911|4029|4070(?:-A)?|4219|4361|"
    r"4421|4422|4423|4461(?:-[A-Z]+)?|4466|4506(?:T-EZ|-[A-Z]+)?|4547|4669|4670|4768|4810|4875|4876-A|"
    r"5129|5213|5308|5434(?:-A)?|5558|5578|5646|5712(?:-A)?|5754|5768|6112|6118|6524|6729(?:-[A-Z]+)?|6744|6745|"
    r"7036|7204|7220|8023|8038-R|8050|8082|8233|8264|8274|8275(?:-R)?|8288-B|8302|8316|8328|8332|8379|"
    r"8453(?:-[A-Z]+)?|8498|8508(?:-I)?|8546|8554(?:-EP)?|8609(?:-A)?|8653|8654|8655|8691|8693|8703|"
    r"8716|8717(?:-A)?|8718|8734|8796-A|8802|8821|8822(?:-[A-Z]+)?|8832|8838|8840|8842|8843|8848|"
    r"8849|8850|8855|8857|8862|8867|8868|8869|8871|8875|8878(?:-A)?|8879(?:-[A-Z]+)?|8886(?:-T)?|8887|8888|8892|8893|8894|8898|"
    r"8905|8940|8942|8944|8945|8946|8948|8950|8951|8952|8957|8979|8980|8981|8982|8983|8984|8988|8989|"
    r"SS-4|SS-8|SS-16|W-5|W-7(?:[A-Z]+)?|W-11|W-12"
    r")\b",
    re.IGNORECASE,
)

# Information returns & third-party/foreign reporting
INFO_RETURN_RE = re.compile(
    r"^(?:"
    r"FinCEN\d+(?!A\b)|"
    r"W-2(?:G|AS|C|GU|VI)?|W-3(?:SS|PR|C)?|W-9S|W-10|W-13|"
    r"1042-[ST]|1094(?:-[A-Z]+)?|1095-[A-Z]+|1096|1097-[A-Z]+|1098(?:-[A-Z]+)?|1099(?:-[A-Z]+)?|"
    r"2439|3520-A|3921|3922|4782|4790|4849|5227|5471|5472|5498(?:-[A-Z]+)?|5499|5500\s*\(Schedule\s+SSA\)|6088|6497|"
    r"8027(?:-T)?|8038(?:-B|-G|-GC|-TC)?|8271|8281|8282|8288-[AC]|8300|8308|8329|8330|8390|"
    r"8596(?:-A)?|8610|8621(?!-)|8805|8806|8809(?:-[A-Z]+)?|8811|8823|8851|8854|8858|8865|8870|8872|8874-[AB]|"
    r"8891|8899|8901|8918|8921|8922|8925|8935(?:-T)?|8937|8955-SSA|8963|8966|8971|8973|8975"
    r")\b",
    re.IGNORECASE,
)

# Individual income, estate/gift, and personal credit/deduction returns
INDIVIDUAL_RETURN_RE = re.compile(
    r"^(?:"
    r"1040(?:-[A-Z0-9]+)?|4868|W-4(?:[A-Z]+)?|CT-2|"
    r"673|706(?:-[A-Z]+)?|708|709|712|942|965-A|1062|1101|1116|2106|2119|2210(?:-F)?|2440|2441|2555|3520(?!-A\b)|3903|"
    r"4137|4469|4563|4625|4726|4808|4831|4835|4852|4952|4970|4972|4977|5074|5329|5405|5495|5695|6251|7202|7206|8396|8606|8615|8689|"
    r"8803|8812|8814|8815|8818|8828|8829|8834|8836|8839|8841|8853|8859|8863|8880|8885|8889|"
    r"8913|8914|8915(?:-[A-Z]+)?|8917|8919|8930|8936|8938|8939|8949|8958|8959|8960|8962|8965|8995(?:-[A-Z]+)?"
    r")\b",
    re.IGNORECASE,
)

# Business, entity, trust, benefit-plan, excise, and employment returns
BUSINESS_RETURN_RE = re.compile(
    r"^(?:"
    r"1120(?:-[A-Z0-9]+)?|1065(?:-[A-Z]+)?|1041(?:-[A-Z]+)?|1042(?!-[ST]\b)|1066|"
    r"940(?:-[A-Z]+)?|941(?:-[A-Z]+)?|943|944|945|990(?:-[A-Z]+)?|"
    r"11-C|707|720(?:-[A-Z]+)?|730|851|926|965(?!-A\b)(?:-[A-Z]+)?|966|970|972|973|976|"
    r"1103|1118|1125-[AE]|2220|2290|2438|3800|3920|4626|4720|4848|"
    r"5300|5304-SIMPLE|5306(?:-A)?|5307|5309|5310(?:-A)?|5316|5330|5452|5500(?:-[A-Z/]+)?|5505|"
    r"5713|5735|5884(?:-[A-Z]+)?|6069|6197|6478|6627|6765|7004|"
    r"7200|7205|7207|7208|7210|7211|7213|7218|8038-(?:CP|T)|8288(?!-[ABC]\b)|8404|"
    r"8586|8611|8612|8613|8656|8697|8725|8752|"
    r"8804(?:-[A-Z]+)?|8810|8813|8816|8817|8820|8825|8826|8827|8830|8831|8835|8844|8845|8846|8847|8860|8861|8864|8873|8874(?!-[AB]\b)|8876|8881|8882|8884|8895|8896|"
    r"8900|8902|8903|8904|8906|8907|8908|8909|8910|8911|8912|8916(?:-A)?|8923|8924|8926|8927|8928|8931|"
    r"8932|8933|8941|8947|8964-[A-Z]+|8974|8978|8985(?:-V)?|8986|"
    r"8990|8991|8992|8993|8994|8996|CT-1X?|T\b"
    r")(?:\b|(?=\s*\())",
    re.IGNORECASE,
)

# True dual-entity tax forms & schedules used across individual and business returns
SHARED_FORM_RE = re.compile(
    r"^(?:"
    r"W-8(?:[A-Z-]+)?|W-9(?!S\b)|W-14|"
    r"172|461|982|3468|4136|4255|4562|4684|4797|6198|6252|6781|7203|7217|"
    r"8283(?:-[A-Z]+)?|8582(?:-[A-Z]+)?|8594|8621-A|8801|8819|8824|8833|8866|8883|8997"
    r")\b",
    re.IGNORECASE,
)

PARENT_INDIVIDUAL_RE = re.compile(r"\(Form 1040(?:-[A-Z]+)?\)", re.IGNORECASE)
PARENT_BUSINESS_RE = re.compile(r"\(Form (?:1120|1065|1041|990)(?:-[A-Z]+)?\)", re.IGNORECASE)

BUCKET_TAGS: dict[str, list[str]] = {
    "individual_return": ["individual"],
    "business_return": ["business"],
    "information_return": ["information"],
    "shared": ["individual", "business"],
    "administrative_and_procedural": ["procedural"],
    "additional_information": ["guidance"],
    "unclassified": [],
}

def is_non_english_variant(product_number: str, title: str) -> bool:
    """Return True if the product number or title contains a non-English or bilingual tag."""
    return bool(NON_ENGLISH_TAG_RE.search(product_number) or NON_ENGLISH_TAG_RE.search(title))


def extract_parent_form(product_number: str) -> str | None:
    """Map 'Inst 1040 (Schedule C)' or 'Form 1040 (Schedule C)' -> 'Form 1040'."""
    match = re.match(
        r"^(?:Form|Inst(?:ruction)?s?)\s+([A-Z0-9-]+)",
        product_number,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    canonical = f"Form {match.group(1).upper()}"
    return None if canonical.lower() == product_number.strip().lower() else canonical


def classify_document(product_number: str, title: str) -> tuple[str, list[str]]:
    """Route a document into a primary bucket and attach audience/purpose tags."""
    prod_clean = product_number.strip()

    if GUIDANCE_PREFIX_RE.match(prod_clean):
        bucket = "additional_information"
        return bucket, BUCKET_TAGS[bucket]

    form_code = re.sub(r"^form\s+", "", prod_clean, flags=re.IGNORECASE).strip()

    if GUIDANCE_FORM_RE.match(form_code):
        bucket = "additional_information"
    elif ADMIN_PROCEDURAL_RE.match(form_code):
        bucket = "administrative_and_procedural"
    elif INFO_RETURN_RE.match(form_code):
        bucket = "information_return"
    elif SHARED_FORM_RE.match(form_code):
        bucket = "shared"
    elif INDIVIDUAL_RETURN_RE.match(form_code) or PARENT_INDIVIDUAL_RE.search(prod_clean) or PARENT_INDIVIDUAL_RE.search(title):
        bucket = "individual_return"
    elif BUSINESS_RETURN_RE.match(form_code) or PARENT_BUSINESS_RE.search(prod_clean) or PARENT_BUSINESS_RE.search(title):
        bucket = "business_return"
    else:
        bucket = "unclassified"

    return bucket, BUCKET_TAGS[bucket]


def parse_product_table(html: str, source_type: str) -> list[tuple[str, dict]]:
    """Extract English-only rows as (bucket_name, record_dict) tuples."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    items: list[tuple[str, dict]] = []
    tbody = table.find("tbody") or table

    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        product_cell, title_cell, rev_cell = cells[:3]
        posted_cell = cells[3] if len(cells) >= 4 else None

        product_number = product_cell.get_text(" ", strip=True)
        title = title_cell.get_text(" ", strip=True)

        if is_non_english_variant(product_number, title):
            continue

        link = product_cell.find("a", href=True)
        bucket, tags = classify_document(product_number, title)

        record = {
            "product_number": product_number,
            "title": title,
            "tags": tags,
            "related_form": extract_parent_form(product_number),
            "revision_date": rev_cell.get_text(" ", strip=True),
            "posted_date": posted_cell.get_text(" ", strip=True) if posted_cell else "",
            "pdf_url": urljoin(BASE_URL, link["href"]) if link else None,
            "index_source": source_type,
        }
        items.append((bucket, record))

    return items


async def fetch_page(
    client: httpx.AsyncClient,
    path: str,
    page: int,
    find_query: str | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> tuple[bool, list[tuple[str, dict]]]:
    """Fetch and parse a single paginated index page."""
    params: dict[str, str | int] = {"page": page}
    if find_query:
        params["find"] = find_query

    async def _get() -> tuple[bool, list[tuple[str, dict]]]:
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        await asyncio.sleep(0.35)

        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        raw_row_count = len((table.find("tbody") or table).find_all("tr")) if table else 0
        if raw_row_count == 0:
            return False, []

        return True, parse_product_table(resp.text, source_type=path.strip("/"))

    if semaphore:
        async with semaphore:
            return await _get()
    return await _get()


async def scrape_irs_index(
    include_prior_years: bool = False,
    max_pages: int | None = None,
    find_query: str | None = None,
    concurrency: int = 3,
) -> dict[str, list[dict]]:
    """Crawl paginated IRS tables and group records directly by document category."""
    headers = {
        "User-Agent": "IRS-Research-MCP-CatalogBuilder/0.1 (academic/research indexing)",
        "Accept": "text/html,application/xhtml+xml",
    }
    semaphore = asyncio.Semaphore(concurrency)

    grouped: dict[str, list[dict]] = {bucket: [] for bucket in BUCKET_TAGS}
    seen_keys: set[tuple[str, str, str]] = set()

    paths = [CURRENT_INDEX_PATH]
    if include_prior_years:
        paths.append(PRIOR_INDEX_PATH)

    async with httpx.AsyncClient(
        base_url=BASE_URL,
        headers=headers,
        timeout=30.0,
        follow_redirects=False,
    ) as client:
        for path in paths:
            page = 0
            while True:
                if max_pages is not None and page >= max_pages:
                    break

                batch_end = page + concurrency
                if max_pages is not None:
                    batch_end = min(batch_end, max_pages)

                tasks = [
                    fetch_page(client, path, p, find_query=find_query, semaphore=semaphore)
                    for p in range(page, batch_end)
                ]
                results = await asyncio.gather(*tasks)

                stop = False
                for p_idx, (has_raw_rows, page_items) in zip(range(page, batch_end), results):
                    if not has_raw_rows:
                        stop = True
                        break

                    added = 0
                    for bucket, record in page_items:
                        dedup_key = (
                            record["product_number"],
                            record["revision_date"],
                            record["index_source"],
                        )
                        if dedup_key in seen_keys:
                            continue
                        seen_keys.add(dedup_key)
                        grouped[bucket].append(record)
                        added += 1

                    print(f"[{path}] page {p_idx}: kept {added} new English rows")

                if stop:
                    break
                page = batch_end

    return grouped


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape and group English IRS forms metadata.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/staging/irs_raw_index.json"),
        help="Output staging JSON path",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Max pages per index to fetch (omit for full crawl)",
    )
    parser.add_argument(
        "--find",
        type=str,
        default=None,
        help="Optional IRS search filter, e.g. 'Form 1040' or 'W-2'",
    )
    parser.add_argument(
        "--prior-years",
        action="store_true",
        help="Also crawl /prior-year-forms-and-instructions",
    )
    args = parser.parse_args()

    grouped = asyncio.run(
        scrape_irs_index(
            include_prior_years=args.prior_years,
            max_pages=args.max_pages,
            find_query=args.find,
        )
    )

    counts = {bucket: len(items) for bucket, items in grouped.items()}
    payload = {
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": counts,
        "total_records": sum(counts.values()),
        "documents": grouped,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved {payload['total_records']} records to {args.out}: {counts}")


if __name__ == "__main__":
    main()