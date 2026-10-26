import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import requests

from extract_abertay_course import (
    MissingFeeTable,
    parse_course_html,
)
from abertay_fee_fallback import (
    FEE_SOURCE_URL,
    parse_missing_course_fee,
)


ROOT = Path(__file__).resolve().parent
INPUT_FILE = ROOT / "abertay_course_urls.json"


def is_course_url(url):
    parsed = urlsplit(url)

    return (
        parsed.scheme == "https"
        and parsed.netloc == "www.abertay.ac.uk"
        and parsed.path.startswith("/course-search/undergraduate/")
        and parsed.path != "/course-search/undergraduate/"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--academic-year", default="2026/27")
    args = parser.parse_args()

    if args.limit < 1:
        parser.error("--limit must be greater than zero")

    year = args.academic_year

    if (
        not re.fullmatch(r"\d{4}/\d{2}", year)
        or int(year[5:]) != (int(year[:4]) + 1) % 100
    ):
        parser.error(
            "--academic-year must be consecutive, for example 2026/27"
        )

    catalogue = json.loads(
        INPUT_FILE.read_text(encoding="utf-8-sig")
    )

    records = []
    seen_urls = set()
    central_fee_html = None

    with requests.Session() as session:
        retries = Retry(
            total=3,
            connect=3,
            read=3,
            other=3,
            status=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )

        session.mount(
            "https://",
            HTTPAdapter(max_retries=retries),
        )
        session.headers.update({
            "User-Agent": "TuitionCompareSourceInspector/1.0"
        })

        for item in catalogue[:args.limit]:
            url = item["course_url"]

            if url in seen_urls:
                continue

            seen_urls.add(url)

            record = {
                "catalogue_title": item["catalogue_title"],
                "source_url": url,
            }

            print("Reading:", url)

            try:
                if not is_course_url(url):
                    raise ValueError("Unexpected course URL")

                response = session.get(
                    url,
                    timeout=(30, 60),
                )
                response.raise_for_status()

                record["response_url"] = response.url

                if not is_course_url(response.url):
                    raise ValueError(
                        "Response redirected outside a course page"
                    )

                # Inner try handles the specific missing-table case.
                # The outer try catches failures from either parser.
                try:
                    extracted = parse_course_html(
                        response.text,
                        year,
                    )

                    extracted["fee_source_url"] = response.url
                    extracted["fee_method"] = "course_page"

                except MissingFeeTable:
                    # Download the central fee page once per run.
                    if central_fee_html is None:
                        central_response = session.get(
                            FEE_SOURCE_URL,
                            timeout=(10, 30),
                        )
                        central_response.raise_for_status()

                        if central_response.url != FEE_SOURCE_URL:
                            raise ValueError(
                                "Central fee page redirected; "
                                "inspect before continuing."
                            )

                        central_fee_html = central_response.text

                    # Run for EVERY missing-table course.
                    extracted = parse_missing_course_fee(
                        course_html=response.text,
                        course_url=response.url,
                        academic_year=year,
                        central_fee_html=central_fee_html,
                    )

                # Both extraction paths finish here.
                record["status"] = "extracted"
                record["data"] = extracted

                print(
                    "Extracted:",
                    extracted["title"],
                    "—",
                    extracted["currency"],
                    extracted["annual_tuition"],
                    f'({extracted["fee_method"]})',
                )

            except requests.RequestException as exc:
                record["status"] = "request_failed"
                record["error"] = str(exc)
                print("Request failed:", exc)

            except ValueError as exc:
                record["status"] = "needs_review"
                record["error"] = str(exc)
                print("Needs review:", exc)

            records.append(record)

            # Space out requests to course pages.
            time.sleep(1)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "academic_year": year,
        "processed": len(records),
        "extracted": sum(
            row["status"] == "extracted" for row in records
        ),
        "needs_review": sum(
            row["status"] == "needs_review" for row in records
        ),
        "request_failed": sum(
            row["status"] == "request_failed" for row in records
        ),
        "records": records,
    }

    output_file = ROOT / "abertay_courses_preview.json"

    output_file.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\nProcessed:", report["processed"])
    print("Extracted:", report["extracted"])
    print("Needs review:", report["needs_review"])
    print("Request failures:", report["request_failed"])
    print("Preview saved to:", output_file)


if __name__ == "__main__":
    main()