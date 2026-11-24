"""Pure text-level parsing and diffing of IRS form PDFs (stdlib only, no I/O).

The parser works on extracted page text, not on visual layout or form fields,
so results are a text comparison - not a legally authoritative form comparison.
Each parse reports quality signals so callers can flag unreliable output.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter

# Bump when parse output changes so cached parses are not reused.
PARSER_VERSION = 3
# Below this many extracted characters per page the PDF is treated as having no usable text layer.
MIN_CHARS_PER_PAGE = 200
# Line numbers only increase, by at most this much; anything else is margin or cross-reference text
# (e.g. "114 may result in", "1a or 1d"). Numbering may restart only at a new Part or copy.
MAX_LINE_NUMBER_JUMP = 5
# At a Part/copy boundary (may_restart=True) allow any line number up to this value.
# IRS forms often start a new Part at a higher number (e.g. Part III begins at line 8),
# so the restart window must be wider than MAX_LINE_NUMBER_JUMP.
MAX_RESTART_LINE = 99

# Digit lookarounds instead of \b so years glued to text ("2022U.S.") still match.
YEAR_TOKEN_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
# Information returns repeat the form once per copy ("Copy B For Recipient").
COPY_RE = re.compile(r"\bCopy\s+([A-D]|[1-3])\b(?=\s+(?:For|To)\b)")
PART_RE = re.compile(r"^(Part\s+[IVX]+|Section\s+[A-Z])\b[.:)]?\s*(.*)$")
LINE_RE = re.compile(r"^(?:Line\s+)?(\d{1,3})([a-z]?)\b[.:)]?(?:\s+(\S.*))?$")
DOT_LEADER_RE = re.compile(r"(?:\s+\.){2,}|\.{3,}|\s\.(?=\s|$)")
FOOTER_RE = re.compile(
    r"Cat\.\s*No\.\s*\w+|Paperwork Reduction Act Notice"
    r"|^Page\s+\d+(?:\s+of\s+\d+)?$"
    r"|^Form\s+\S+\s+\(\d{4}\)(?:\s+Page\s+\d+)?$",
    re.IGNORECASE,
)

LIMITATIONS = [
    "Compares extracted text only; layout, field geometry, check boxes, and formatting-only changes are not detected.",
    "Scanned PDFs without a text layer produce little or no text (see parse_quality.text_layer).",
    "Margin notes and multi-column text can be attached to the nearest line anchor.",
    "Annual tax-year rollovers (the revision year and adjacent years) are reported in year_only_changes, "
    "not in modified; all other years are compared literally.",
    "This is a text comparison, not a legally authoritative comparison of the forms.",
]


def _digit_shape(line: str) -> str:
    return re.sub(r"\d+", "#", line)


def parse_form_text(pages: list[str]) -> dict:
    """Split extracted page text into {anchor: text} sections plus quality signals.

    Anchors are "Part II", "Part II > Line 6", or "Line 6" (before any Part). On
    multi-copy information returns, anchors are scoped by copy ("Copy B > Line 1").
    Duplicate anchors get " [2]", " [3]"... suffixes instead of overwriting.
    Text is kept verbatim (no year normalization); see compute_line_diff.
    """
    cleaned = [[" ".join(ln.split()) for ln in page.splitlines() if ln.strip()] for page in pages]
    page_copies = [m.group(1) if (m := COPY_RE.search(" ".join(lines))) else None for lines in cleaned]
    multi_copy = len({c for c in page_copies if c}) >= 2

    # Lines repeated at the top/bottom of most pages are running headers/footers.
    repeated: set[str] = set()
    if len(cleaned) >= 2:
        edge_counts: Counter[str] = Counter()
        for lines in cleaned:
            edge_counts.update(
                {_digit_shape(ln) for ln in lines[:3] + lines[-3:] if not (LINE_RE.match(ln) or PART_RE.match(ln))}
            )
        threshold = max(2, len(cleaned) / 2)
        repeated = {shape for shape, n in edge_counts.items() if n >= threshold}

    sections: dict[str, str] = {}
    duplicates: list[str] = []
    removed = 0
    part: str | None = None
    key = "header"
    buf: list[str] = []
    last_line_num = 0
    current_num = ""
    may_restart = False

    def flush() -> None:
        text = " ".join(t for t in buf if t).strip()
        if not text and key == "header":
            return
        final = key
        if final in sections:
            duplicates.append(key)
            n = 2
            while f"{key} [{n}]" in sections:
                n += 1
            final = f"{key} [{n}]"
        sections[final] = text

    for page_no, lines in enumerate(cleaned):
        if multi_copy and page_copies[page_no]:
            flush()
            part = f"Copy {page_copies[page_no]}"
            key, buf = part, []
            current_num, may_restart = "", True
        for line in lines:
            if FOOTER_RE.search(line) or _digit_shape(line) in repeated:
                removed += 1
                continue
            line = " ".join(DOT_LEADER_RE.sub(" ", line).split())
            if not line:
                continue
            m = PART_RE.match(line)
            if m:
                flush()
                label = " ".join(m.group(1).split())
                part = f"Copy {page_copies[page_no]} > {label}" if multi_copy and page_copies[page_no] else label
                key, buf = part, [m.group(2)]
                current_num, may_restart = "", True
                continue
            m = LINE_RE.match(line)
            n = int(m.group(1)) if m else 0
            in_sequence = last_line_num <= n <= last_line_num + MAX_LINE_NUMBER_JUMP
            restart = may_restart and 1 <= n <= MAX_RESTART_LINE
            if m and (in_sequence or restart):
                num = m.group(1) + m.group(2)
                text = m.group(3) or ""
                if not text and num == current_num:
                    continue  # the same number repeated in the amount column
                flush()
                last_line_num, current_num, may_restart = n, num, False
                # Drop the repeated line number in the amount column ("Add lines 1 and 2 . . . 3")
                text = re.sub(rf"\s+{re.escape(num)}$", "", text)
                key = f"{part} > Line {num}" if part else f"Line {num}"
                buf = [text]
                continue
            if current_num:
                # Amount-column line number at the end of a wrapped line
                line = re.sub(rf"\s+{re.escape(current_num)}$", "", line)
            buf.append(line)
    flush()

    total_chars = sum(len(p) for p in pages)
    per_page = total_chars / len(pages) if pages else 0
    anchors = [k for k in sections if k != "header"]
    if total_chars == 0:
        text_layer = "missing"
    elif per_page < MIN_CHARS_PER_PAGE:
        text_layer = "sparse"
    else:
        text_layer = "ok"

    return {
        "sections": sections,
        "quality": {
            "parser_version": PARSER_VERSION,
            "pages": len(pages),
            "text_chars": total_chars,
            "text_layer": text_layer,
            "anchors": len(anchors),
            "duplicate_anchors": sorted(set(duplicates)),
            "header_footer_lines_removed": removed,
        },
    }


def tax_year_of(revision_date: str) -> int | None:
    m = YEAR_TOKEN_RE.search(revision_date or "")
    return int(m.group(0)) if m else None


def _year_normalizer(tax_year: int | None):
    """Replace only the revision year and its neighbours, so other years stay comparable."""
    if not tax_year:
        return lambda s: s
    mapping = {str(tax_year + d): "<YEAR>" if d == 0 else f"<YEAR{d:+d}>" for d in (-1, 0, 1)}
    return lambda s: YEAR_TOKEN_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), s)


def word_changes(before: str, after: str) -> list[dict]:
    """Word-level change spans, e.g. [{"op": "replace", "before": "Other", "after": "Additional"}]."""
    a, b = before.split(), after.split()
    spans = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op != "equal":
            spans.append({"op": op, "before": " ".join(a[i1:i2]), "after": " ".join(b[j1:j2])})
    return spans


def compute_line_diff(
    old_sections: dict[str, str],
    new_sections: dict[str, str],
    old_year: int | None = None,
    new_year: int | None = None,
) -> dict:
    """Compare two parsed revisions by anchor.

    Returns:
      - added / removed: {anchor: text}
      - modified: {anchor: {before, after, changes}} for real text changes, where
        changes lists word-level spans that differ
      - year_only_changes: {anchor: {before, after}} where the only difference is the
        tax-year rollover (e.g. "During 2023" -> "During 2024")
    """
    norm_old, norm_new = _year_normalizer(old_year), _year_normalizer(new_year)
    old_keys, new_keys = set(old_sections), set(new_sections)
    added = {k: new_sections[k] for k in sorted(new_keys - old_keys)}
    removed = {k: old_sections[k] for k in sorted(old_keys - new_keys)}
    modified: dict[str, dict] = {}
    year_only: dict[str, dict] = {}
    for k in sorted(old_keys & new_keys):
        before, after = old_sections[k], new_sections[k]
        if before == after:
            continue
        if norm_old(before) == norm_new(after):
            year_only[k] = {"before": before, "after": after}
        else:
            modified[k] = {"before": before, "after": after, "changes": word_changes(before, after)}
    return {"added": added, "removed": removed, "modified": modified, "year_only_changes": year_only}


def quality_warnings(label: str, quality: dict) -> list[str]:
    """Human-readable warnings for one parsed revision."""
    warnings = []
    if quality["text_layer"] == "unreadable":
        warnings.append(f"{label}: PDF could not be opened (malformed or unsupported); no text was compared.")
    elif quality["text_layer"] == "missing":
        warnings.append(f"{label}: PDF has no extractable text (likely a scan); comparison is not meaningful.")
    elif quality["text_layer"] == "sparse":
        warnings.append(f"{label}: very little extractable text (likely a partial scan); results are incomplete.")
    if quality["anchors"] == 0 and quality["text_layer"] in ("ok", "sparse"):
        warnings.append(f"{label}: no line/part anchors recognized; all text is under 'header'.")
    if quality["duplicate_anchors"]:
        warnings.append(
            f"{label}: {len(quality['duplicate_anchors'])} anchor(s) appear more than once and were "
            "suffixed [2], [3]...; matching of those lines across revisions is less reliable."
        )
    return warnings
