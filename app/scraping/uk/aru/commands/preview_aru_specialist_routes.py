import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from app.models import Course, Tuition
from django.db import transaction

from app.models import TuitionSource, TuitionUpdateAudit


TARGETS = {
    "C_ARU003_01": {
        "slug": "undergraduate/international-business-top-up",
        "expected_title": "BSc (Hons) Business and Management (Top-up)",
        "replacement": True,
        "changes": {
            "title": "BSc (Hons) International Business (Top-Up)",
            "level": "TOPUP",
            "duration": "1 Year",
            "location": "Cambridge Campus",
            "start_month": "September",
            "start_year": 2027,
        },
        "fee": "18825.00",
        "academic_year": "2027/28",
        "route_markers": [
            "International Business",
            "Cambridge",
            "1 year full-time",
            "September 2027",
        ],
        "scholarship_note": (
            "Top-up scholarship applicability requires confirmation "
            "of entry year and the applicable award conditions."
        ),
    },
    "C_UK003_59": {
        "slug": "undergraduate/optometry",
        "expected_title": "MSc Optometry",
        "replacement": True,
        "changes": {
            "title": "MOptom Optometry",
            "level": "UG",
            "duration": "4 Years",
            "location": "Cambridge Campus",
            "start_month": "September",
            "start_year": 2027,
            "ucas_cao_code": "B510",
        },
        "fee": "19750.00",
        "academic_year": "2027/28",
        "route_markers": [
            "MOptom",
            "Cambridge",
            "4 years",
            "B510",
            "September 2027",
        ],
        "scholarship_note": (
            "The advertised route is an undergraduate-entry integrated "
            "master's degree. Scholarship eligibility must be checked "
            "for MOptom specifically; existing MSc eligibility "
            "must not be carried over automatically."
        ),
    },
    "C_UK003_70": {
        "slug": "undergraduate/equine-science",
        "expected_title": "BSc (Hons) Equine Behavioral Science",
        "replacement": True,
        "changes": {
            "title": "BSc (Hons) Equine Science",
            "level": "UG",
            "duration": "3 Years",
            "location": "Writtle Campus",
            "start_month": "September",
            "start_year": 2027,
            "ucas_cao_code": "D423",
        },
        "fee": "18825.00",
        "academic_year": "2027/28",
        "route_markers": [
            "Equine Science",
            "Writtle",
            "3 years",
            "D423",
            "September 2027",
        ],
        "scholarship_note": (
            "Check the standard three-year Writtle route against "
            "the scholarship's campus, entry-year and award conditions."
        ),
    },
    "C_UK003_87": {
        "slug": "undergraduate/early-childhood-studies",
        "expected_title": "BA (Hons) Early Childhood Studies",
        "replacement": False,
        "changes": {
            "title": (
                "BA (Hons) Early Childhood Studies "
                "(Graduate Practitioner)"
            ),
            "duration": "2 Years",
            "location": "Blended learning — Chelmsford",
            "start_month": "September",
            "start_year": 2027,
            "ucas_cao_code": "X3C0",
        },
        "fee": None,
        "academic_year": "",
        "route_markers": [
            "Graduate Practitioner",
            "Chelmsford",
            "2 years accelerated",
            "X3C0",
            "September 2027",
        ],
        "reason": (
            "The advertised Chelmsford route is a two-year accelerated, "
            "blended-learning degree. An applicable international "
            "tuition fee has not been verified. UK and distance-learning "
            "prices must not be substituted."
        ),
    },
    "C_UK003_88": {
        "slug": "undergraduate/social-work",
        "expected_title": "BA (Hons) Social Work",
        "replacement": False,
        "changes": {
            "location": "Blended learning — Cambridge",
            "start_month": "September",
            "start_year": 2027,
            "ucas_cao_code": "L501",
        },
        "fee": None,
        "academic_year": "",
        "route_markers": [
            "Social Work",
            "Cambridge",
            "Blended learning",
            "3 years",
            "L501",
            "September 2027",
        ],
        "reason": (
            "The captured page publishes UK tuition and application "
            "guidance for UK applicants and specified settled-status "
            "groups. An applicable international fee and application "
            "route remain unverified. This is not a blanket nationality "
            "exclusion."
        ),
    },
    "C_UK003_104": {
        "slug": "postgraduate/global-public-health",
        "expected_title": "MPH Global Public Health",
        "replacement": False,
        "changes": {
            "start_month": "January",
            "start_year": 2027,
        },
        "fee": None,
        "academic_year": "",
        "route_markers": [
            "Global Public Health",
            "Cambridge",
            "January 2027",
            "1 year full-time",
        ],
        "reason": (
            "The captured route advertises January 2027 entry. "
            "Its displayed international fee of £18,825 is labelled "
            "2027/28 and cannot verify January 2027 tuition. "
            "The applicable 2026/27 fee requires confirmation."
        ),
    },
}


def normalized(value):
    return " ".join(str(value).split()).casefold()


class Command(BaseCommand):
    help = (
        "Preview six ARU specialist route outcomes from saved evidence. "
        "Does not change database records."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--evidence",
            required=True,
            help="Path to campus-intake-evidence.json.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Save the reviewed proposals.",
        )
        parser.add_argument(
            "--accept-replacements",
            action="store_true",
            help="Accept the three explicit course replacements.",
        )

    def load_page(self, report, evidence_path, course_id, target):
        expected_url = (
            "https://www.aru.ac.uk/study/" + target["slug"]
        )

        captures = [
            page
            for page in report.get("pages", [])
            if page.get("source_url", "").rstrip("/")
            == expected_url.rstrip("/")
        ]

        if not captures:
            raise ValueError("Expected course-page capture is missing.")

        texts = set()

        for page in captures:
            html_name = page.get("html_file")
            expected_hash = page.get("sha256")

            if not html_name or not expected_hash:
                raise ValueError("Capture has no HTML file or checksum.")

            supplied_path = Path(html_name)

            if supplied_path.is_absolute():
                candidates = [supplied_path]
            else:
                candidates = [
                    evidence_path.parent / supplied_path,
                    Path(settings.BASE_DIR) / supplied_path,
                    evidence_path.parent / supplied_path.name,
                ]

            html_path = next(
                (path for path in candidates if path.is_file()),
                None,
            )

            if html_path is None:
                raise ValueError(f"Saved HTML is missing: {html_name}")

            actual_hash = hashlib.sha256(
                html_path.read_bytes()
            ).hexdigest()

            if actual_hash != expected_hash:
                raise ValueError("Saved HTML checksum does not match.")

            texts.add(normalized(page.get("text", "")))

        if len(texts) != 1:
            raise ValueError(
                "Duplicate captures contain different page text."
            )

        page = captures[0]
        page_text = next(iter(texts))

        for marker in target["route_markers"]:
            if normalized(marker) not in page_text:
                raise ValueError(
                    f"Expected route evidence is missing: {marker}"
                )

        course_entries = [
            entry
            for entry in report.get("courses", [])
            if entry.get("database_course", {}).get("course_id")
            == course_id
        ]

        if len(course_entries) != 1:
            raise ValueError(
                "Expected one course entry in the evidence report."
            )

        if (
            course_entries[0].get("course_source_url", "").rstrip("/")
            != expected_url.rstrip("/")
        ):
            raise ValueError("Course entry points to a different page.")

        return page, page_text, expected_url

    def make_proposal(self, report, evidence_path, course_id, target):
        page, page_text, source_url = self.load_page(
            report,
            evidence_path,
            course_id,
            target,
        )

        course = Course.objects.get(
            course_id=course_id,
            university_id="UK003",
        )

        allowed_titles = {
            target["expected_title"],
            target["changes"].get("title", target["expected_title"]),
        }

        if course.title not in allowed_titles:
            raise ValueError(
                f"Unexpected stored title: {course.title!r}"
            )

        tuitions = list(
            Tuition.objects.filter(
                course=course,
                residency="INTL",
            )
        )

        if len(tuitions) != 1 or tuitions[0].currency != "GBP":
            raise ValueError(
                "Exactly one GBP international tuition is required."
            )

        tuition = tuitions[0]

        course_changes = {
            field: {
                "before": getattr(course, field),
                "after": value,
            }
            for field, value in {
                **target["changes"],
                "course_url": source_url,
            }.items()
            if getattr(course, field) != value
        }

        proposed_fee = target["fee"]

        if proposed_fee is not None:
            amount = Decimal(proposed_fee)
            amount_text = f"{amount:,.0f}"

            fee_pattern = re.compile(
                rf"£\s*{re.escape(amount_text)}"
                rf"(?:\.00)?\s+International students starting\s+"
                rf"{re.escape(target['academic_year'])}"
                rf"\s+\(full-time,\s*per year\)",
                re.IGNORECASE,
            )

            matches = fee_pattern.findall(page.get("text", ""))

            # Equine Science publishes the same amount for both
            # BSc (Hons) and Cert HE. The route-specific check below
            # verifies that the standard BSc has this fee.
            if course_id != "C_UK003_70" and len(matches) != 1:
                raise ValueError(
                    "Cannot isolate one expected full-time "
                    "international annual fee."
                )
            if course_id == "C_UK003_70":
                route_fee_pattern = re.compile(
                    rf"International students\s+"
                    rf"BSc \(Hons\)\s+With foundation year\s+"
                    rf"6 years part-time\s+Cert HE\s+"
                    rf"BSc \(Hons\)\s+£\s*{re.escape(amount_text)}"
                    rf"(?:\.00)?\s+International students starting\s+"
                    rf"2027/28\s+\(full-time,\s*per year\)",
                    re.IGNORECASE,
                )

                route_matches = route_fee_pattern.findall(
                    page.get("text", "")
                )

                if len(route_matches) != 1:
                    raise ValueError(
                        "Cannot associate the Equine fee with "
                        "the standard BSc route."
                    )

            if not re.search(
                r"deposit of £\s*4,000 or a sponsorship letter",
                page.get("text", ""),
                re.IGNORECASE,
            ):
                raise ValueError(
                    "Expected £4,000 deposit statement is missing."
                )

        return {
            "course_id": course_id,
            "mapping": (
                "explicit_replacement_proposal"
                if target["replacement"]
                else "route_correction_proposal"
            ),
            "replacement_accepted": False,
            "course_changes": course_changes,
            "tuition": {
                "stored_amount": str(tuition.tuition_fee),
                "proposed_amount": proposed_fee,
                "currency": "GBP",
                "academic_year": target["academic_year"],
                "fee_basis": (
                    "annual_tuition"
                    if proposed_fee is not None
                    else "unavailable"
                ),
                "fee_available": proposed_fee is not None,
                "reason": target.get("reason", ""),
            },
            "scholarship": {
                "status": "applicability_pending",
                "note": target.get(
                    "scholarship_note",
                    "Scholarship applicability remains unverified "
                    "for the reviewed route and intake.",
                ),
                "existing_rules_changed": False,
                "eligibility_questions_preserved": True,
            },
            "deposit": {
                "published_amount": (
                    "4000.00" if proposed_fee is not None else None
                ),
                "currency": "GBP",
                "status": (
                    "published_statement_scope_review_required"
                    if proposed_fee is not None
                    else "applicability_pending"
                ),
                "note": (
                    "A published deposit statement does not extend "
                    "the saved policy automatically. Visa, funding, "
                    "sponsorship and course applicability require review."
                ),
                "saved_policy_changed": False,
            },
            "evidence": {
                "source_url": source_url,
                "source_sha256": page["sha256"],
                "evidence_generated_at": report.get("generated_at"),
            },
        }

    def apply_proposals(self, proposals, report, evidence_path):
        with transaction.atomic():
            # Lock every affected course and tuition before rechecking.
            list(
                Course.objects.select_for_update()
                .filter(
                    university_id="UK003",
                    course_id__in=TARGETS,
                )
                .order_by("course_id")
            )

            list(
                Tuition.objects.select_for_update()
                .filter(
                    course__university_id="UK003",
                    course_id__in=TARGETS,
                    residency="INTL",
                )
                .order_by("course_id")
            )

            # Refuse to apply a preview if its database inputs changed.
            for original in proposals:
                current = self.make_proposal(
                    report,
                    evidence_path,
                    original["course_id"],
                    TARGETS[original["course_id"]],
                )

                if current != original:
                    raise CommandError(
                        f"{original['course_id']}: inputs changed "
                        "during the run. Nothing was applied."
                    )

            changed_count = 0

            for proposal in proposals:
                course_id = proposal["course_id"]

                course = Course.objects.get(
                    course_id=course_id,
                    university_id="UK003",
                )
                tuition = Tuition.objects.get(
                    course=course,
                    residency="INTL",
                    currency="GBP",
                )

                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )

                fields = set(TARGETS[course_id]["changes"])
                fields.add("course_url")

                before_course = {
                    field: getattr(course, field)
                    for field in sorted(fields)
                }

                before_source = None

                if source is not None:
                    before_source = {
                        "academic_year": source.academic_year,
                        "source_url": source.source_url,
                        "source_course_code": source.source_course_code,
                        "source_course_name": source.source_course_name,
                        "fee_basis": source.fee_basis,
                        "raw_fees": source.raw_fees,
                    }

                before = {
                    "course": before_course,
                    "tuition": {
                        "tuition_fee": str(tuition.tuition_fee),
                        "currency": tuition.currency,
                    },
                    "source": before_source,
                }

                for field, change in proposal["course_changes"].items():
                    setattr(course, field, change["after"])

                fee = proposal["tuition"]
                evidence = proposal["evidence"]
                verified = fee["fee_available"]

                # Preserve earlier evidence and replacement history.
                raw = (
                    dict(source.raw_fees)
                    if source is not None
                    and isinstance(source.raw_fees, dict)
                    else {}
                )

                history = list(raw.get("route_review_history", []))

                if proposal["course_changes"]:
                    history.append({
                        "mapping": proposal["mapping"],
                        "replacement_accepted": (
                            TARGETS[course_id]["replacement"]
                        ),
                        "changes": proposal["course_changes"],
                        "source_url": evidence["source_url"],
                        "source_sha256": evidence["source_sha256"],
                        "evidence_generated_at": (
                            evidence["evidence_generated_at"]
                        ),
                    })

                raw.update({
                    "verified": verified,
                    "academic_year": fee["academic_year"],
                    "source_fee_category": "International",
                    "currency": "GBP",
                    "annual_tuition": fee["proposed_amount"],
                    "fee_basis": fee["fee_basis"],
                    "course_source_url": evidence["source_url"],
                    "source_sha256": evidence["source_sha256"],
                    "verification_reason": fee["reason"],
                    "fee_note": (
                        "Published annual international tuition "
                        "for the reviewed standard full-time route."
                        if verified
                        else fee["reason"]
                    ),
                    "scholarship_status": "applicability_unconfirmed",
                    "scholarship_review": proposal["scholarship"],
                    "deposit_status": "applicability_pending",
                    "deposit_review": proposal["deposit"],
                    "route_review_history": history,
                    "fee_categories": (
                        [{
                            "source_fee_category": "International",
                            "academic_year": fee["academic_year"],
                            "currency": "GBP",
                            "annual_tuition": fee["proposed_amount"],
                            "amount_requires_review": False,
                        }]
                        if verified
                        else []
                    ),
                })

                after_source = {
                    "academic_year": fee["academic_year"],
                    "source_url": evidence["source_url"],
                    "source_course_code": course.ucas_cao_code or "",
                    "source_course_name": course.title,
                    "fee_basis": fee["fee_basis"],
                    "raw_fees": raw,
                }

                new_amount = (
                    Decimal(fee["proposed_amount"])
                    if verified
                    else tuition.tuition_fee
                )

                after = {
                    "course": {
                        field: getattr(course, field)
                        for field in sorted(fields)
                    },
                    "tuition": {
                        "tuition_fee": str(new_amount),
                        "currency": tuition.currency,
                    },
                    "source": after_source,
                }

                if before == after:
                    continue

                if proposal["course_changes"]:
                    course.save(
                        update_fields=[
                            *proposal["course_changes"],
                            "updated_at",
                        ]
                    )

                if tuition.tuition_fee != new_amount:
                    tuition.tuition_fee = new_amount
                    tuition.save(update_fields=["tuition_fee"])

                TuitionSource.objects.update_or_create(
                    tuition=tuition,
                    defaults=after_source,
                )

                TuitionUpdateAudit.objects.create(
                    tuition=tuition,
                    course_identifier=course_id,
                    residency="INTL",
                    before=before,
                    after=after,
                )

                changed_count += 1

        return changed_count
    def handle(self, *args, **options):
        evidence_path = Path(options["evidence"])

        if not evidence_path.is_absolute():
            evidence_path = (
                Path(settings.BASE_DIR) / evidence_path
            )

        try:
            report = json.loads(
                evidence_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, ValueError) as exc:
            raise CommandError(
                f"Cannot read evidence: {exc}"
            ) from exc

        if report.get("university_id") != "UK003":
            raise CommandError("Evidence is not for ARU UK003.")

        proposals = []
        blocked = []

        for course_id, target in TARGETS.items():
            try:
                proposal = self.make_proposal(
                    report,
                    evidence_path,
                    course_id,
                    target,
                )
            except (
                ValueError,
                OSError,
                Course.DoesNotExist,
                Course.MultipleObjectsReturned,
            ) as exc:
                blocked.append({
                    "course_id": course_id,
                    "reason": str(exc),
                })
                self.stderr.write(f"{course_id}: BLOCKED — {exc}")
                continue

            proposals.append(proposal)
            fee = proposal["tuition"]

            if fee["fee_available"]:
                self.stdout.write(
                    f"{course_id}: GBP {fee['stored_amount']} -> "
                    f"{fee['proposed_amount']}; "
                    f"{fee['academic_year']} — "
                    f"{proposal['mapping']}"
                )
            else:
                self.stdout.write(
                    f"{course_id}: international fee unavailable"
                )
                self.stdout.write(f"  {fee['reason']}")

            for field, change in proposal["course_changes"].items():
                self.stdout.write(
                    f"  {field}: {change['before']} -> {change['after']}"
                )

        now = timezone.now()
        folder = Path(settings.BASE_DIR) / "logs" / "aru"
        folder.mkdir(parents=True, exist_ok=True)

        output = folder / (
            "specialist-route-preview-"
            + now.strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )

        output.write_text(
            json.dumps(
                {
                    "generated_at": now.isoformat(),
                    "mode": "preview",
                    "database_changes": False,
                    "university_id": "UK003",
                    "evidence_report": str(evidence_path),
                    "collection_issues": report.get("errors", []),
                    "proposals": proposals,
                    "blocked": blocked,
                },
                cls=DjangoJSONEncoder,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        self.stdout.write(
            "\nVerified fee proposals: "
            + str(sum(
                item["tuition"]["fee_available"]
                for item in proposals
            ))
        )
        self.stdout.write(
            "Unavailable fee proposals: "
            + str(sum(
                not item["tuition"]["fee_available"]
                for item in proposals
            ))
        )
        self.stdout.write(f"Blocked: {len(blocked)}")
        self.stdout.write(f"Report: {output}")
        if blocked:
            self.stdout.write(
                "No database records changed."
            )
            raise CommandError(
                "Some proposals require review. See the saved report."
            )

        if not options["apply"]:
            self.stdout.write(
                "Preview only. No database records changed."
            )
            self.stdout.write(
                "Scholarship and deposit scopes remain pending."
            )
            return

        if not options["accept_replacements"]:
            raise CommandError(
                "Applying this batch requires "
                "--accept-replacements because three proposals "
                "replace the stored course routes. "
                "No database records changed."
            )

        changed_count = self.apply_proposals(
            proposals,
            report,
            evidence_path,
        )

        saved_report = json.loads(
            output.read_text(encoding="utf-8")
        )
        saved_report.update({
            "mode": "apply",
            "database_changes": changed_count > 0,
            "changed_records": changed_count,
            "replacements_accepted": True,
        })

        output.write_text(
            json.dumps(
                saved_report,
                cls=DjangoJSONEncoder,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        self.stdout.write(
            f"Applied changed records: {changed_count}"
        )
        self.stdout.write(
            "Reviewed routes, tuition evidence and audit records saved."
        )
        self.stdout.write(
            "Unavailable fees remain unavailable. Scholarship rules "
            "and deposit policy scopes were not extended."
        )
        self.stdout.write(
            "Replacements are proposals. Scholarship rules, "
            "eligibility questions and deposit policies are unchanged."
        )

        if blocked:
            raise CommandError(
                "Some proposals require review. See the saved report."
            )