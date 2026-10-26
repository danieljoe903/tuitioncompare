import json
from pathlib import Path

import requests
from bs4 import BeautifulSoup


URL = (
    "https://www.abertay.ac.uk/course-search/"
    "undergraduate/biomedical-science/"
)
ACADEMIC_YEAR = "2026/27"


def clean_text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def main():
    response = requests.get(
        URL,
        headers={
            "User-Agent": "TuitionCompareFeeInspector/1.0",
        },
        timeout=(10, 30),
    )
    response.raise_for_status()

    if response.url != URL:
        raise ValueError("Page redirected; inspect the new URL.")

    soup = BeautifulSoup(response.text, "html.parser")
    matches = []

    for table in soup.find_all("table"):
        first_row = table.find("tr")

        if first_row is None:
            continue

        headers = [
            clean_text(cell)
            for cell in first_row.find_all(["th", "td"])
        ]

        if (
            headers
            and headers[0].casefold() == "fee status"
            and ACADEMIC_YEAR in headers
        ):
            matches.append((table, headers))

    if len(matches) != 1:
        raise ValueError(
            f"Expected one fee table; found {len(matches)}."
        )

    table, headers = matches[0]

    if headers.count(ACADEMIC_YEAR) != 1:
        raise ValueError("Academic-year column is ambiguous.")

    year_column = headers.index(ACADEMIC_YEAR)
    categories = []

    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["th", "td"])

        if not cells:
            continue

        if len(cells) != len(headers):
            raise ValueError(
                "Fee row has an unexpected number of columns."
            )

        categories.append({
            "source_fee_category": clean_text(cells[0]),
            "raw_annual_fee": clean_text(cells[year_column]),
        })

    if not categories:
        raise ValueError("No fee categories extracted.")

    # Preserve nearby policy text for review.
    section = table.find_parent("div", class_="container")

    report = {
        "academic_year": ACADEMIC_YEAR,
        "currency": "GBP",
        "source_url": response.url,
        "categories": categories,
        "fee_policy_text": clean_text(section) if section else "",
        "database_changes": False,
    }

    output = (
        Path(__file__).resolve().parent
        / "abertay_fee_categories_preview.json"
    )
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(categories, indent=2, ensure_ascii=False))
    print(f"\nSaved to: {output}")
    print("No database records changed.")


if __name__ == "__main__":
    main()