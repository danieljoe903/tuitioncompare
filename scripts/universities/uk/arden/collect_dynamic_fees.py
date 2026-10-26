"""Collect country-specific Arden fee evidence. No database changes."""

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT = next(
    parent for parent in Path(__file__).resolve().parents
    if (parent / "manage.py").is_file()
)
ENDPOINT = "https://arden.ac.uk/fees/course-page"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--countries",
        nargs="+",
        default=["Nigeria", "Ghana"],
        help="Country labels from Arden's published fee selector.",
    )
    args = parser.parse_args()

    base = ROOT / "logs" / "arden"
    folders = [
        folder for folder in base.glob("course-evidence-*")
        if folder.is_dir()
        and (folder / "course-evidence.json").is_file()
    ]
    if not folders:
        raise ValueError("No Arden course evidence folder found.")

    evidence_folder = max(folders, key=lambda path: path.name)
    evidence = json.loads(
        (evidence_folder / "course-evidence.json").read_text(
            encoding="utf-8"
        )
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = base / f"dynamic-fees-{stamp}"
    output.mkdir(parents=True)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "university_id": "UK004",
        "source_evidence": str(evidence_folder),
        "countries_requested": args.countries,
        "database_changes": False,
        "mappings_approved": False,
        "responses": [],
        "skipped_pages": [],
        "errors": [],
    }

    def save():
        (output / "dynamic-fees.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    session = requests.Session()
    session.headers["User-Agent"] = (
        "UniversityTuitionCompare/1.0 source evidence collector"
    )
    session.mount(
        "https://",
        HTTPAdapter(max_retries=Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )),
    )

    try:
        for page in evidence["pages"]:
            html_path = evidence_folder / Path(page["html_file"]).name
            soup = BeautifulSoup(html_path.read_bytes(), "html.parser")

            selector = soup.select_one(
                'select[name="fees_country_id"]'
            )
            values = {}
            for field in (
                "fees_award_id",
                "fees_course_id",
                "fees_awarding_body",
            ):
                element = soup.find("input", attrs={"name": field})
                values[field] = (
                    element.get("value", "") if element else ""
                )

            if selector is None or not all(values.values()):
                report["skipped_pages"].append({
                    "source_url": page["source_url"],
                    "reason": "No complete published course-fee selector.",
                })
                save()
                continue

            countries = {
                " ".join(option.get_text(" ", strip=True).split()).casefold():
                    option.get("value", "")
                for option in selector.find_all("option")
            }

            for country in args.countries:
                country_id = countries.get(
                    " ".join(country.split()).casefold()
                )
                if not country_id:
                    report["errors"].append({
                        "source_url": page["source_url"],
                        "country": country,
                        "error": "Country not found in published selector.",
                    })
                    save()
                    continue

                params = {
                    "country_id": country_id,
                    "award_id": values["fees_award_id"],
                    "awarding_body": values["fees_awarding_body"],
                    "course_id": values["fees_course_id"],
                }
                print(
                    f"Reading {country}: {page['heading']} "
                    f"[{params['course_id']}]",
                    flush=True,
                )

                try:
                    response = session.get(
                        ENDPOINT,
                        params=params,
                        timeout=(20, 60),
                        allow_redirects=False,
                    )
                    if response.is_redirect:
                        raise ValueError(
                            "Unexpected redirect; response needs review."
                        )
                    response.raise_for_status()

                    payload = response.json()
                    fragment = (
                        payload.get("data")
                        if isinstance(payload, dict) else None
                    )
                    if not isinstance(fragment, str) or not fragment.strip():
                        raise ValueError(
                            "Response contains no non-empty HTML data field."
                        )

                    number = len(report["responses"]) + 1
                    raw_name = f"response-{number:03d}.json"
                    html_name = f"fees-{number:03d}.html"

                    (output / raw_name).write_bytes(response.content)
                    (output / html_name).write_text(
                        fragment, encoding="utf-8"
                    )

                    fee_soup = BeautifulSoup(fragment, "html.parser")
                    for tag in fee_soup(["script", "style"]):
                        tag.decompose()

                    report["responses"].append({
                        "course_page": page["source_url"],
                        "course_heading": page["heading"],
                        "country": country,
                        "request_parameters": params,
                        "fee_source_url": response.url,
                        "raw_response_file": raw_name,
                        "html_file": html_name,
                        "response_sha256": hashlib.sha256(
                            response.content
                        ).hexdigest(),
                        "text": fee_soup.get_text(" ", strip=True),
                        "tables": [
                            [
                                [
                                    cell.get_text(" ", strip=True)
                                    for cell in row.find_all(["th", "td"])
                                ]
                                for row in table.find_all("tr")
                            ]
                            for table in fee_soup.find_all("table")
                        ],
                    })

                except (requests.RequestException, ValueError) as exc:
                    report["errors"].append({
                        "source_url": page["source_url"],
                        "country": country,
                        "request_parameters": params,
                        "error": str(exc),
                    })
                    print("  REVIEW:", exc, flush=True)

                save()

    finally:
        session.close()

    report["summary"] = {
        "course_pages_examined": len(evidence["pages"]),
        "fee_responses_collected": len(report["responses"]),
        "pages_without_fee_selector": len(report["skipped_pages"]),
        "collection_errors": len(report["errors"]),
    }
    save()

    archive = shutil.make_archive(
        str(output), "zip", root_dir=output
    )
    print(json.dumps(report["summary"], indent=2))
    print("Attach:", archive)
    print("Evidence only. No database records changed.")


if __name__ == "__main__":
    main()