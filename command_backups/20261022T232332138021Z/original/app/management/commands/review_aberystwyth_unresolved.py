import copy
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import (
    Course,
    TuitionSource,
    TuitionUpdateAudit,
    University,
)

from .import_aberystwyth_fees import read_json, validate_age


SPECIAL_REASONS = {
    "C_ABE002_RES_02": (
        "Possible Parasite Control MRes candidate. Equivalence to the "
        "stored Parasitology course requires confirmation."
    ),
    "C_ABE002_RES_03": (
        "Stored MRes award differs from the advertised MPhil/PhD "
        "International Politics routes."
    ),
    "C_ABE002_RES_04": (
        "Modern History is advertised as MA; the stored MRes award "
        "requires correction review."
    ),
    "C_ABE002_RES_06": (
        "Psychology MPhil candidate advertises one year; the stored "
        "two-year duration may represent a different study mode."
    ),
    "C_ABE002_RES_07": (
        "Stored Geography MSc by Research does not match the advertised "
        "MPhil award. Do not substitute a route without confirmation."
    ),
    "C_ABE002_RES_08": (
        "Computer Science taught MSc and research MPhil/PhD are distinct "
        "routes. The stored MSc Research identity remains unconfirmed."
    ),
    "C_UK002_07": (
        "Course identity found; applicable fee band for the combined "
        "Criminology and Sociology programme remains unconfirmed."
    ),
    "C_UK002_10": (
        "International Relations is a candidate for stored International "
        "Politics; course equivalence requires confirmation."
    ),
    "C_UK002_14": (
        "Joint Aberystwyth/RVC Veterinary Science route. Use its "
        "course-specific RVC fee source, not the generic science band."
    ),
    "C_UK002_36": (
        "The collected Adult Nursing page says international places "
        "for 2027 are awaiting HEIW funding confirmation."
    ),
    "C_UK002_37": (
        "The collected Mental Health Nursing page says the course "
        "is unavailable to international applicants ordinarily "
        "resident outside the UK. Individual eligibility needs assessment."
    ),
    "C_UK002_41": (
        "Drama and Theatre is a candidate for the stored Drama and "
        "Theatre Studies course; identity requires confirmation."
    ),
    "C_UK002_43": (
        "The stored French and Spanish language combination has not "
        "been matched to a confirmed official route."
    ),
    "C_UK002_57": (
        "Course identity found; the applicable fee band for combined "
        "Psychology and Marketing requires confirmation."
    ),
    "C_UK002_60": (
        "Stored Law with Criminology differs from the advertised "
        "Law and Criminology title; route equivalence is unconfirmed."
    ),
    "C_UK002_65": (
        "Advertised Agrifood Innovation candidates are longer programmes. "
        "Duration, study mode and distance-learning fee basis need review."
    ),
    "C_UK002_83": (
        "Physics with Astronomy has not been matched to a confirmed "
        "official route. Do not substitute another Physics programme."
    ),
    "C_UK002_84": (
        "Pure Mathematics / Statistics is a candidate; equivalence "
        "to Mathematics with Statistics remains unconfirmed."
    ),
    "C_UK002_91": (
        "Behaviour Change course identity found; its multidisciplinary "
        "programme fee classification remains unconfirmed."
    ),
    "C_UK002_92": (
        "Agrifood Innovation candidates differ in award and duration. "
        "Confirm the distance-learning route and module/programme fees."
    ),
    "C_UK002_97": (
        "International Politics PhD is a possible match, but it is also "
        "mapped to another database record. Duplicate/identity review needed."
    ),
    "C_UK002_99": (
        "Stored Geography and Earth Sciences MRes differs from advertised "
        "MPhil/PhD awards. Programme identity requires confirmation."
    ),
}

SCHOLARSHIP_MESSAGE = (
    "Scholarship information is not confirmed for this course and "
    "intake. No unverified discount should be included."
)

DEPOSIT_MESSAGE = (
    "Deposit amount is not available for comparison. "
    "Check your university offer letter."
)


class Command(BaseCommand):
    help = (
        "Preview/save evidence-based review statuses for remaining "
        "Aberystwyth courses. Does not change tuition prices."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        directory = Path(settings.BASE_DIR) / "logs" / "aberystwyth"
        paths = list(
            directory.glob(
                "remaining-review-*/remaining-course-review.json"
            )
        )

        if not paths:
            raise CommandError("Remaining-course evidence report not found.")

        evidence_path = max(paths, key=lambda path: path.parent.name)
        evidence = read_json(evidence_path)

        if evidence.get("university_id") != "UK002":
            raise CommandError("University evidence mismatch.")

        validate_age(evidence)

        evidence_records = {
            row["course_id"]: row
            for row in evidence.get("records", [])
        }

        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        output = directory / f"unresolved-review-{stamp}.json"

        proposals = []
        skipped_verified = []
        blocked = []
        locked = {}

        with transaction.atomic():
            university = University.objects.select_for_update().get(
                pk="UK002",
                country="UK",
            )

            if university.name.strip().casefold() != "aberystwyth university":
                raise CommandError("University identity mismatch.")

            courses = (
                Course.objects.select_for_update()
                .filter(
                    university=university,
                    is_active=True,
                    pk__in=evidence_records,
                )
                .order_by("course_id")
            )

            for course in courses:
                tuitions = list(
                    course.tuitions.select_for_update().filter(
                        residency="INTL"
                    )
                )

                if len(tuitions) != 1:
                    blocked.append({
                        "course_id": course.pk,
                        "reason": (
                            "A unique international tuition record is "
                            "required before saving review metadata."
                        ),
                    })
                    continue

                tuition = tuitions[0]
                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )

                before_raw = (
                    copy.deepcopy(source.raw_fees)
                    if source else {}
                )

                if not isinstance(before_raw, dict):
                    blocked.append({
                        "course_id": course.pk,
                        "reason": "Existing source metadata is not an object.",
                    })
                    continue

                # Never downgrade a fee already verified by another batch.
                if before_raw.get("verified") is True:
                    skipped_verified.append(course.pk)
                    continue

                record = evidence_records[course.pk]
                candidates = record.get("candidates", [])

                reason = SPECIAL_REASONS.get(
                    course.pk,
                    (
                        "No confirmed exact course/award match was established. "
                        "Similar-title candidates do not justify a fee update."
                        if candidates else
                        "No official catalogue candidate was found in this "
                        "collection. This does not establish discontinuation."
                    ),
                )

                candidate_urls = sorted({
                    candidate["source_url"]
                    for candidate in candidates
                    if candidate.get("source_url")
                })

                after_raw = copy.deepcopy(before_raw)
                after_raw.update({
                    "verified": False,
                    "fee_verification_status": "needs_review",
                    "fee_review": {
                        "status": "pending",
                        "reason": reason,
                        "candidate_urls": candidate_urls,
                        "evidence_generated_at": evidence["generated_at"],
                    },
                    "scholarship_status": "applicability_unconfirmed",
                    "deposit_status": "offer_letter_amount_required",
                    "display_notes": {
                        "fee": (
                            "International tuition has not been verified "
                            "for this course and intake."
                        ),
                        "scholarship": SCHOLARSHIP_MESSAGE,
                        "deposit": DEPOSIT_MESSAGE,
                    },
                })

                changed = before_raw != after_raw

                proposals.append({
                    "course_id": course.pk,
                    "title": course.title,
                    "changed": changed,
                    "reason": reason,
                    "before": before_raw,
                    "after": after_raw,
                    "source_record_to_create": source is None,
                })
                locked[course.pk] = (tuition, source)

                self.stdout.write(
                    f"{course.pk}: "
                    f"{'save review' if changed else 'unchanged'}"
                )

            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "university_id": "UK002",
                        "mode": "apply" if options["apply"] else "preview",
                        "evidence_report": str(evidence_path),
                        "proposals": proposals,
                        "blocked": blocked,
                        "skipped_verified": skipped_verified,
                        "tuition_prices_changed": False,
                        "courses_deactivated": False,
                        "scholarship_amounts_changed": False,
                        "deposit_amounts_changed": False,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            if options["apply"] and blocked:
                raise CommandError(
                    f"{len(blocked)} records are blocked. "
                    f"No review updates applied. See {output}"
                )

            if options["apply"]:
                for proposal in proposals:
                    if not proposal["changed"]:
                        continue

                    tuition, source = locked[proposal["course_id"]]

                    if source is None:
                        source = TuitionSource.objects.create(
                            tuition=tuition,
                            academic_year="",
                            source_url="",
                            source_course_name=proposal["title"],
                            source_course_code="",
                            fee_basis="unverified",
                            raw_fees=proposal["after"],
                        )
                    else:
                        source.raw_fees = proposal["after"]
                        source.save(update_fields=["raw_fees"])

                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=proposal["course_id"],
                        residency="INTL",
                        before={"raw_fees": proposal["before"]},
                        after={"raw_fees": proposal["after"]},
                    )

        self.stdout.write(f"\nUnresolved records reviewed: {len(proposals)}")
        self.stdout.write(f"Already verified and skipped: {len(skipped_verified)}")
        self.stdout.write(f"Blocked: {len(blocked)}")
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Saved review metadata. Tuition prices remain unchanged."
            if options["apply"]
            else "Preview only. No database records changed."
        )