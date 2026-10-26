import hashlib
import json
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from app.models import (
    Course,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
)


COURSE_ID = "C_UK003_64"

SOURCE_URL = (
    "https://www.aru.ac.uk/study/undergraduate/computing"
)

NEW_COURSE = {
    "title": "BSc (Hons) Computing",
    "level": "UG",
    "location": "Peterborough Campus",
    "duration": "3 Years",
    "start_month": "September",
    "start_year": 2027,
    "intake_months": "September",
    "ucas_cao_code": "I118",
    "course_url": SOURCE_URL,
}

FEE_NOTE = (
    "Annual international tuition for the standard three-year "
    "BSc Computing route at Peterborough, September 2027 / 2027/28. "
    "Foundation and placement routes are excluded. This is an "
    "explicit replacement of the stored Cambridge Computing and "
    "Information Systems record, not a verified course rename."
)

HELD_REVIEWS = {
    "C_UK003_18": (
        "The captured Writtle veterinary listing describes college "
        "qualifications. It does not verify the stored BSc route."
    ),
    "C_UK003_41": (
        "Historical Marine Biology evidence does not verify a "
        "current intake or applicable international fee."
    ),
    "C_UK003_43": (
        "The former Ecology course URL redirects to a subject "
        "listing. A current course-specific intake and fee "
        "remain unverified."
    ),
}


def money(value):
    amount = Decimal(str(value).replace(",", ""))

    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid monetary amount.")

    return format(amount.quantize(Decimal("0.01")), ".2f")


class Command(BaseCommand):
    help = (
        "Preview or apply an explicit replacement with the "
        "reviewed Peterborough BSc Computing route."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--evidence",
            help="Path to four-route-evidence.json.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Save the reviewed replacement and tuition.",
        )
        parser.add_argument(
            "--accept-replacements",
            action="store_true",
            help="Accept the explicit course and campus replacement.",
        )

    def find_evidence(self, supplied):
        if supplied:
            path = Path(supplied)

            if not path.is_absolute():
                path = Path(settings.BASE_DIR) / path

            if not path.is_file():
                raise CommandError(
                    f"Evidence file not found: {path}"
                )

            return path.resolve()

        folder = Path(settings.BASE_DIR) / "logs" / "aru"

        paths = list(
            folder.glob(
                "four-unidentified-*/four-route-evidence.json"
            )
        )

        if not paths:
            raise CommandError(
                "No four-route evidence found. Supply --evidence."
            )

        return max(
            paths,
            key=lambda path: path.parent.name,
        ).resolve()

    def verify_evidence(self, path):
        report = json.loads(
            path.read_text(encoding="utf-8")
        )

        if report.get("university_id") != "UK003":
            raise ValueError("Evidence is not for ARU.")

        entries = [
            entry
            for entry in report.get("courses", [])
            if entry.get("database_course", {}).get("course_id")
            == COURSE_ID
            and entry.get("candidate_url", "").rstrip("/")
            == SOURCE_URL
        ]

        if len(entries) != 1:
            raise ValueError(
                "Expected one Computing candidate entry."
            )

        pages = [
            page
            for page in report.get("pages", [])
            if page.get("requested_url", "").rstrip("/")
            == SOURCE_URL
        ]

        if len(pages) != 1:
            raise ValueError(
                "Expected one Computing source capture."
            )

        page = pages[0]

        if (
            page.get("source_url", "").rstrip("/") != SOURCE_URL
            or page.get("redirects")
        ):
            raise ValueError(
                "Computing source redirected; review required."
            )

        html_path = Path(page["html_file"])

        if not html_path.is_absolute():
            html_path = path.parent / html_path

        digest = hashlib.sha256(
            html_path.read_bytes()
        ).hexdigest()

        if digest != page["sha256"]:
            raise ValueError(
                "Saved HTML checksum does not match."
            )

        schemas = [
            script["parsed_json"]
            for script in page.get("scripts", [])
            if isinstance(script.get("parsed_json"), dict)
            and script["parsed_json"].get("@type") == "Course"
            and script["parsed_json"].get("name") == "Computing"
            and script["parsed_json"].get("award") == "BSc (Hons)"
        ]

        if len(schemas) != 1:
            raise ValueError(
                "Cannot verify the Computing course and award."
            )

        instances = schemas[0].get("hasCourseInstance", [])

        if isinstance(instances, dict):
            instances = [instances]

        matches = []

        for instance in instances:
            if not isinstance(instance, dict):
                continue

            locations = instance.get("location", [])

            if isinstance(locations, dict):
                locations = [locations]

            start = (
                instance.get("startdate")
                or instance.get("startDate")
                or ""
            )

            if (
                instance.get("name") == "Full-time"
                and instance.get("courseMode") == "Onsite"
                and instance.get("courseWorkload") == "P3Y"
                and str(start).startswith("2027-09")
                and any(
                    isinstance(location, dict)
                    and location.get("name") == "Peterborough"
                    for location in locations
                )
            ):
                matches.append(instance)

        if len(matches) != 1:
            raise ValueError(
                "Cannot verify the standard three-year "
                "Peterborough September 2027 route."
            )

        text = " ".join(page["text"].split())

        if not re.search(r"\bI118\b", text):
            raise ValueError("UCAS code I118 not found.")

        # Isolate the standard BSc fee section before the placement
        # section. The source contains more than one academic year.
        section_pattern = (
            r"International students\s+"
            r"BSc \(Hons\)\s+With placement\s+"
            r"With foundation year\s+"
            r"5 years with foundation and placement\s+"
            r"BSc \(Hons\)(.*?)"
            r"(?=With placement\s+£)"
        )

        sections = re.findall(
            section_pattern,
            text,
            re.IGNORECASE | re.DOTALL,
        )

        if len(sections) != 1:
            raise ValueError(
                "Cannot isolate the standard BSc international "
                "fee section."
            )

        fee_pattern = (
            r"£\s*([\d,]+(?:\.\d{2})?)\s+"
            r"International students starting\s+"
            r"(\d{4}/\d{2})\s+"
            r"\(full-time,\s*per year\)"
        )

        fees = [
            money(amount)
            for amount, year in re.findall(
                fee_pattern,
                sections[0],
                re.IGNORECASE,
            )
            if year == "2027/28"
        ]

        if fees != ["19750.00"]:
            raise ValueError(
                f"Unexpected standard 2027/28 fees: {fees}"
            )

        return {
            "source_sha256": digest,
            "evidence_generated_at": report.get("generated_at"),
        }

    @staticmethod
    def snapshot(course, tuition, source):
        return {
            "course": {
                field: getattr(course, field)
                for field in NEW_COURSE
            },
            "tuition": {
                "id": tuition.pk,
                "residency": tuition.residency,
                "currency": tuition.currency,
                "tuition_fee": money(tuition.tuition_fee),
            },
            "source": (
                {
                    "academic_year": source.academic_year,
                    "source_url": source.source_url,
                    "source_course_name": source.source_course_name,
                    "source_course_code": source.source_course_code,
                    "fee_basis": source.fee_basis,
                    "raw_fees": deepcopy(source.raw_fees),
                }
                if source is not None
                else None
            ),
        }

    def build_source(self, before, evidence):
        previous_source = before["source"]

        if previous_source is not None:
            if not isinstance(previous_source["raw_fees"], dict):
                raise ValueError(
                    "Existing source metadata requires review."
                )

            raw = deepcopy(previous_source["raw_fees"])
        else:
            raw = {}

        course_changed = before["course"] != NEW_COURSE

        source_changed = (
            previous_source is None
            or previous_source["source_url"] != SOURCE_URL
            or previous_source["academic_year"] != "2027/28"
        )

        if course_changed:
            history = raw.get("route_review_history", [])

            if not isinstance(history, list):
                raise ValueError(
                    "Existing route history requires review."
                )

            old_evidence = deepcopy(previous_source)

            if old_evidence:
                old_evidence["raw_fees"].pop(
                    "route_review_history",
                    None,
                )

            history.append({
                "recorded_at": timezone.now().isoformat(),
                "mapping": "explicit_replacement",
                "replacement_accepted": True,
                "not_a_confirmed_rename": True,
                "before_course": deepcopy(before["course"]),
                "after_course": deepcopy(NEW_COURSE),
                "previous_tuition_source": old_evidence,
                "evidence_generated_at": evidence[
                    "evidence_generated_at"
                ],
            })

            raw["route_review_history"] = history

        raw.update({
            "verified": True,
            "academic_year": "2027/28",
            "currency": "GBP",
            "source_fee_category": "International",
            "annual_tuition": "19750.00",
            "fee_basis": "annual_tuition",
            "course_kind": "undergraduate_taught",
            "course_source_url": SOURCE_URL,
            "source_course_name": "Computing",
            "source_course_code": "I118",
            "source_sha256": evidence["source_sha256"],
            "mapping": "explicit_replacement",
            "replacement_accepted": True,
            "not_a_confirmed_rename": True,
            "fee_note": FEE_NOTE,
            "fee_categories": [{
                "source_fee_category": "International",
                "academic_year": "2027/28",
                "currency": "GBP",
                "annual_tuition": "19750.00",
                "raw_annual_fee": "£19,750",
                "annotation": FEE_NOTE,
                "footnote_marker": "",
                "amount_requires_review": False,
            }],
        })

        raw.pop("verification_reason", None)

        # Preserve later award reconciliation on repeat imports.
        if course_changed or source_changed:
            raw["scholarship_status"] = "applicability_pending"
            raw["deposit_status"] = "applicability_pending"

        return {
            "academic_year": "2027/28",
            "source_url": SOURCE_URL,
            "source_course_name": "Computing",
            "source_course_code": "I118",
            "fee_basis": "annual_tuition",
            "raw_fees": raw,
        }

    @staticmethod
    def write_report(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)

        path.write_text(
            json.dumps(
                data,
                cls=DjangoJSONEncoder,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def handle(self, *args, **options):
        if (
            options["apply"]
            and not options["accept_replacements"]
        ):
            raise CommandError(
                "Use both --apply and --accept-replacements."
            )

        evidence_path = self.find_evidence(options["evidence"])

        try:
            evidence = self.verify_evidence(evidence_path)
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            ArithmeticError,
        ) as exc:
            raise CommandError(
                f"Source verification stopped: {exc}. "
                "No database records changed."
            ) from exc

        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")

        output = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aru"
            / f"computing-replacement-{stamp}.json"
        )

        with transaction.atomic():
            try:
                course = Course.objects.select_for_update().get(
                    university_id="UK003",
                    course_id=COURSE_ID,
                )

                if course.title not in {
                    "BSc (Hons) Computing and Information Systems",
                    NEW_COURSE["title"],
                }:
                    raise ValueError(
                        f"Unexpected stored title: {course.title!r}"
                    )

                duplicates = list(
                    Course.objects.filter(
                        university_id="UK003",
                        course_url__in=[
                            SOURCE_URL,
                            SOURCE_URL + "/",
                        ],
                    )
                    .exclude(course_id=COURSE_ID)
                    .values_list("course_id", flat=True)
                )

                if duplicates:
                    raise ValueError(
                        "Another record already uses this route: "
                        + ", ".join(duplicates)
                    )

                tuitions = list(
                    Tuition.objects.select_for_update().filter(
                        course=course,
                        residency="INTL",
                    )
                )

                if (
                    len(tuitions) != 1
                    or tuitions[0].currency != "GBP"
                ):
                    raise ValueError(
                        "Expected one GBP international tuition."
                    )

                tuition = tuitions[0]

                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )

                before = self.snapshot(course, tuition, source)
                source_values = self.build_source(before, evidence)

                after = {
                    "course": deepcopy(NEW_COURSE),
                    "tuition": {
                        **before["tuition"],
                        "tuition_fee": "19750.00",
                    },
                    "source": deepcopy(source_values),
                }

                changed = before != after

                report = {
                    "generated_at": timezone.now().isoformat(),
                    "university_id": "UK003",
                    "course_id": COURSE_ID,
                    "mode": "preview",
                    "database_changes": False,
                    "proposed_changes": int(changed),
                    "mapping": "explicit_replacement_proposal",
                    "replacement_accepted": False,
                    "not_a_confirmed_rename": True,
                    "evidence_report": str(evidence_path),
                    "before": before,
                    "after": after,
                    "other_courses_unchanged": HELD_REVIEWS,
                }

                self.write_report(output, report)

                if options["apply"] and changed:
                    fields = [
                        field
                        for field, value in NEW_COURSE.items()
                        if getattr(course, field) != value
                    ]

                    for field in fields:
                        setattr(course, field, NEW_COURSE[field])

                    if fields:
                        update_fields = list(fields)

                        if any(
                            field.name == "updated_at"
                            for field in Course._meta.fields
                        ):
                            update_fields.append("updated_at")

                        course.save(update_fields=update_fields)

                    if money(tuition.tuition_fee) != "19750.00":
                        tuition.tuition_fee = Decimal("19750.00")
                        tuition.save(update_fields=["tuition_fee"])

                    if before["source"] != source_values:
                        source, _ = (
                            TuitionSource.objects.update_or_create(
                                tuition=tuition,
                                defaults=source_values,
                            )
                        )

                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=COURSE_ID,
                        residency="INTL",
                        before=before,
                        after=after,
                    )

            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                ArithmeticError,
                Course.DoesNotExist,
            ) as exc:
                raise CommandError(
                    f"Import stopped: {exc}. "
                    "No changes from this batch were committed."
                ) from exc

        if options["apply"]:
            report["mode"] = "apply"
            report["database_changes"] = changed
            report["mapping"] = "explicit_replacement"
            report["replacement_accepted"] = True

            try:
                self.write_report(output, report)
            except OSError as exc:
                raise CommandError(
                    "Database operation completed, but the final "
                    f"report could not be updated: {exc}"
                ) from exc

        self.stdout.write(
            f"{COURSE_ID}: GBP "
            f"{before['tuition']['tuition_fee']} -> 19750.00; "
            "September 2027 / 2027/28"
        )

        for field, value in NEW_COURSE.items():
            if before["course"][field] != value:
                self.stdout.write(
                    f"  {field}: {before['course'][field]} -> {value}"
                )

        self.stdout.write(f"Report: {output}")

        if options["apply"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Applied changed records: {int(changed)}"
                )
            )
            self.stdout.write(
                "Accepted Computing replacement, international fee, "
                "source evidence and audit saved."
            )
        else:
            self.stdout.write(
                f"Proposed changes: {int(changed)}"
            )
            self.stdout.write(
                "Preview only. No database records changed."
            )

        self.stdout.write(
            "The other three courses remain unchanged and unresolved. "
            "Scholarship rules, stored discounts and deposit policies "
            "unchanged. Computing applicability requires reconciliation."
        )