

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

from pathlib import Path
from bs4 import BeautifulSoup

# The distinction: headings tell us what information exists; 
# their surrounding HTML tells us how to extract its value.
html_file = _SCRIPT_ROOT / "abertay_course.html"

soup = BeautifulSoup(
    html_file.read_text(encoding="utf-8",),
    "html.parser"
)
targets = {
    "Start Date",
    "Duration",
    "Award Title",
    "UCAS Code",
    "Annual Tuition Fees",
}
print("--- Detail heading structures ---")

for heading in soup.find_all(["h3", "h4"]):
    label = heading.get_text(" ", strip=True)

    if label not in targets:
        continue
    print(f"\nHeading: {label}")
    print(str(heading.parent)[:2500])

print("\n--- Raw first table ---")

table = soup.find_all("table")
if table is not None:
    print(str(table)[:6000])
else:
    print("No table found.")
