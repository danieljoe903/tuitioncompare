

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
from pathlib import Path

from django.conf import settings
from app.models import Course

courses = list(
    Course.objects.filter(
        university__name__iexact="Abertay University",
        level__in=["UG", "TOPUP"],
    ).order_by("course_id")
    .values(
        "course_id",
        "title",
        "level",
        "duration",
        "course_url",
        "start_year",
    )
)
output_file = Path(settings.BASE_DIR) / 'data/universities/uk/abertay/abertay_database_courses.json'
output_file.write_text(
    json.dumps(courses, indent=2, ensure_ascii=False),
    encoding="utf-8"
)
print("Database courses exported:", len(courses))
print("Saved to:", output_file)