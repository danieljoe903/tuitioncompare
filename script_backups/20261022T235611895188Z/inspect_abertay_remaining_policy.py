import argparse
import json
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT = Path(__file__).resolve().parent

URL = (
    "https://www.abertay.ac.uk/study-apply/"
    "money-fees-and-funding/tuition-fees-2026-2027/"
)

PAGE_TITLE = "Tuition Fees (2026-2027)"

REQUIRED_SECTIONS = {
    "Undergraduate Tuition Fees - International Students 2026/27":
        "UNDERGRADUATE DEGREE COURSES",
    "Postgraduate Tuition Fees - International Students 2026/27":
        "POSTGRADUATE COURSES",
}


def text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")

    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_report(report):
    if report["source_url"] != URL:
        raise ValueError("Unexpected central fee source.")

    if report["page_title"] != PAGE_TITLE:
        raise ValueError("Unexpected policy title or academic year.")

    for label, first_header in REQUIRED_SECTIONS.items():
        sections = [
            section
            for section in report["sections"]
            if section["heading"] == label
        ]

        if len(sections) != 1:
            raise ValueError(
                f"Expected exactly one section: {label}"
            )

        tables = [
            table
            for table in sections[0]["tables"]
            if table
            and table[0]
            and table[0][0].casefold() == first_header.casefold()
        ]

        if len(tables) != 1:
            raise ValueError(
                f"Expected exactly one fee table: {label}"
            )

        if len(tables[0]) < 2:
            raise ValueError(f"Fee table is empty: {label}")


def collect_policy():
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
    )

    with requests.Session() as session:
        session.headers["User-Agent"] = (
            "TuitionCompareSourceInspector/1.0"
        )
        session.mount(
            "https://",
            HTTPAdapter(max_retries=retry),
        )

        response = session.get(URL, timeout=(30, 60))
        response.raise_for_status()

    if response.url.rstrip("/") != URL.rstrip("/"):
        raise ValueError(
            f"Unexpected redirect: {response.url}"
        )

    soup = BeautifulSoup(response.text, "html.parser")
    heading = soup.find("h1")

    if heading is None:
        raise ValueError("Central fee page has no title.")

    report = {
        "source_url": URL,
        "page_title": text(heading),
        "database_changes": False,
        "sections": [],
    }

    seen = set()

    for heading in soup.find_all(["h2", "h3", "h4"]):
        label = text(heading)
        lower = label.casefold()

        if not (
            "undergraduate tuition fees" in lower
            or "postgraduate tuition fees" in lower
            or "fee guarantee" in lower
        ):
            continue

        section = heading.find_parent("div", class_="e-item")

        if section is None:
            section = heading.find_parent(
                "div", class_="container"
            )

        if section is None:
            section = heading.parent

        content = text(section)
        fingerprint = (label, content)

        if fingerprint in seen:
            continue

        seen.add(fingerprint)
        tables = []

        for table in section.find_all("table"):
            rows = []

            for row in table.find_all("tr"):
                cells = row.find_all(["th", "td"])

                if cells:
                    rows.append([
                        text(cell) for cell in cells
                    ])

            if rows:
                tables.append(rows)

        report["sections"].append({
            "heading": label,
            "text": content,
            "tables": tables,
            "links": [
                {
                    "text": text(link),
                    "href": link["href"],
                }
                for link in section.find_all("a", href=True)
            ],
        })

    validate_report(report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / 'data/universities/uk/abertay/abertay_remaining_policy.json',
    )
    args = parser.parse_args()

    report = collect_policy()
    write_json(args.output, report)

    print("Page:", report["page_title"])
    print("Sections collected:", len(report["sections"]))

    for section in report["sections"]:
        print(
            "-",
            section["heading"],
            f"({len(section['tables'])} tables)",
        )

    print("\nSaved to:", args.output)
    print("No database records changed.")


if __name__ == "__main__":
    main()