"""Verify SSR page JSON is valid for browser JSON.parse."""
import json
import re

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

for path in ["/", "/mlb", "/mlb/game/822716"]:
    r = client.get(path)
    m = re.search(r'id="ntg-page-data">(.+?)</script>', r.text, re.DOTALL)
    if not m:
        print(f"{path}: MISSING ntg-page-data")
        continue
    payload = m.group(1)
    try:
        data = json.loads(payload)
        print(f"{path}: OK kind={data.get('kind')}")
    except json.JSONDecodeError as exc:
        print(f"{path}: JSON FAIL {exc}")
        for bad in ("NaN", "Infinity", "-Infinity"):
            if bad in payload:
                print(f"  contains {bad}")
