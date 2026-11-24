from pathlib import Path
from getpass import getpass
import hashlib
import secrets
import re

path = Path(__file__).resolve().parent / ".env"
if not path.exists():
    raise SystemExit(".env not found")
old = path.read_text(encoding="utf-8-sig")
password = getpass("New password (at least 12 characters): ")
if len(password) < 12 or password != getpass("Repeat password: "):
    raise SystemExit("Passwords do not match or are too short. No changes made.")
salt = secrets.token_bytes(16)
digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000)
old = re.sub(r"(?m)^\s*AUTH_(?:USER|PASSWORD_HASH|ORIGIN)\s*=.*$", "", old)
path.write_text(old.rstrip() + "\nAUTH_USER=teacher\nAUTH_ORIGIN=http://127.0.0.1:8000\nAUTH_PASSWORD_HASH=" + salt.hex() + ":" + digest.hex() + "\n", encoding="utf-8")
print("Authentication configured. Restart the server.")
