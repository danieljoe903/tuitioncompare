"""Collect Arden course evidence without changing database records."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT = next(
    parent for parent in Path(__file__).resolve().parents
    if (parent / "manage.py").is_file()
)

ALLOWED_HOSTS = {"arden.ac.uk", "www.arden.ac.uk"}


def clean_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        return None
    return urlunsplit((
        parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""
    ))


def fetch(session, url):
    """Follow only redirects that stay on the official domain."""
    current = url
    redirects = []

    for _ in range(8):
        response = session.get(
            current, timeout=(20, 60), allow_redirects=False
        )

        if response.status_code in {301, 302, 303, 307, 308}:
            from urllib.parse import urljoin

            target = clean_url(urljoin(
                current, response.headers.get("Location", "")
            ))
            if not target or target == current:
                raise ValueError("Invalid or external redirect.")

            redirects.append({"from": current, "to": target})
            current = target
            continue

        response.raise_for_status()
        return response, redirects

    raise ValueError("Too many redirects.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()

    evidence = args.evidence
    if not evidence.is_absolute():
        evidence = ROOT / evidence

    original = json.loads(evidence.read_text(encoding="utf-8"))
    if original.get("university_id") != "UK004":
        raise ValueError("Expected Arden UK004 source evidence.")

    links = {}

    def add(url, label=""):
        url = clean_url(url)
        if not url:
            return

        path = urlsplit(url).path.lower()
        if not path.startswith("/our-courses/"):
            return
        if not any(
            part in path.split("/")
            for part in ("undergraduate", "postgraduate")
        ):
            return

        entry = links.setdefault(url, set())
        if label:
            entry.add(label)

    for page in original.get("pages", []):
        for link in page.get("links", []):
            add(link.get("href", ""), link.get("text", ""))

    for course in original.get("courses", []):
        for candidate in course.get("candidates", []):
            for label in candidate.get("labels", [""]):
                add(candidate.get("url", ""), label)

    if not links:
        raise ValueError("No course links found in the source report.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = ROOT / "logs" / "arden" / f"course-evidence-{stamp}"
    folder.mkdir(parents=True)

    session = requests.Session()
    session.headers["User-Agent"] = (
        "UniversityTuitionCompare/1.0 source evidence collector"
    )
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "university_id": "UK004",
        "source_review": str(evidence),
        "database_changes": False,
        "mappings_approved": False,
        "courses": original.get("courses", []),
        "pages": [],
        "errors": [],
    }
    output = folder / "course-evidence.json"

    try:
        for index, (url, labels) in enumerate(sorted(links.items()), 1):
            print(f"Reading {index}/{len(links)}: {url}", flush=True)

            try:
                response, redirects = fetch(session, url)
                html = response.content
                html_file = folder / f"page-{index:03d}.html"
                html_file.write_bytes(html)

                soup = BeautifulSoup(html, "html.parser")
                scripts = [
                    {
                        "type": script.get("type", ""),
                        "text": script.string or script.get_text(),
                    }
                    for script in soup.find_all("script")
                    if script.string or script.get_text()
                ]
                tables = [
                    [
                        [
                            cell.get_text(" ", strip=True)
                            for cell in row.find_all(["th", "td"])
                        ]
                        for row in table.find_all("tr")
                    ]
                    for table in soup.find_all("table")
                ]

                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()

                main = soup.find("main") or soup
                heading = main.find("h1")

                report["pages"].append({
                    "requested_url": url,
                    "source_url": response.url,
                    "redirects": redirects,
                    "catalogue_labels": sorted(labels),
                    "heading": (
                        heading.get_text(" ", strip=True)
                        if heading else ""
                    ),
                    "sha256": hashlib.sha256(html).hexdigest(),
                    "html_file": html_file.name,
                    "text": main.get_text(" ", strip=True),
                    "tables": tables,
                    "scripts": scripts,
                })

            except (requests.RequestException, ValueError) as exc:
                report["errors"].append({
                    "requested_url": url,
                    "error": str(exc),
                })
                print(f"  REVIEW: {exc}", flush=True)

            # Save after every page so completed work is retained.
            output.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    finally:
        session.close()

    summary = {
        "database_courses": len(report["courses"]),
        "candidate_urls_attempted": len(links),
        "pages_collected": len(report["pages"]),
        "collection_errors": len(report["errors"]),
    }
    report["summary"] = summary
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(summary, indent=2))
    print("Report:", output)
    print("Evidence only. No mappings approved or database records changed.")


if __name__ == "__main__":
    main()