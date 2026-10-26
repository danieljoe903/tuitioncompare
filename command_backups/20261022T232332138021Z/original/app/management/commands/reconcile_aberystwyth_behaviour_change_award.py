import copy
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import (
    Course,
    ScholarshipRule,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
    University,
)

from .import_aberystwyth_behaviour_change_fee import verify_sources


COURSE_ID = "C_UK002_91"
YEAR = "2027/28"
RULE_NAME = "Global Citizens Scholarship — Sciences PG (2027/28)"

SCHOLARSHIP_URL = (
    "https://www.aber.ac.uk/en/study-with-us/international/"
    "scholarships/global-citizens-scholarship/"
)

REQUIRED_CONDITIONS = {
    "fee_status": ["INTL"],
    "new_entrant": [True],
    "offer_received": [True],
    "external_funding": [False],
    "applied_by_deadline": [True],
    "academic_conditions_met": [True],
}


def text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def verify_scholarship(session):
    response = session.get(SCHOLARSHIP_URL, timeout=(20, 60))
    response.raise_for_status()

    if response.url.rstrip("/") != SCHOLARSHIP_URL.rstrip("/"):
        raise ValueError("Unexpected scholarship page redirect.")

    soup = BeautifulSoup(response.content, "html.parser")
    heading = soup.find("h1")

    if heading is None or text(heading) != "Global Citizens Scholarship":
        raise ValueError("Wrong scholarship page.")

    matches = []

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = [
                text(cell)
                for cell in row.find_all(["th", "td"], recursive=False)
            ]

            if len(cells) < 4 or cells[0].casefold() != "sciences":
                continue

            compact = [
                value.replace(",", "").replace(" ", "")
                for value in cells[1:4]
            ]

            if (
                compact[0] == "£23820"
                and compact[1] == "£3800"
                and compact[2].startswith("£20020")
            ):
                matches.append(cells)

    if len(matches) != 1:
        raise ValueError(
            "A unique £23,820 / £3,800 / £20,020 science award "
            "row is required."
        )

    page_text = text(soup).casefold()

    required_phrases = (
        "2027/28",
        "international fee-paying",
        "academic entry requirements",
        "received an offer",
        "external",
        "1st august",
        "distance learning",
    )

    for phrase in required_phrases:
        if phrase not in page_text:
            raise ValueError(
                f"Scholarship terms need review: missing {phrase!r}."
            )

    return {
        "source_url": SCHOLARSHIP_URL,
        "sha256": hashlib.sha256(response.content).hexdigest(),
        "published_row": matches[0],
    }


class Command(BaseCommand):
    help = (
        "Extend the verified postgraduate science scholarship scope "
        "to MSc Behaviour Change."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            with requests.Session() as session:
                session.headers["User-Agent"] = (
                    "TuitionCompareSourceInspector/1.0"
                )
                fee_evidence = verify_sources(session)
                award_evidence = verify_scholarship(session)
        except (requests.RequestException, ValueError) as exc:
            raise CommandError(
                f"Source verification stopped: {exc}. "
                "No database records changed."
            ) from exc

        with transaction.atomic():
            if options["apply"]:
                University.objects.select_for_update().get(
                    university_id="UK002"
                )

            courses = Course.objects.filter(
                course_id=COURSE_ID,
                university_id="UK002",
                is_active=True,
                level="PG",
                start_month="September",
                start_year=2027,
            )
            if options["apply"]:
                courses = courses.select_for_update()

            try:
                course = courses.get()
            except Course.DoesNotExist as exc:
                raise CommandError(
                    "Corrected September 2027 course not found."
                ) from exc

            tuitions = Tuition.objects.filter(
                course=course,
                residency="INTL",
            )
            if options["apply"]:
                tuitions = tuitions.select_for_update()
            tuitions = list(tuitions)

            if len(tuitions) != 1 or tuitions[0].currency != "GBP":
                raise CommandError(
                    "A unique GBP international tuition is required."
                )

            tuition = tuitions[0]

            if Decimal(str(tuition.tuition_fee)) != Decimal("23820.00"):
                raise CommandError("Verified gross tuition must be £23,820.")

            sources = TuitionSource.objects.filter(tuition=tuition)
            if options["apply"]:
                sources = sources.select_for_update()

            try:
                source = sources.get()
            except TuitionSource.DoesNotExist as exc:
                raise CommandError("Tuition source is missing.") from exc

            raw = source.raw_fees

            if (
                not isinstance(raw, dict)
                or raw.get("verified") is not True
                or source.academic_year != YEAR
                or raw.get("course_kind") != "postgraduate_taught"
                or raw.get("fee_band") != "Sciences"
            ):
                raise CommandError(
                    "Verified 2027/28 postgraduate science fee metadata "
                    "is required."
                )

            rules = ScholarshipRule.objects.filter(
                scholarship__university_id="UK002",
                scholarship__name=RULE_NAME,
                scholarship__level="PG",
            )
            if options["apply"]:
                rules = rules.select_for_update()
            rules = list(rules)

            if len(rules) != 1:
                raise CommandError(
                    "A unique existing postgraduate science award rule "
                    "is required."
                )

            rule = rules[0]
            saved = rule.rules

            if (
                rule.verified is not True
                or rule.source_url.rstrip("/")
                != SCHOLARSHIP_URL.rstrip("/")
                or not isinstance(saved, dict)
            ):
                raise CommandError("Existing award rule is not verified.")

            try:
                valid_value = (
                    Decimal(str(saved.get("value"))) == Decimal("3800.00")
                )
            except Exception as exc:
                raise CommandError("Invalid stored award value.") from exc

            if (
                not valid_value
                or saved.get("kind") != "fixed"
                or saved.get("benefit") != "tuition"
                or saved.get("currency") != "GBP"
                or saved.get("academic_year") != YEAR
                or saved.get("study_years") != [1]
                or saved.get("fee_basis_confirmed") is not True
                or saved.get("award_status") != "conditional"
                or saved.get("application_deadline") != "2027-08-01"
            ):
                raise CommandError(
                    "Existing award configuration differs from the "
                    "reviewed first-year rule."
                )

            conditions = saved.get("conditions")
            if not isinstance(conditions, dict):
                raise CommandError("Award conditions require review.")

            for key, expected in REQUIRED_CONDITIONS.items():
                if conditions.get(key) != expected:
                    raise CommandError(
                        f"Unexpected eligibility condition: {key}."
                    )

            course_ids = conditions.get("course_id")
            if (
                not isinstance(course_ids, list)
                or not all(isinstance(value, str) for value in course_ids)
                or not course_ids
            ):
                raise CommandError("Existing course scope is invalid.")

            before = {
                "scholarship_rule_id": rule.pk,
                "rules": copy.deepcopy(saved),
                "raw_fees": copy.deepcopy(raw),
            }

            updated_rule = copy.deepcopy(saved)
            updated_rule["conditions"]["course_id"] = sorted(
                set(course_ids) | {COURSE_ID}
            )

            updated_raw = copy.deepcopy(raw)
            updated_raw.update({
                "scholarship_status": "verified_rule_eligibility_required",
                "scholarship_review": {
                    "status": "verified_rule_eligibility_required",
                    "scholarship_rule_id": rule.pk,
                    "source_url": SCHOLARSHIP_URL,
                    "academic_year": YEAR,
                    "award": "3800.00",
                    "gross": "23820.00",
                    "net_if_eligible": "20020.00",
                    "study_year": 1,
                    "note": (
                        "Postgraduate science award. Student eligibility "
                        "must be confirmed before a deduction is calculated."
                    ),
                },
            })

            notes = updated_raw.get("display_notes", {})
            if not isinstance(notes, dict):
                notes = {}

            notes["scholarship"] = (
                "£3,800 scholarship if the published eligibility "
                "conditions are met; estimated net tuition £20,020."
            )
            updated_raw["display_notes"] = notes

            after = {
                "scholarship_rule_id": rule.pk,
                "rules": updated_rule,
                "raw_fees": updated_raw,
            }

            changed = before != after
            now = datetime.now(timezone.utc)
            folder = Path(settings.BASE_DIR) / "logs" / "aberystwyth"
            folder.mkdir(parents=True, exist_ok=True)

            report_path = folder / (
                "behaviour-change-award-"
                + now.strftime("%Y%m%dT%H%M%S%fZ")
                + ".json"
            )

            report_path.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "mode": (
                            "apply_requested"
                            if options["apply"]
                            else "preview"
                        ),
                        "course_id": COURSE_ID,
                        "changed": changed,
                        "fee_evidence": fee_evidence,
                        "award_evidence": award_evidence,
                        "before": before,
                        "after": after,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self.stdout.write(
                f"{COURSE_ID}: "
                f"{'update' if changed else 'unchanged'}"
            )
            self.stdout.write(
                "Conditional scholarship: GBP 3800.00"
            )
            self.stdout.write(
                "Net tuition if eligible: GBP 20020.00"
            )
            self.stdout.write(
                "PG science course scope: "
                f"{len(updated_rule['conditions']['course_id'])}"
            )
            self.stdout.write(f"Proposed changes: {int(changed)}")
            self.stdout.write(f"Report: {report_path}")

            if not options["apply"]:
                self.stdout.write(
                    "Preview only. No database records changed."
                )
                return

            if changed:
                rule.rules = updated_rule
                rule.save(update_fields=["rules", "reviewed_at"])

                source.raw_fees = updated_raw
                source.save(update_fields=["raw_fees", "checked_at"])

                TuitionUpdateAudit.objects.create(
                    tuition=tuition,
                    course_identifier=COURSE_ID,
                    residency="INTL",
                    before=before,
                    after=after,
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Scholarship scope and course review metadata saved."
                if changed
                else "Already current. No database updates needed."
            )
        )
        self.stdout.write(
            "Gross tuition, stored discounts and deposits unchanged."
        )