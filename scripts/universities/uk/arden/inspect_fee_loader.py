"""Collect Arden's public fee-loader JavaScript. No database changes."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup


ROOT = next(
    parent for parent in Path(__file__).resolve().parents
    if (parent / "manage.py").is_file()
)
HOSTS = {"arden.ac.uk", "www.arden.ac.uk"}


def main():
    base = ROOT / "logs" / "arden"
    folders = [
        folder for folder in base.glob("course-evidence-*")
        if folder.is_dir()
        and (folder / "course-evidence.json").is_file()
    ]
    if not folders:
        raise ValueError("No collected Arden course evidence found.")

    evidence_folder = max(folders, key=lambda path: path.name)
    evidence = json.loads(
        (evidence_folder / "course-evidence.json").read_text(
            encoding="utf-8"
        )
    )

    page = next(
        item for item in evidence["pages"]
        if item["source_url"].rstrip("/").endswith(
            "/ba-hons-business-management"
        )
    )

    html_path = evidence_folder / Path(page["html_file"]).name
    soup = BeautifulSoup(
        html_path.read_bytes(), "html.parser"
    )

    urls = sorted({
        urljoin(page["source_url"], script["src"])
        for script in soup.find_all("script", src=True)
        if "/sites/default/files/js/" in script["src"]
    })
    if not urls:
        raise ValueError("No Arden JavaScript bundles found.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = base / f"fee-loader-{stamp}"
    output.mkdir(parents=True)

    report = {
        "source_course_url": page["source_url"],
        "database_changes": False,
        "files": [],
        "errors": [],
    }

    with requests.Session() as session:
        for index, url in enumerate(urls, 1):
            print("Reading:", url, flush=True)
            try:
                # Do not follow redirects to unreviewed hosts.
                response = session.get(
                    url, timeout=(20, 60), allow_redirects=False
                )
                if response.is_redirect:
                    raise ValueError(
                        "JavaScript URL redirected; review required."
                    )
                response.raise_for_status()

                if urlsplit(response.url).hostname not in HOSTS:
                    raise ValueError("Unexpected source host.")

                content = response.content
                if b"<html" in content[:1000].lower():
                    raise ValueError(
                        "HTML received instead of JavaScript."
                    )

                filename = f"bundle-{index:02d}.js"
                (output / filename).write_bytes(content)

                report["files"].append({
                    "source_url": response.url,
                    "file": filename,
                    "sha256": hashlib.sha256(content).hexdigest(),
                })

            except (requests.RequestException, ValueError) as exc:
                report["errors"].append({
                    "source_url": url,
                    "error": str(exc),
                })
                print("REVIEW:", exc)

    (output / "report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    import shutil

    archive = shutil.make_archive(
        str(output), "zip", root_dir=output
    )
    print("\nJavaScript bundles collected:", len(report["files"]))
    print("Collection errors:", len(report["errors"]))
    print("Attach:", archive)
    print("No database records changed.")


if __name__ == "__main__":
    main()