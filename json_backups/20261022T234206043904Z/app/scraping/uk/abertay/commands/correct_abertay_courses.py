import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import Course, Tuition, TuitionUpdateAudit


ALLOWED_IDS = {
    "C_UK001_10",
    "C_UK001_79",
    "C_UK001_44",
}


class Command(BaseCommand):
    help = "Preview or apply the three reviewed Abertay course corrections."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        apply = options["apply"]
        root = settings.BASE_DIR

        report = json.loads(
            (root / "abertay_corrections_preview.json").read_text(
                encoding="utf-8-sig"
            )
        )
        extraction = json.loads(
            (root / "abertay_courses_preview.json").read_text(
                encoding="utf-8-sig"
            )
        )

        proposals = report["proposals"]
        ids = [row["course_id"] for row in proposals]

        if (
            report["academic_year"] != "2026/27"
            or extraction["academic_year"] != "2026/27"
            or report["blocked"]
            or len(ids) != 3
            or set(ids) != ALLOWED_IDS
        ):
            raise CommandError("Unexpected correction report.")

        if apply and any(
            row.get("approved") is not True for row in proposals
        ):
            raise CommandError(
                'Set "approved": true for the three reviewed corrections.'
            )

        plans = []

        with transaction.atomic():
            for proposal in proposals:
                course_id = proposal["course_id"]

                sources = [
                    row for row in extraction["records"]
                    if row.get("status") == "extracted"
                    and row["source_url"] == proposal["source_url"]
                ]

                if len(sources) != 1:
                    raise CommandError(
                        f"{course_id}: source record is ambiguous."
                    )

                data = sources[0]["data"]
                expected_after = {
                    "title": data["title"],
                    "duration": data["duration"],
                    "course_url": proposal["source_url"],
                }

                if (
                    proposal["proposed"] != expected_after
                    or proposal["ucas_code"] != data["ucas_code"]
                ):
                    raise CommandError(
                        f"{course_id}: proposal differs from extraction."
                    )

                try:
                    course = Course.objects.select_for_update().get(
                        course_id=course_id,
                        university__name__iexact="Abertay University",
                        university__country="UK",
                        level="UG",
                        start_year=2026,
                    )
                except Course.DoesNotExist as exc:
                    raise CommandError(
                        f"{course_id}: course not found."
                    ) from exc

                current = {
                    key: getattr(course, key)
                    for key in expected_after
                }

                # Permit reruns after the correction is already applied.
                if current not in (
                    proposal["before"],
                    expected_after,
                ):
                    raise CommandError(
                        f"{course_id}: course changed since preview."
                    )

                if course.ucas_cao_code not in (
                    "",
                    proposal["ucas_code"],
                ):
                    raise CommandError(
                        f"{course_id}: existing UCAS code conflicts."
                    )

                tuitions = list(
                    Tuition.objects.select_for_update()
                    .filter(course=course)
                    .order_by("pk")
                )

                if not tuitions:
                    raise CommandError(
                        f"{course_id}: no tuition record for audit linkage."
                    )

                changed = (
                    current != expected_after
                    or course.ucas_cao_code != proposal["ucas_code"]
                )

                plans.append((
                    course,
                    tuitions,
                    current,
                    expected_after,
                    proposal["ucas_code"],
                    changed,
                ))

            updated = 0

            for course, tuitions, before, after, ucas_code, changed in plans:
                self.stdout.write(
                    f'{course.course_id}: '
                    f'{"correction" if changed else "unchanged"}'
                )

                if not changed:
                    continue

                updated += 1

                if not apply:
                    continue

                before_snapshot = {
                    **before,
                    "ucas_cao_code": course.ucas_cao_code,
                }
                after_snapshot = {
                    **after,
                    "ucas_cao_code": ucas_code,
                }

                for key, value in after.items():
                    setattr(course, key, value)

                course.ucas_cao_code = ucas_code
                course.save(update_fields=[
                    "title",
                    "duration",
                    "course_url",
                    "ucas_cao_code",
                    "updated_at",
                ])

                # One audit entry for each affected tuition relationship.
                for tuition in tuitions:
                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=course.course_id,
                        residency=tuition.residency,
                        before={
                            "action": "course_correction",
                            "course": before_snapshot,
                        },
                        after={
                            "action": "course_correction",
                            "course": after_snapshot,
                            "scholarship_review_pending": True,
                        },
                    )

        if apply:
            self.stdout.write(self.style.SUCCESS(
                f"Course corrections applied: {updated}. "
                "Tuition and scholarship values preserved."
            ))
        else:
            self.stdout.write(
                f"Preview: {updated} course corrections. "
                "No database records changed."
            )