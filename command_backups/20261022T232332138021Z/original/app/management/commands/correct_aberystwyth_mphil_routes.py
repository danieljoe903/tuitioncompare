import copy
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import (
    Course,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
    University,
)

from .import_aberystwyth_fees import read_json, validate_age


MATCHES = {
    "C_ABE002_RES_03": {
        "official_title": "International Politics",
        "title": "MPhil International Politics",
        "code": "L2442",
        "path": "/postgraduate/international-politics-mphil",
    },
    "C_ABE002_RES_08": {
        "official_title": "Computer Science",
        "title": "MPhil Computer Science",
        "code": "G4313",
        "path": "/postgraduate/mphil-computer-science",
    },
    "C_UK002_99": {
        "official_title": "Geography and Earth Sciences (Science)",
        "title": "MPhil Geography and Earth Sciences (Science)",
        "code": "F9352",
        "path": "/postgraduate/mphil-geography-science",
    },
}


def course_values(course):
    return {
        "title": course.title,
        "level": course.level,
        "duration": course.duration,
        "start_month": course.start_month,
        "start_year": course.start_year,
        "course_url": course.course_url,
    }


class Command(BaseCommand):
    help = (
        "Preview/apply three proposed MPhil route replacements. "
        "No tuition, scholarship or deposit amount changes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--accept-proposed-matches",
            action="store_true",
            help="Accept the displayed official MPhil route replacements.",
        )

    def handle(self, *args, **options):
        if options["apply"] and not options["accept_proposed_matches"]:
            raise CommandError(
                "Review the preview first, then use "
                "--apply --accept-proposed-matches."
            )

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

        records = {}
        for record in evidence.get("records", []):
            course_id = record.get("course_id")
            if not course_id or course_id in records:
                raise CommandError("Missing or duplicate evidence course ID.")
            records[course_id] = record

        proposals = []
        blocked = []
        locked = {}
        now = timezone.now()
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        output = directory / f"mphil-corrections-{stamp}.json"

        with transaction.atomic():
            university = University.objects.select_for_update().get(
                pk="UK002",
                country="UK",
            )

            if university.name.strip().casefold() != "aberystwyth university":
                raise CommandError("University identity mismatch.")

            for course_id, match in MATCHES.items():
                record = records.get(course_id, {})
                candidates = []

                for candidate in record.get("candidates", []):
                    parsed = urlsplit(candidate.get("source_url", ""))

                    if (
                        parsed.scheme == "https"
                        and parsed.netloc == "courses.aber.ac.uk"
                        and parsed.path.rstrip("/") == match["path"]
                        and not parsed.query
                        and not parsed.fragment
                    ):
                        candidates.append(candidate)

                if len(candidates) != 1:
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Unique official MPhil candidate required.",
                    })
                    continue

                candidate = candidates[0]
                page = candidate.get("evidence", {})
                content = page.get("text", "")

                if (
                    page.get("status") != "collected"
                    or page.get("title") != match["official_title"]
                    or not re.search(
                        rf"\b{re.escape(match['code'])}-MPHL\b",
                        content,
                        flags=re.IGNORECASE,
                    )
                    or not re.search(
                        r"Course duration\s+1\s+year\b",
                        content,
                        flags=re.IGNORECASE,
                    )
                    or page.get("advertised_intakes") != [
                        {"month": "January", "year": 2027}
                    ]
                ):
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Official award, duration or intake changed.",
                    })
                    continue

                course = Course.objects.select_for_update().filter(
                    pk=course_id,
                    university=university,
                    is_active=True,
                ).first()

                if course is None:
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Active database course not found.",
                    })
                    continue

                before = course_values(course)
                after = {
                    "title": match["title"],
                    "level": "PG",
                    "duration": "1 Year",
                    "start_month": "January",
                    "start_year": 2027,
                    "course_url": candidate["source_url"],
                }

                stored = record.get("stored", {})

                if not all(
                    before[field] in (stored.get(field), after[field])
                    for field in ("title", "level", "duration")
                ):
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Database identity changed since collection.",
                    })
                    continue

                duplicate_ids = list(
                    Course.objects.filter(
                        university=university,
                        is_active=True,
                        course_url__in=[
                            after["course_url"],
                            after["course_url"].rstrip("/"),
                        ],
                    )
                    .exclude(pk=course_id)
                    .values_list("course_id", flat=True)
                )

                if duplicate_ids:
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Official route is already mapped elsewhere.",
                        "other_course_ids": duplicate_ids,
                    })
                    continue

                tuitions = list(
                    Tuition.objects.select_for_update().filter(
                        course=course,
                        residency="INTL",
                    )
                )

                if len(tuitions) != 1:
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Unique international tuition required.",
                    })
                    continue

                tuition = tuitions[0]
                source = (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition=tuition)
                    .first()
                )

                if source is None or not isinstance(source.raw_fees, dict):
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Saved unresolved-review metadata is required.",
                    })
                    continue

                if source.raw_fees.get("verified") is True:
                    blocked.append({
                        "course_id": course_id,
                        "reason": "Tuition is already verified; review separately.",
                    })
                    continue

                raw_before = copy.deepcopy(source.raw_fees)
                raw_after = copy.deepcopy(raw_before)
                raw_after.update({
                    "verified": False,
                    "fee_verification_status": "pending_fee_mapping",
                    "course_identity": {
                        "status": "accepted_current_catalogue_match",
                        "official_title": match["official_title"],
                        "official_code": match["code"],
                        "official_award": "MPHL",
                        "source_url": candidate["source_url"],
                        "evidence_generated_at": evidence["generated_at"],
                        "note": (
                            "Accepted current MPhil replacement for the "
                            "stored record; historical equivalence is "
                            "not established."
                        ),
                    },
                    "fee_review": {
                        "status": "pending",
                        "reason": (
                            "Accepted MPhil identity. January 2027 tuition "
                            "must be mapped to the 2026/27 research fee."
                        ),
                        "candidate_urls": [candidate["source_url"]],
                    },
                    "scholarship_status": "applicability_unconfirmed",
                    "deposit_status": "offer_letter_amount_required",
                })

                changed = before != after or raw_before != raw_after

                proposals.append({
                    "course_id": course_id,
                    "changed": changed,
                    "before": before,
                    "after": after,
                    "metadata_before": raw_before,
                    "metadata_after": raw_after,
                })
                locked[course_id] = (course, tuition, source)

                self.stdout.write(
                    f"\n{course_id}: "
                    f"{'proposed correction' if changed else 'unchanged'}"
                )

                for field in after:
                    if before[field] != after[field]:
                        self.stdout.write(
                            f"  {field}: {before[field]} -> {after[field]}"
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
                        "tuition_prices_changed": False,
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
                    f"{len(blocked)} records require review. "
                    f"No corrections applied. See {output}"
                )

            if options["apply"]:
                for proposal in proposals:
                    if not proposal["changed"]:
                        continue

                    course, tuition, source = locked[proposal["course_id"]]

                    for field, value in proposal["after"].items():
                        setattr(course, field, value)

                    course.save(
                        update_fields=list(proposal["after"]) + ["updated_at"]
                    )

                    source.raw_fees = proposal["metadata_after"]
                    source.save(update_fields=["raw_fees"])

                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=course.pk,
                        residency="INTL",
                        before={
                            "course": proposal["before"],
                            "raw_fees": proposal["metadata_before"],
                        },
                        after={
                            "course": proposal["after"],
                            "raw_fees": proposal["metadata_after"],
                        },
                    )

        self.stdout.write(f"\nMatched candidates: {len(proposals)}")
        self.stdout.write(
            f"Proposed changes: {sum(p['changed'] for p in proposals)}"
        )
        self.stdout.write(f"Blocked: {len(blocked)}")
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Accepted MPhil course corrections saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )
        self.stdout.write(
            "Tuition prices, scholarships and deposit amounts unchanged."
        )