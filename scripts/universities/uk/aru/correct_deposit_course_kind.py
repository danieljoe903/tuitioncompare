"""Correct five reviewed ARU undergraduate source metadata labels.

Run through Django's management shell.
No tuition, scholarship or deposit amounts are changed.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.db import transaction

from app.models import TuitionSource


EXPECTED = {
    "C_UK003_01": 332,
    "C_UK003_05": 343,
    "C_UK003_31": 349,
    "C_UK003_36": 333,
    "C_UK003_95": 334,
}

OLD_KIND = "undergraduate"
NEW_KIND = "undergraduate_taught"


def run(*, apply=False):
    stamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    folder = Path(settings.BASE_DIR) / "logs" / "aru"
    folder.mkdir(parents=True, exist_ok=True)
    report_path = folder / f"deposit-kind-correction-{stamp}.json"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "apply" if apply else "preview",
        "university_id": "UK003",
        "records": [],
        "changed_records": 0,
        "database_changes": False,
    }

    with transaction.atomic():
        query = TuitionSource.objects.filter(
            pk__in=EXPECTED.values()
        ).select_related("tuition__course")

        if apply:
            query = query.select_for_update()

        sources = {source.pk: source for source in query}
        updates = []

        # Validate all five records before saving any changes.
        for course_id, source_id in EXPECTED.items():
            source = sources.get(source_id)

            if source is None:
                raise ValueError(
                    f"{course_id}: source {source_id} is missing."
                )

            tuition = source.tuition
            course = tuition.course

            if (
                course.course_id != course_id
                or str(course.university_id) != "UK003"
                or tuition.residency != "INTL"
                or tuition.currency != "GBP"
                or source.academic_year != "2027/28"
                or course.level != "UG"
            ):
                raise ValueError(
                    f"{course_id}: unexpected source/course identity, "
                    "fee category, level or academic year. "
                    "No corrections applied."
                )

            raw = source.raw_fees
            if not isinstance(raw, dict):
                raise ValueError(
                    f"{course_id}: source metadata is not a dictionary."
                )

            previous = raw.get("course_kind")
            if previous not in {OLD_KIND, NEW_KIND}:
                raise ValueError(
                    f"{course_id}: unexpected course_kind "
                    f"{previous!r}. No corrections applied."
                )

            changed = previous != NEW_KIND
            updated = {**raw, "course_kind": NEW_KIND}

            report["records"].append({
                "course_id": course_id,
                "source_id": source_id,
                "academic_year": source.academic_year,
                "before_raw_fees": raw,
                "after_raw_fees": updated,
                "changed": changed,
            })

            if changed:
                updates.append((source, updated))

            status = "update" if changed else "unchanged"
            print(
                f"{course_id}: {previous} -> {NEW_KIND}; {status}"
            )

        report["changed_records"] = len(updates)

        # Save the before/after evidence before attempting DB writes.
        report["status"] = (
            "prepared" if apply else "preview"
        )
        report_path.write_text(
            json.dumps(
                report, indent=2, ensure_ascii=False, default=str
            ),
            encoding="utf-8",
        )

        if apply:
            for source, updated in updates:
                # Update only this JSON field. Do not refresh checked_at:
                # this is metadata correction, not new source verification.
                count = TuitionSource.objects.filter(
                    pk=source.pk,
                    raw_fees=source.raw_fees,
                ).update(raw_fees=updated)

                if count != 1:
                    raise RuntimeError(
                        f"Source {source.pk} changed concurrently. "
                        "All corrections rolled back."
                    )

    report["database_changes"] = bool(apply and updates)
    report["status"] = "applied" if apply else "preview"
    report_path.write_text(
        json.dumps(
            report, indent=2, ensure_ascii=False, default=str
        ),
        encoding="utf-8",
    )

    print(f"\nChanged/proposed records: {len(updates)}")
    print(f"Report: {report_path}")

    if apply:
        print("Reviewed course-kind corrections saved.")
    else:
        print("Preview only. No database records changed.")

    print(
        "Tuition, scholarship rules and deposit policies unchanged."
    )


run(apply=globals().get("APPLY", False))