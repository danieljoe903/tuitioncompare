

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

import csv

# Read all course IDs from courses.csv
course_ids = set()
with open('data/courses.csv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        course_ids.add(row['course_id'])

print(f"Total courses in courses.csv: {len(course_ids)}")

# Check tuition_fees.csv for missing course IDs
missing_courses = set()
with open('data/tuition_fees.csv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row['course_id'] not in course_ids:
            missing_courses.add(row['course_id'])

if missing_courses:
    print(f"\n❌ Found {len(missing_courses)} course IDs in tuition_fees.csv that don't exist in courses.csv:")
    for course_id in sorted(missing_courses):
        print(f"  - {course_id}")
else:
    print("\n✅ All course IDs in tuition_fees.csv exist in courses.csv")
