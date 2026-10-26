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

output_file = Path(__file__).resolve().parent / "abertay_catalogue.html"

# Saves the response HTML for inspection.
output_file.write_text(
    response.text,
    encoding="utf-8"
)
print("html saved to", output_file)