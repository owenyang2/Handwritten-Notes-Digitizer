"""Write SeaweedFS identities from required deployment secrets."""
import json
import os
from pathlib import Path

bucket = os.environ.get("HND_S3_BUCKET", "hnd-objects")
admin_key = os.environ["HND_S3_ADMIN_ACCESS_KEY"]
admin_secret = os.environ["HND_S3_ADMIN_SECRET_KEY"]
app_key = os.environ["HND_S3_ACCESS_KEY"]
app_secret = os.environ["HND_S3_SECRET_KEY"]
if len(os.environ["HND_S3_ENCRYPTION_KEY"]) < 32:
    raise ValueError("Use an independent encryption secret of at least 32 characters.")
if admin_key == app_key:
    raise ValueError("The backend must use a separate bucket-scoped S3 identity.")
for value in (admin_key, admin_secret, app_key, app_secret):
    if value.strip().lower() in {"", "admin", "password", "changeme", "minioadmin", "hnd"}:
        raise ValueError("Storage credentials must not be empty or known defaults.")

identities = [
    {"name": "storage-admin", "credentials": [{"accessKey": admin_key, "secretKey": admin_secret}], "actions": ["Admin", "Read", "Write", "List"]},
    {"name": "notes-backend", "credentials": [{"accessKey": app_key, "secretKey": app_secret}], "actions": [f"{action}:{bucket}" for action in ("Read", "Write", "List")]},
]
config = Path("/config/s3.json")
config.write_text(json.dumps({"identities": identities}) + "\n")
os.chown(config, 1000, 1000)  # The pinned image runs as seaweed (uid/gid 1000).
config.chmod(0o600)
