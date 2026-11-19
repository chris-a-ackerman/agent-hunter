"""POST/GET a JSON request and log the full request and response to log.md.

Usage: python3 -I call.py <label> <METHOD> <url> [body.json]
"""
import datetime
import json
import sys
import urllib.error
import urllib.request

LOG = "/Users/chrisackerman/coldstart3/log.md"

label, method, url = sys.argv[1], sys.argv[2], sys.argv[3]
body = open(sys.argv[4]).read() if len(sys.argv) > 4 else None

req = urllib.request.Request(url, method=method, data=body.encode() if body else None,
                             headers={"content-type": "application/json"} if body else {})
ts = datetime.datetime.now().isoformat(timespec="seconds")
try:
    with urllib.request.urlopen(req, timeout=300) as r:
        status, text = r.status, r.read().decode()
except urllib.error.HTTPError as e:
    status, text = e.code, e.read().decode()
except Exception as e:  # connection errors etc.
    status, text = "ERROR", f"{type(e).__name__}: {e}"


def pretty(s):
    try:
        return json.dumps(json.loads(s), indent=2)
    except Exception:
        return s


with open(LOG, "a") as f:
    f.write(f"\n## {label}\n\n**Time:** {ts}\n\n### Request\n\n`{method} {url}`\n\n")
    if body:
        f.write(f"```json\n{pretty(body)}\n```\n")
    f.write(f"\n### Response\n\nStatus: `{status}`\n\n```json\n{pretty(text)}\n```\n")

print(status)
print(text)
