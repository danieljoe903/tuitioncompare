import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import Course


COURSE_ID = "C_UK001_70"

OLD_TITLE = "MA Technical Art and Visual Effects"
NEW_TITLE = "MSc Technical Art and Visual Effects"

SOURCE_URL = (
    "https://www.abertay.ac.uk/course-search/"
    "postgraduate-taught/technical-art-and-visual-effects/"
)

FIELDS = (
    "title",
    "level",
    "duration",
    "start_month",
    "start_year",
    "course_url",
    "is_active",
)


def snapshot(course):
    return {
        field: getattr(course, field)
        for field in FIELDS
    }


class Command(BaseCommand):
    help = (
        "Preview or apply evidence-backed Abertay "
        "course-record corrections."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)

        # Reuse the source preview already collected.
        preview_path = root / "abertay_remaining_preview.json"

        try:
            preview = json.loads(
                preview_path.read_text(encoding="utf-8-sig")
            )

            if preview["academic_year"] != "2026/27":
                raise ValueError(
                    "Expected the 2026/27 source preview."
                )

            matches = [
                record
                for record in preview["postgraduate"]
                if record.get("kind") == "postgraduate_taught"
                and record.get("status") == "extracted"
                and record.get("source_url", "").rstrip("/")
                == SOURCE_URL.rstrip("/")
            ]

            if len(matches) != 1:
                raise ValueError(
                    "Expected one Technical Art source record."
                )

            data = matches[0]["data"]

            if data.get("title") != NEW_TITLE:
                raise ValueError(
                    "Source title does not match the reviewed MSc."
                )

            if (
                data.get("academic_year") != "2026/27"
                or data.get("currency") != "GBP"
                or Decimal(str(data["annual_tuition"]))
                != Decimal("19950.00")
                or data.get("footnote_marker")
            ):
                raise ValueError(
                    "Source fee evidence requires review."
                )

            if "12 months" not in (
                data.get("duration") or ""
            ).casefold():
                raise ValueError(
                    "Source full-time duration requires review."
                )

        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            ArithmeticError,
        ) as exc:
            raise CommandError(
                f"Source validation failed: {exc}"
            ) from exc

        log_dir = root / "logs" / "abertay-course-corrections"
        log_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        report_path = log_dir / f"courses-{timestamp}.json"

        with transaction.atomic():
            try:
                course = (
                    Course.objects.select_for_update()
                    .get(
                        course_id=COURSE_ID,
                        university__name__iexact=(
                            "Abertay University"
                        ),
                        university__country="UK",
                    )
                )
            except Course.DoesNotExist as exc:
                raise CommandError(
                    "Expected Abertay course was not found."
                ) from exc

            if course.title not in {OLD_TITLE, NEW_TITLE}:
                raise CommandError(
                    "Database title changed since review."
                )

            if (
                course.level not in {"PG", "PGT"}
                or course.start_year != 2026
                or course.start_month.strip().casefold()
                != "september"
                or course.duration.strip().casefold()
                not in {"1 year", "1 years"}
            ):
                raise CommandError(
                    "Stored level, intake or duration "
                    "does not match the reviewed record."
                )

            duplicates = list(
                Course.objects.filter(
                    university_id=course.university_id,
                    title__iexact=NEW_TITLE,
                    start_year=course.start_year,
                    start_month__iexact=course.start_month,
                    level__in=["PG", "PGT"],
                )
                .exclude(pk=course.pk)
                .values_list("course_id", flat=True)
            )

            if duplicates:
                raise CommandError(
                    "Possible duplicate records require review: "
                    + ", ".join(duplicates)
                )

            before = snapshot(course)
            after = dict(before)

            # The stored one-year duration already agrees
            # with the published 12-month full-time duration.
            after["title"] = NEW_TITLE
            after["course_url"] = SOURCE_URL

            changed_fields = [
                field
                for field in FIELDS
                if before[field] != after[field]
            ]

            report = {
                "mode": (
                    "apply" if options["apply"] else "preview"
                ),
                "database_changes": False,
                "committed": False,
                "course_id": course.course_id,
                "evidence": {
                    "course_url": SOURCE_URL,
                    "academic_year": "2026/27",
                    "source_preview": preview_path.name,
                    "source_title": data["title"],
                    "source_duration": data["duration"],
                    "published_international_fee": (
                        data["annual_tuition"]
                    ),
                },
                "before": before,
                "after": after,
                "changed_fields": changed_fields,
                "tuition_changes": False,
                "scholarship_changes": False,
                "deposit_changes": False,
            }

            # Save the plan before applying changes.
            report_path.write_text(
                json.dumps(
                    report,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            if options["apply"] and changed_fields:
                for field in changed_fields:
                    setattr(course, field, after[field])

                course.save(
                    update_fields=[
                        *changed_fields,
                        "updated_at",
                    ]
                )

        if options["apply"]:
            report["database_changes"] = bool(changed_fields)
            report["committed"] = True

            report_path.write_text(
                json.dumps(
                    report,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        self.stdout.write(
            json.dumps(
                {
                    "course_id": COURSE_ID,
                    "before": before,
                    "after": after,
                    "changed_fields": changed_fields,
                },
                indent=2,
                ensure_ascii=False,
            )
        )

        self.stdout.write(f"\nReport: {report_path}")

        if options["apply"]:
            self.stdout.write(
                f"Applied corrections: {len(changed_fields)} fields."
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )