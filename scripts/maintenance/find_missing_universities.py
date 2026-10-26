

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

# Read all university IDs from universities.csv
university_ids = set()
with open('data/universities.csv', newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        university_ids.add(row['university_id'])

print(f"Total universities in universities.csv: {len(university_ids)}")

# Check courses.csv for missing university IDs
missing_universities = set()
course_university_ids = set()

with open('data/courses.csv', newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        uni_id = row['university_id']
        course_university_ids.add(uni_id)
        if uni_id not in university_ids:
            missing_universities.add(uni_id)

print(f"Total unique universities referenced in courses.csv: {len(course_university_ids)}")
print(f"\nMissing university IDs: {len(missing_universities)}")

if missing_universities:
    print("\nMissing university IDs:")
    for uni_id in sorted(missing_universities):
        print(f"  - {uni_id}")
else:
    print("\nAll university IDs in courses.csv exist in universities.csv!")
