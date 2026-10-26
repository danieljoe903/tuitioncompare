

# Locate the project root after this script has been relocated.
import sys as _script_sys
from pathlib import Path as _ScriptPath

_script_locations = []
if "__file__" in globals():
    _script_locations.extend(_ScriptPath(__file__).resolve().parents)
_script_locations.extend([
    _ScriptPath.cwd(),
    *_ScriptPath.cwd().parents,
])

_SCRIPT_ROOT = next(
    (
        location for location in _script_locations
        if (location / "manage.py").is_file()
        and (location / "app").is_dir()
    ),
    None,
)
if _SCRIPT_ROOT is None:
    raise RuntimeError(
        "Cannot locate the Academy project. Run from its main folder."
    )

if str(_SCRIPT_ROOT) not in _script_sys.path:
    _script_sys.path.insert(0, str(_SCRIPT_ROOT))

import json
from collections import Counter

from app.models import Course, TuitionSource
from app.views import abertay_fee_verification_error


courses = (
    Course.objects.filter(
        university__name__iexact="Abertay University",
        university__country="UK",
        is_active=True
    )
    .prefetch_related(
        "tuitions",
        "tuitions__source_metadata",
    )
    .order_by("course_id")
)

verified = []
unresolved = []

for course in courses:
    row = {
        "course_id": course.course_id,
        "title": course.title,
        "level": course.level,
        "start_month": course.start_month,
        "start_year": course.start_year,
        "duration": course.duration,
        "course_url": course.course_url,
    }

    tuitions = [
        tuition
        for tuition in course.tuitions.all()
        if tuition.residency == "INTL"
    ]

    if len(tuitions) != 1:
        row["reason"] = (
            f"Expected one INTL tuition; found {len(tuitions)}."
        )
        unresolved.append(row)
        continue

    tuition = tuitions[0]

    try:
        source = tuition.source_metadata
    except TuitionSource.DoesNotExist:
        source = None

    reason = abertay_fee_verification_error(tuition, source)

    row["stored_tuition"] = str(tuition.tuition_fee)
    row["academic_year"] = (
        source.academic_year if source else None
    )
    row["source_url"] = (
        source.source_url if source else None
    )

    if reason:
        row["reason"] = reason
        unresolved.append(row)
    else:
        verified.append(row)

report = {
    "database_changes": False,
    "total_courses": len(verified) + len(unresolved),
    "verified_fees": len(verified),
    "unresolved_fees": len(unresolved),
    "verified_by_academic_year": dict(
        Counter(row["academic_year"] for row in verified)
    ),
    "unresolved_by_level": dict(
        Counter(row["level"] for row in unresolved)
    ),
    "unresolved": unresolved,
}

print(json.dumps(report, indent=2, ensure_ascii=False))
print("\nRead-only audit. No database records changed.")