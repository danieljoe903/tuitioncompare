import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import (
    Course,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
)


class Command(BaseCommand):
    help = "Import mapped Abertay fees from the extracted JSON preview."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Save changes. Without this flag, preview only.",
        )

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        apply = options["apply"]

        try:
            mapping = json.loads(
                (root / 'data/universities/uk/abertay/abertay_course_mapping.json').read_text(
                    encoding="utf-8-sig"
                )
            )
            extraction = json.loads(
                (root / 'data/universities/uk/abertay/abertay_courses_preview.json').read_text(
                    encoding="utf-8-sig"
                )
            )
        except (OSError, ValueError) as exc:
            raise CommandError(f"Cannot read input files: {exc}") from exc

        year = mapping["academic_year"]

        if extraction["academic_year"] != year:
            raise CommandError("Mapping and extraction years differ.")

        sources = {}
        for record in extraction["records"]:
            if record.get("status") == "extracted":
                sources.setdefault(
                    record["source_url"], []
                ).append(record)

        entries = mapping["mappings"]

        if not entries:
            raise CommandError("Mapping contains no courses.")

        ids = [entry["course_id"] for entry in entries]
        urls = [entry["source_url"] for entry in entries]

        if len(ids) != len(set(ids)) or len(urls) != len(set(urls)):
            raise CommandError("Duplicate course IDs or source URLs.")

        if apply and any(
            entry.get("approved") is not True for entry in entries
        ):
            raise CommandError(
                "Mappings are still drafts. Review them and set "
                '"approved": true before using --apply.'
            )

        plans = []

        # Validate every record before writing any changes.
        with transaction.atomic():
            for entry in entries:
                course_id = entry["course_id"]
                source_url = entry["source_url"]

                parsed = urlsplit(source_url)
                if (
                    parsed.scheme != "https"
                    or parsed.netloc != "www.abertay.ac.uk"
                    or not parsed.path.startswith(
                        "/course-search/undergraduate/"
                    )
                ):
                    raise CommandError(
                        f"{course_id}: unexpected source URL."
                    )

                records = sources.get(source_url, [])
                if len(records) != 1:
                    raise CommandError(
                        f"{course_id}: expected one extracted record."
                    )

                data = records[0]["data"]
                categories = data.get("fee_categories", [])

                international_rows = [
                    row for row in categories
                    if row.get("source_fee_category") == "International"
                ]

                if len(international_rows) != 1:
                    raise CommandError(
                        f"{course_id}: expected one international fee category."
                    )

                international_fee = international_rows[0]

                if (
                    international_fee.get("amount_requires_review") is not False
                    or international_fee.get("footnote_marker")
                    or international_fee.get("academic_year") != year
                    or international_fee.get("currency") != "GBP"
                    or international_fee.get("annual_tuition")
                    != data.get("annual_tuition")
                ):
                    raise CommandError(
                        f"{course_id}: international fee category needs review."
                    )
                fee_source_url = data.get("fee_source_url",source_url)
                central_fee_url = (
                    "https://www.abertay.ac.uk/study-apply/"
                    "money-fees-and-funding/tuition-fees-2026-2027/"
                )

                if fee_source_url != source_url:
                    if(
                        fee_source_url != central_fee_url
                        or data.get("fee_method") != "reviewed_central_default"
                        or course_id not in {"C_UK001_37", "C_UK001_41"}
                        or year != "2026/27"
                    ):
                        raise CommandError( f"{course_id}: unexpected fee source.")

                    
                expected_source = {
                    "title": entry["source_title"],
                    "award_title": entry["source_award"],
                    "duration": entry["source_duration"],
                    "academic_year": year,
                    "fee_status": "International",
                    "currency": "GBP",
                }

                if any(
                    data.get(key) != value
                    for key, value in expected_source.items()
                ):
                    raise CommandError(
                        f"{course_id}: source differs from mapping."
                    )

                if data.get("footnote_marker"):
                    raise CommandError(
                        f"{course_id}: fee footnote needs review."
                    )

                courses = list(
                    Course.objects.select_for_update().filter(
                        course_id=course_id,
                        university__name__iexact="Abertay University",
                        university__country="UK",
                    )[:2]
                )

                if len(courses) != 1:
                    raise CommandError(
                        f"{course_id}: expected one database course."
                    )

                course = courses[0]

                expected_course = {
                    "title": entry["expected_stored_title"],
                    "duration": entry["expected_stored_duration"],
                    "level": entry["expected_stored_level"],
                }

                if any(
                    getattr(course, key) != value
                    for key, value in expected_course.items()
                ):
                    raise CommandError(
                        f"{course_id}: database course changed."
                    )

                if course.start_year != int(year[:4]):
                    raise CommandError(
                        f"{course_id}: intake year differs."
                    )

                tuitions = list(
                    Tuition.objects.select_for_update().filter(
                        course=course,
                        residency="INTL",
                    )[:2]
                )

                if len(tuitions) != 1:
                    raise CommandError(
                        f"{course_id}: expected one INTL tuition."
                    )

                tuition = tuitions[0]

                if tuition.currency != "GBP":
                    raise CommandError(
                        f"{course_id}: currency differs."
                    )

                try:
                    fee = Decimal(data["annual_tuition"])
                except (InvalidOperation, TypeError, ValueError) as exc:
                    raise CommandError(
                        f"{course_id}: invalid fee."
                    ) from exc

                if (
                    not fee.is_finite()
                    or fee <= 0
                    or fee != fee.quantize(Decimal("0.01"))
                ):
                    raise CommandError(
                        f"{course_id}: invalid fee amount."
                    )

                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )

                if source and (
                    source.academic_year != year
                    or source.source_url != fee_source_url
                    or source.fee_basis != "annual_tuition"
                ):
                    raise CommandError(
                        f"{course_id}: existing source metadata "
                        "conflicts; review before replacing it."
                    )

                metadata = {
                    "academic_year": year,
                    "source_url": fee_source_url,
                    "source_course_code": data.get("ucas_code", ""),
                    "source_course_name": data["title"],
                    "fee_basis": "annual_tuition",
                    "student_contribution": None,
                    "published_total": None,
                    "raw_fees": {
                        "fee_status": data["fee_status"],
                        "currency": data["currency"],
                        "annual_tuition": data["raw_fee"],
                        "award_title": data["award_title"],
                        "duration": data["duration"],
                    },
                }
                if data.get("fee_method") == "reviewed_central_default":
                    metadata["raw_fees"].update({
                        "course_source_url": source_url,
                        "fee_method": data["fee_method"],
                        "fee_rule": data["fee_rule"],
                    })
                metadata["raw_fees"]["fee_categories"] = categories
                metadata["raw_fees"]["source_fee_category"] = "International"
                if (
                    len(metadata["source_course_code"]) > 100
                    or len(metadata["source_course_name"]) > 255
                    or len(source_url) > 500
                    or len(source_url)
                    > Course._meta.get_field("course_url").max_length
                ):
                    raise CommandError(
                        f"{course_id}: value exceeds a field length."
                    )

                before_metadata = None
                if source:
                    before_metadata = {
                        key: getattr(source, key)
                        for key in metadata
                    }
                    for key in (
                        "student_contribution",
                        "published_total",
                    ):
                        value = before_metadata[key]
                        if value is not None:
                            before_metadata[key] = str(value)

                before = {
                    "tuition_fee": str(tuition.tuition_fee),
                    "currency": tuition.currency,
                    "course_url": course.course_url,
                    "source_metadata": before_metadata,
                }
                after = {
                    "tuition_fee": str(fee),
                    "currency": tuition.currency,
                    "course_url": course.course_url,
                    "source_metadata": metadata,
                }

                # Compare amounts numerically: 16950.0 equals 16950.00.
                changed = (
                    tuition.tuition_fee != fee
                    or before_metadata != metadata
                )

                plans.append({
                    "course": course,
                    "tuition": tuition,
                    "fee": fee,
                    "metadata": metadata,
                    "before": before,
                    "after": after,
                    "changed": changed,
                })

            changed_count = 0

            for plan in plans:
                course = plan["course"]
                tuition = plan["tuition"]

                self.stdout.write(
                    f"{course.course_id}: GBP "
                    f'{plan["before"]["tuition_fee"]} '
                    f'-> {plan["after"]["tuition_fee"]}'
                    f' — {"update" if plan["changed"] else "unchanged"}'
                )

                if not plan["changed"]:
                    continue

                changed_count += 1

                if not apply:
                    continue

                tuition.tuition_fee = plan["fee"]
                tuition.save(update_fields=["tuition_fee"])

                TuitionSource.objects.update_or_create(
                    tuition=tuition,
                    defaults=plan["metadata"],
                )

                TuitionUpdateAudit.objects.create(
                    tuition=tuition,
                    course_identifier=course.course_id,
                    residency=tuition.residency,
                    before=plan["before"],
                    after=plan["after"],
                )

        if apply:
            self.stdout.write(self.style.SUCCESS(
                f"Import complete: {changed_count} updated, "
                f"{len(plans) - changed_count} unchanged."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Preview: {changed_count} proposed updates. "
                "No database records changed."
            ))