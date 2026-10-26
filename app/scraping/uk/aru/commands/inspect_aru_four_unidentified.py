import json
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from app.models import Course

from app.scraping.uk.aru.commands.inspect_aru_remaining_17 import Command as EvidenceCollector


TARGETS = {
    "C_UK003_18": {
        "source_url": (
            "https://www.aru.ac.uk/study/college-courses/"
            "subject-areas/veterinary-nursing"
        ),
        "purpose": "qualification_level_review",
        "note": (
            "Inspect the published qualification levels. "
            "A college diploma or apprenticeship must not replace "
            "the stored BSc automatically."
        ),
    },
    "C_UK003_41": {
        "source_url": (
            "https://www.aru.ac.uk/news/"
            "aru-courses-given-seal-of-quality-by-society"
        ),
        "purpose": "historical_course_identity_only",
        "note": (
            "Historical evidence for Marine Biology with Biodiversity "
            "and Conservation. This does not verify a current intake "
            "or international fee."
        ),
    },
    "C_UK003_43": {
        "source_url": (
            "https://www.aru.ac.uk/study/undergraduate/"
            "ecology-and-conservation"
        ),
        "purpose": "former_course_url_review",
        "note": (
            "Capture the former course URL and any redirect. "
            "A subject listing does not verify a course-specific "
            "intake or tuition fee."
        ),
    },
    "C_UK003_64": {
        "source_url": (
            "https://www.aru.ac.uk/study/undergraduate/computing"
        ),
        "purpose": "explicit_replacement_candidate",
        "note": (
            "Investigate BSc (Hons) Computing at Peterborough. "
            "Do not treat this as a confirmed rename of the stored "
            "Cambridge Computing and Information Systems record."
        ),
    },
}


class Command(EvidenceCollector):
    help = (
        "Collect evidence for four unidentified ARU routes. "
        "No mappings or database changes are applied."
    )

    def handle(self, *args, **options):
        courses = list(
            Course.objects.filter(
                university_id="UK003",
                course_id__in=TARGETS,
            )
            .order_by("course_id")
            .values()
        )

        found = {
            course["course_id"]: course
            for course in courses
        }

        missing = sorted(set(TARGETS) - set(found))

        if missing:
            raise CommandError(
                "Missing database courses: " + ", ".join(missing)
            )

        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")

        folder = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aru"
            / f"four-unidentified-{stamp}"
        )
        folder.mkdir(parents=True, exist_ok=True)

        report = {
            "generated_at": timezone.now().isoformat(),
            "university_id": "UK003",
            "database_changes": False,
            "mappings_approved": False,
            "courses": [],
            "pages": [],
            "errors": [],
        }

        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )

        with requests.Session() as session:
            session.headers["User-Agent"] = (
                "TuitionCompareSourceInspector/1.0"
            )
            session.mount(
                "https://",
                HTTPAdapter(max_retries=retry),
            )

            for index, (course_id, target) in enumerate(
                TARGETS.items(),
                start=1,
            ):
                url = target["source_url"]

                entry = {
                    "database_course": found[course_id],
                    "candidate_url": url,
                    "purpose": target["purpose"],
                    "note": target["note"],
                    "mapping_approved": False,
                    "status": "collection_pending",
                }

                report["courses"].append(entry)
                self.stdout.write(f"Reading {course_id}: {url}")

                try:
                    page = self.fetch_page(
                        session,
                        url,
                        folder,
                        index,
                    )

                    report["pages"].append(page)

                    entry["status"] = (
                        "redirect_requires_review"
                        if page["redirect_review_required"]
                        else "evidence_collected"
                    )

                except (
                    requests.RequestException,
                    OSError,
                    ValueError,
                ) as exc:
                    entry["status"] = "collection_failed"

                    report["errors"].append({
                        "course_id": course_id,
                        "requested_url": url,
                        "reason": str(exc),
                    })

                    self.stderr.write(
                        f"{course_id}: collection issue: {exc}"
                    )

        report["summary"] = {
            "database_courses": len(courses),
            "pages_collected": len(report["pages"]),
            "redirected_pages": sum(
                page["redirect_review_required"]
                for page in report["pages"]
            ),
            "collection_errors": len(report["errors"]),
        }

        output = folder / "four-route-evidence.json"

        output.write_text(
            json.dumps(
                report,
                cls=DjangoJSONEncoder,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        self.stdout.write(
            json.dumps(report["summary"], indent=2)
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Evidence collected only. No replacements accepted. "
            "No database records changed."
        )