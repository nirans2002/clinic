#!/usr/bin/env python3
"""Create a production .env with fresh random secrets (standard library only).

    python3 deploy/generate_secrets.py --domain clinic.example.com

Refuses to overwrite an existing .env: losing FIELD_ENCRYPTION_KEYS makes encrypted patient data unreadable.
"""
import argparse
import base64
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", help="public host name, e.g. clinic.example.com (Docker/VPS mode)")
    ap.add_argument("--render", action="store_true", help="print the two values to paste into Render instead of writing .env")
    ap.add_argument("--out", default=str(ROOT / ".env"))
    ap.add_argument("--force", action="store_true", help="overwrite an existing file (DANGEROUS)")
    a = ap.parse_args()
    if a.render:
        alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        pw = "".join(secrets.choice(alphabet) for _ in range(20)) + "-" + secrets.token_hex(2)
        print("Paste these into the Render dashboard when the Blueprint asks (and keep the first one OFFLINE too):\n")
        print("FIELD_ENCRYPTION_KEYS      =", base64.urlsafe_b64encode(os.urandom(32)).decode())
        print("ADMIN_BOOTSTRAP_PASSWORD   =", pw, "   (temporary - you must change it at first sign-in)")
        print("\nCopy FIELD_ENCRYPTION_KEYS into a password manager + a printed copy NOW. If it is lost, encrypted patient data is unrecoverable.")
        return
    if not a.domain:
        sys.exit("--domain is required (or use --render).")
    out = Path(a.out)
    if out.exists() and not a.force:
        sys.exit(f"{out} already exists - not overwriting. Replacing it would lose the encryption key and make "
                 "existing patient data unreadable. Move it away deliberately if you really want a new one.")
    domain = a.domain.strip().lower()
    if not re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", domain):
        sys.exit("--domain must be a bare host name such as clinic.example.com (no https://, no path).")

    db_pw = secrets.token_hex(24)  # hex: safe inside a URL, no escaping needed
    values = {
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
        "FIELD_ENCRYPTION_KEYS": base64.urlsafe_b64encode(os.urandom(32)).decode(),  # = Fernet.generate_key()
        "POSTGRES_PASSWORD": db_pw,
        "DATABASE_URL": f"postgres://clinic:{db_pw}@db:5432/clinic",
        "DJANGO_ALLOWED_HOSTS": domain,
        "DJANGO_CSRF_TRUSTED_ORIGINS": f"https://{domain}",
        "SITE_URL": f"https://{domain}",
        "DJANGO_ADMIN_URL": f"console-{secrets.token_hex(6)}/",
        "DEFAULT_FROM_EMAIL": f"clinic@{domain}",
    }
    lines = []
    for line in (ROOT / ".env.example").read_text().splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if m and m.group(1) in values:
            line = f"{m.group(1)}={values[m.group(1)]}"
        lines.append(line)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner-only from the first byte
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {out} (mode 600).")
    print(f"Emergency back-office URL: https://{domain}/{values['DJANGO_ADMIN_URL']}")
    print("\nNOW: copy FIELD_ENCRYPTION_KEYS to a password manager / offline safe, SEPARATE from the server and its backups.")


if __name__ == "__main__":
    main()
