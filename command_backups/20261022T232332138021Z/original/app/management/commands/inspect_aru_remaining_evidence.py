import hashlib
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from app.models import Course, TuitionSource


RELEVANT_SCRIPT = re.compile(
    r"fee|tuition|course|qualification|"
    r"startdate|start_date|duration|intake|"
    r"international|2026|2027|2028",
    flags=re.IGNORECASE,
)


class Command(BaseCommand):
    help = (
        "Inspect saved HTML, embedded data and script references "
        "for remaining ARU courses. No database changes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--report",
            help="Path to the original course-evidence.json.",
        )

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        logs = root / "logs" / "aru"

        if options["report"]:
            evidence_path = Path(options["report"])
            if not evidence_path.is_absolute():
                evidence_path = root / evidence_path
        else:
            available = list(
                logs.glob("courses-*/course-evidence.json")
            )

            if not available:
                raise CommandError(
                    "No course-evidence.json found. "
                    "Run inspect_aru_courses first."
                )

            evidence_path = max(
                available,
                key=lambda path: path.parent.name,
            )

        try:
            original = json.loads(
                evidence_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, ValueError) as exc:
            raise CommandError(
                f"Cannot read course evidence: {exc}"
            ) from exc

        if original.get("university_id") != "UK003":
            raise CommandError("Evidence is not for UK003.")

        courses = list(
            Course.objects.filter(
                university_id="UK003",
                is_active=True,
            )
            .order_by("course_id")
            .values(
                "course_id",
                "title",
                "level",
                "duration",
                "location",
                "start_month",
                "start_year",
                "course_url",
                "ucas_cao_code",
            )
        )

        # Identify already reviewed annual fees from saved metadata.
        # This is a workflow count, not a new source verification.
        reviewed = set()

        sources = TuitionSource.objects.filter(
            tuition__course__university_id="UK003",
            tuition__residency="INTL",
            fee_basis="annual_tuition",
        ).select_related("tuition")

        for source in sources:
            raw = source.raw_fees

            if (
                isinstance(raw, dict)
                and raw.get("verified") is True
                and source.academic_year
                and raw.get("academic_year") == source.academic_year
            ):
                reviewed.add(str(source.tuition.course_id))

        remaining = [
            course for course in courses
            if str(course["course_id"]) not in reviewed
        ]

        remaining_ids = {
            str(course["course_id"])
            for course in remaining
        }

        candidate_rows = {
            str(row["stored_course"]["course_id"]): row
            for row in original.get("course_candidates", [])
        }

        # Associate candidate URLs with remaining database records.
        targets = {}

        for course_id in remaining_ids:
            row = candidate_rows.get(course_id, {})

            for candidate in row.get("candidates", []):
                url = candidate.get("source_url")
                if url:
                    targets.setdefault(
                        url.rstrip("/"), set()
                    ).add(course_id)

        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        folder = logs / f"remaining-evidence-{stamp}"
        folder.mkdir(parents=True, exist_ok=False)

        report = {
            "generated_at": timezone.now().isoformat(),
            "university_id": "UK003",
            "database_changes": False,
            "mappings_approved": False,
            "original_evidence_report": str(
                evidence_path.resolve()
            ),
            "remaining_courses": remaining,
            "course_candidates": [
                candidate_rows[course_id]
                for course_id in sorted(remaining_ids)
                if course_id in candidate_rows
            ],
            "pages": [],
            "errors": [],
        }

        output = folder / "remaining-evidence.json"

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

        collected_urls = set()

        for page in original.get("pages", []):
            requested = page.get("requested_url", "").rstrip("/")
            ids = targets.get(requested)

            if not ids:
                continue

            collected_urls.add(requested)
            filename = page.get("html_file")

            if (
                not filename
                or Path(filename).name != filename
            ):
                report["errors"].append({
                    "source_url": requested,
                    "reason": "Missing or invalid HTML filename.",
                })
                continue

            html_path = evidence_path.parent / filename

            try:
                content = html_path.read_bytes()

                if hashlib.sha256(content).hexdigest() != page.get(
                    "sha256"
                ):
                    raise ValueError(
                        "Saved HTML hash differs from the evidence."
                    )

                soup = BeautifulSoup(content, "html.parser")

                embedded_scripts = []
                script_references = []

                for script in soup.find_all("script"):
                    if script.get("src"):
                        script_references.append(script["src"])
                        continue

                    value = script.get_text("", strip=False)
                    script_type = script.get("type", "")

                    if not value.strip():
                        continue

                    if (
                        "json" in script_type.casefold()
                        or RELEVANT_SCRIPT.search(value)
                    ):
                        item = {
                            "id": script.get("id"),
                            "type": script_type,
                            "text": value,
                        }

                        # Parse actual JSON only; never execute scripts.
                        if "json" in script_type.casefold():
                            try:
                                item["parsed_json"] = json.loads(value)
                            except ValueError:
                                item["json_parse_failed"] = True

                        embedded_scripts.append(item)

                data_attributes = []

                for element in soup.find_all(True):
                    attributes = {
                        key: value
                        for key, value in element.attrs.items()
                        if key.startswith("data-")
                    }

                    if attributes and RELEVANT_SCRIPT.search(
                        json.dumps(attributes, ensure_ascii=False)
                    ):
                        data_attributes.append({
                            "tag": element.name,
                            "id": element.get("id"),
                            "attributes": attributes,
                        })

                report["pages"].append({
                    "requested_url": page.get("requested_url"),
                    "source_url": page.get("source_url"),
                    "title": page.get("title"),
                    "sha256": page.get("sha256"),
                    "candidate_course_ids": sorted(ids),
                    "text": page.get("text"),
                    "tables": page.get("tables", []),
                    "links": page.get("links", []),
                    "embedded_scripts": embedded_scripts,
                    "script_references": sorted(
                        set(script_references)
                    ),
                    "data_attributes": data_attributes,
                })

                self.stdout.write(
                    f"Inspected: {requested}"
                )

            except (OSError, ValueError) as exc:
                report["errors"].append({
                    "source_url": requested,
                    "reason": str(exc),
                })

            save_report()

        for missing in sorted(set(targets) - collected_urls):
            report["errors"].append({
                "source_url": missing,
                "reason": "Candidate page is absent from collected evidence.",
            })

        no_candidates = [
            course["course_id"]
            for course in remaining
            if not candidate_rows.get(
                course["course_id"], {}
            ).get("candidates")
        ]

        report["summary"] = {
            "active_database_courses": len(courses),
            "courses_with_saved_verified_annual_metadata": sum(
                course["course_id"] in reviewed
                for course in courses
            ),
            "remaining_courses": len(remaining),
            "candidate_pages_inspected": len(report["pages"]),
            "pages_with_embedded_data": sum(
                bool(page["embedded_scripts"] or page["data_attributes"])
                for page in report["pages"]
            ),
            "courses_without_candidates": len(no_candidates),
            "collection_errors": len(report["errors"]),
        }
        report["course_ids_without_candidates"] = no_candidates

        save_report()

        self.stdout.write("")
        self.stdout.write(
            json.dumps(report["summary"], indent=2)
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Saved evidence inspected only. "
            "No mappings approved. No database records changed."
        )