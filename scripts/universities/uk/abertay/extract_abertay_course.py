

# Locate the project root after this script has been relocated.
import sys as _script_sys
from pathlib import Path as _ScriptPath

_script_locations = []
if "__file__" in globals():
    _script_locations.extend(_ScriptPath(__file__).resolve().parents)
_script_locations.extend([
    _ScriptPath.cwd(),
    *_ScriptPath.cwd().parents,
])

_SCRIPT_ROOT = next(
    (
        location for location in _script_locations
        if (location / "manage.py").is_file()
        and (location / "app").is_dir()
    ),
    None,
)
if _SCRIPT_ROOT is None:
    raise RuntimeError(
        "Cannot locate the Academy project. Run from its main folder."
    )

if str(_SCRIPT_ROOT) not in _script_sys.path:
    _script_sys.path.insert(0, str(_SCRIPT_ROOT))

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup


class MissingFeeTable(ValueError):
    """The page has no fee table for the requested academic year."""
def extract_fee_categories(html, academic_year):
    """Extract published categories without assigning EU/HOME status."""

    soup = BeautifulSoup(html, "html.parser")
    matching_tables = []

    def text(element):
        return " ".join(element.get_text(" ", strip=True).split())

    for table in soup.find_all("table"):
        first_row = table.find("tr")

        if first_row is None:
            continue

        headers = [
            text(cell)
            for cell in first_row.find_all(["th", "td"])
        ]

        if (
            headers
            and headers[0].casefold() == "fee status"
            and academic_year in headers
        ):
            matching_tables.append((table, headers))

    if not matching_tables:
        raise MissingFeeTable(
            f"No fee table found for {academic_year}."
        )

    if len(matching_tables) != 1:
        raise ValueError("Multiple matching fee tables found.")

    table, headers = matching_tables[0]

    if headers.count(academic_year) != 1:
        raise ValueError("Academic-year column is ambiguous.")

    year_column = headers.index(academic_year)
    categories = []
    seen_labels = set()

    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["th", "td"])

        if not cells:
            continue

        if len(cells) != len(headers):
            raise ValueError(
                "Fee row has an unexpected number of columns."
            )

        label = text(cells[0])
        raw_fee = text(cells[year_column])

        if not label or label.casefold() in seen_labels:
            raise ValueError("Empty or duplicate fee category.")

        seen_labels.add(label.casefold())

        # Preserve annotations rather than discarding them.
        match = re.fullmatch(
            r"£\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d{2})?"
            r"\s*(\*{0,3})\s*(\(.*\))?",
            raw_fee,
        )

        categories.append({
            "source_fee_category": label,
            "academic_year": academic_year,
            "currency": "GBP",
            "raw_annual_fee": raw_fee,
            "annual_tuition": (
                match.group(1).replace(",", "")
                + (match.group(2) or ".00")
                if match else None
            ),
            "footnote_marker": match.group(3) if match else "",
            "annotation": (match.group(4) or "") if match else "",
            "amount_requires_review": match is None,
        })

    if not categories:
        raise ValueError("No fee categories extracted.")

    return categories

def parse_course_html(html, academic_year):
    """Extract one course and its annual international tuition."""

    soup = BeautifulSoup(html, "html.parser")

    heading = soup.find("h1")

    if heading is None:
        raise ValueError("Course title was not found.")

    title = heading.get_text(" ", strip=True)

    if not title:
        raise ValueError("Course title is empty.")

    # Extract labelled course details.
    details = {}

    for item in soup.select("div.e-item"):
        label = item.find("h4")
        value = item.find("span", recursive=False)

        if label is not None and value is not None:
            details[label.get_text(" ", strip=True)] = (
                value.get_text(" ", strip=True)
            )

    matching_tables = []

    for table in soup.find_all("table"):
        first_row = table.find("tr")

        if first_row is None:
            continue

        headers = [
            cell.get_text(" ", strip=True)
            for cell in first_row.find_all(["th", "td"])
        ]

        if (
            headers
            and headers[0].casefold() == "fee status"
            and academic_year in headers
        ):
            matching_tables.append((table, headers))

    if not matching_tables:
        raise MissingFeeTable(
            f"No fee table found for {academic_year}."
        )

    if len(matching_tables) > 1:
        raise ValueError(
            "Multiple matching fee tables found; "
            "manual review required."
        )

    fee_table, headers = matching_tables[0]

    if headers.count(academic_year) != 1:
        raise ValueError("Academic-year column is ambiguous.")

    year_column = headers.index(academic_year)

    international_values = []

    for row in fee_table.find_all("tr")[1:]:
        cells = [
            cell.get_text(" ", strip=True)
            for cell in row.find_all(["th", "td"])
        ]

        if not cells:
            continue

        if cells[0].casefold() == "international":
            if len(cells) <= year_column:
                raise ValueError(
                    "International fee row is missing a column."
                )

            international_values.append(cells[year_column])

    if len(international_values) != 1:
        raise ValueError(
            "Expected exactly one international fee value."
        )

    raw_fee = international_values[0]

    # Accept fees such as £16,950, £16950.00 or £16,950*.
    # Preserve footnotes so the importer can require review.
    match = re.fullmatch(
        r"£\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d{2})?\s*(\*{0,3})",
        raw_fee,
    )

    if match is None:
        raise ValueError(
            f"Fee requires manual review: {raw_fee!r}"
        )

    amount = (
        match.group(1).replace(",", "")
        + (match.group(2) or ".00")
    )

    return {
        "title": title,
        "start_month": details.get("Start Date"),
        "duration": details.get("Duration"),
        "award_title": details.get("Award Title"),
        "ucas_code": details.get("UCAS Code"),
        "academic_year": academic_year,
        "fee_status": "International",
        "currency": "GBP",
        "raw_fee": raw_fee,
        "annual_tuition": amount,
        "footnote_marker": match.group(3),
        "fee_categories": extract_fee_categories(html, academic_year),
    }


def identify_award(title):
    """Return an award only when exactly one type is present."""

    awards = {
        value.upper()
        for value in re.findall(
            r"\b(BSc|BA|BEng|MEng|LLB|MSc|MA|MRes)\b",
            title or "",
            flags=re.IGNORECASE,
        )
    }

    return next(iter(awards)) if len(awards) == 1 else None


def review_course_match(extracted, existing):
    """Flag award and URL issues; this does not approve a match."""

    flags = []

    extracted_award = identify_award(
        extracted.get("award_title") or extracted.get("title")
    )
    existing_award = identify_award(existing["title"])

    if extracted_award and existing_award:
        if extracted_award != existing_award:
            flags.append({
                "code": "award_mismatch",
                "message": (
                    f"Stored award is {existing_award}; "
                    f"source award is {extracted_award}."
                ),
            })
    else:
        flags.append({
            "code": "award_unconfirmed",
            "message": (
                "Could not identify a single award type "
                "for both records."
            ),
        })

    stored_url = (existing.get("course_url") or "").strip()

    if not stored_url:
        flags.append({
            "code": "missing_course_url",
            "message": "Stored course URL is missing.",
        })
    else:
        normalised_url = (
            stored_url
            if "://" in stored_url
            else f"https://{stored_url}"
        )

        parsed = urlsplit(normalised_url)

        if (
            parsed.scheme not in ("http", "https")
            or parsed.hostname not in (
                "www.abertay.ac.uk",
                "abertay.ac.uk",
            )
        ):
            flags.append({
                "code": "unexpected_course_url",
                "message": "Stored URL is not an Abertay web URL.",
            })

        elif not parsed.path.rstrip("/"):
            flags.append({
                "code": "homepage_url",
                "message": (
                    "Stored URL points to a homepage; "
                    "it cannot establish course identity."
                ),
            })

    return {
        "course_id": existing["course_id"],
        "stored_title": existing["title"],
        "source_title": extracted["title"],
        "match_status": (
            "needs_review" if flags else "candidate_only"
        ),
        "flags": flags,
    }


def main():
    html_file = (
        _SCRIPT_ROOT / "abertay_course.html"
    )

    html = html_file.read_text(encoding="utf-8")

    record = parse_course_html(
        html,
        academic_year="2026/27",
    )

    print(json.dumps(record, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()