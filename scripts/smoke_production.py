"""Smoke-test a disposable production stack. Creates then deletes one account.

Run with HND_SMOKE_URL pointing to the frontend origin, e.g. http://localhost:8080.
Uses a synthetic source and a manual correction; does not test recognition quality.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

root = os.environ.get("HND_SMOKE_URL", "http://localhost:8080").rstrip("/") + "/api"
token = None


def request(method, path, data=None, content_type="application/json"):
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else None
    with urlopen(Request(root + path, data=body, headers=headers, method=method), timeout=30) as response:
        return json.load(response)


health = request("GET", "/health")
assert set(health) == {"status", "capabilities"} and health["status"] == "ok"
identity = "stack-smoke-" + uuid.uuid4().hex
account = request("POST", "/auth/register", {
    "username": identity, "email": identity + "@example.test", "password": uuid.uuid4().hex,
})
token = account["access_token"]
try:
    boundary = uuid.uuid4().hex
    source = Path(__file__).resolve().parents[1] / "frontend/e2e/fixtures/offline-source.png"
    multipart = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic.png"\r\n'
        'Content-Type: image/png\r\n\r\n'
    ).encode() + source.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    document = request("POST", "/documents/import", multipart, f"multipart/form-data; boundary={boundary}")
    page = document["pages"][0]
    region = request("POST", f'/pages/{page["page_id"]}/regions', {
        "bbox": [20, 20, 400, 80], "content_type": "text", "coordinate_space": page["coordinate_space"],
    })
    correction = "Saved correction from the production stack smoke test."
    saved = request("POST", f'/regions/{region["region_id"]}/review', {
        "reviewed_text": correction, "excluded": False, "include_in_training": False,
    })
    regions = request("GET", f'/pages/{page["page_id"]}/regions')
    assert any(item["reviewed_text"] == correction for item in regions)
    export = request("POST", f'/documents/{document["document_id"]}/exports', {
        "format": "markdown", "revision_id": saved["document_revision_id"],
    })
    assert correction in export["text"]
finally:
    deleted = request("DELETE", "/account", {"confirmation": "DELETE"})
    assert deleted["status"] == "completed", deleted["status"]
print("Production API: import, source crop, save/reload, revision export and account purge passed.")
