

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

# Fetch the official catalogue page.

from pathlib import Path
import requests

URL =  "https://www.abertay.ac.uk/course-search/undergraduate/"


# Downloads the page returned by the server
response = requests.get(
    URL,
    
    # Downloads the page returned by the serve
    headers={
       "User-Agent": "TuitionCompareSourceInspector/1.0"  
    },
    timeout=(10, 30) #Sets connection and read timeouts.
)

# Stops if the server returns an HTTP error.
response.raise_for_status()

# Shows where the request ended after redirects.
print("Status", response.status_code)
print("FINAL URL",response.url)
print("Content type", response.headers.get("Content-Type"))
print("charachters", len(response.text))

output_file = _SCRIPT_ROOT / "abertay_catalogue.html"

# Saves the response HTML for inspection.
output_file.write_text(
    response.text,
    encoding="utf-8"
)
print("html saved to", output_file)