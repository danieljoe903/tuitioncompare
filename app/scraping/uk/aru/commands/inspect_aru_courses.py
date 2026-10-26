import json
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import CommandError
from django.utils import timezone

from app.scraping.uk.aru.commands.inspect_aru_sources import Command as SourceInspector, official_url


class Command(SourceInspector):
    help = (
        "Collect ARU candidate course pages for identity, "
        "intake and tuition review. No database changes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--report",
            help=(
                "Path to source-review.json. "
                "Defaults to the latest ARU source report."
            ),
        )

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        log_root = root / "logs" / "aru"

        if options["report"]:
            source = Path(options["report"])
            if not source.is_absolute():
                source = root / source
        else:
            available = list(
                log_root.glob("*/source-review.json")
            )

            if not available:
                raise CommandError(
                    "Run inspect_aru_sources first."
                )

            source = max(
                available,
                key=lambda path: path.parent.name,
            )

        try:
            data = json.loads(
                source.read_text(encoding="utf-8-sig")
            )
        except (OSError, ValueError) as exc:
            raise CommandError(
                f"Cannot read source report: {exc}"
            ) from exc

        if data.get("university_id") != "UK003":
            raise CommandError(
                "The source report is not for UK003."
            )

        if data.get("errors"):
            raise CommandError(
                "Resolve the source report's collection "
                "errors before continuing."
            )

        rows = data.get("course_candidates")

        if not isinstance(rows, list) or not rows:
            raise CommandError(
                "No course candidates found in the report."
            )

        # Collect all suggested pages, once per URL.
        # Candidates remain unapproved regardless of score.
        targets = {}

        for row in rows:
            course = row.get("stored_course", {})
            course_id = course.get("course_id")

            if not course_id:
                raise CommandError(
                    "A candidate row has no course ID."
                )

            for candidate in row.get("candidates", []):
                url = candidate.get("source_url")

                if (
                    not isinstance(url, str)
                    or official_url(url) != url
                ):
                    raise CommandError(
                        f"Invalid candidate URL for {course_id}: "
                        f"{url!r}"
                    )

                targets.setdefault(url, set()).add(course_id)

        stamp = timezone.now().strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        folder = log_root / f"courses-{stamp}"
        folder.mkdir(parents=True, exist_ok=False)

        report = {
            "university_id": "UK003",
            "generated_at": timezone.now().isoformat(),
            "source_report": str(source.resolve()),
            "database_changes": False,
            "mappings_approved": False,
            "course_candidates": rows,
            "pages": [],
            "errors": [],
        }

        output = folder / "course-evidence.json"

        def save_report():
            temporary = output.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    report,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            temporary.replace(output)

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[
                429, 500, 502, 503, 504
            ],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )

        save_report()

        with requests.Session() as session:
            session.headers.update({
                "User-Agent": (
                    "TuitionCompareSourceInspector/1.0"
                ),
            })
            session.mount(
                "https://",
                HTTPAdapter(max_retries=retry),
            )

            for number, url in enumerate(
                sorted(targets), start=1
            ):
                self.stdout.write(
                    f"\nPage {number}/{len(targets)}"
                )

                try:
                    response, soup = self.get_page(
                        session, url
                    )

                    evidence = self.evidence(
                        response, soup
                    )

                    evidence.update({
                        "requested_url": url,
                        "candidate_course_ids": sorted(
                            targets[url]
                        ),
                        "mapping_approved": False,
                    })

                    html_file = (
                        folder / f"page-{number:04}.html"
                    )
                    html_file.write_bytes(
                        response.content
                    )

                    evidence["html_file"] = (
                        html_file.name
                    )

                    report["pages"].append(evidence)

                except (
                    requests.RequestException,
                    ValueError,
                ) as exc:
                    report["errors"].append({
                        "source_url": url,
                        "candidate_course_ids": sorted(
                            targets[url]
                        ),
                        "reason": str(exc),
                    })

                    self.stderr.write(
                        f"Needs review: {url}: {exc}"
                    )

                # Preserve completed evidence if interrupted.
                save_report()

        report["summary"] = {
            "database_courses": len(rows),
            "candidate_pages": len(targets),
            "pages_collected": len(report["pages"]),
            "pages_needing_review": len(
                report["errors"]
            ),
            "courses_without_candidates": sum(
                not row.get("candidates")
                for row in rows
            ),
        }
        save_report()

        self.stdout.write("")
        self.stdout.write(
            json.dumps(report["summary"], indent=2)
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "No mappings approved. "
            "No database records changed."
        )