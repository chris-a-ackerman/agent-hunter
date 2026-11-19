"""Send one HTTP request, print the response, and append both to log.md.

Usage: python3 -I call.py <log.md> <label> <METHOD> <url> [<json body file>]
"""
import datetime, json, sys, urllib.request, urllib.error

log_path, label, method, url = sys.argv[1:5]
body = open(sys.argv[5]).read() if len(sys.argv) > 5 else None

req = urllib.request.Request(url, method=method, data=body.encode() if body else None,
                             headers={"content-type": "application/json"} if body else {})
ts = datetime.datetime.now().isoformat(timespec="seconds")
try:
    with urllib.request.urlopen(req, timeout=300) as r:
        status, text = r.status, r.read().decode()
except urllib.error.HTTPError as e:
    status, text = e.code, e.read().decode()
except Exception as e:  # network failure
    status, text = None, f"{type(e).__name__}: {e}"

with open(log_path, "a") as f:
    f.write(f"\n## {label}\n\n_{ts}_ — `{method} {url}`\n\n")
    if body:
        f.write("**Request body:**\n\n```json\n" + body.strip() + "\n```\n\n")
    f.write(f"**Response (HTTP {status}):**\n\n```json\n" + text.strip() + "\n```\n")
print(status)
print(text)
