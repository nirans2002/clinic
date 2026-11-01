# Deployment guide – secrets and production hosting

Target: one Linux server (Ubuntu 24.04 LTS), Docker Compose, nginx with HTTPS, PostgreSQL.
A 2 vCPU / 2 GB RAM / 40 GB VPS is plenty for a clinic. Prefer a provider/region that satisfies your
data-residency expectations (for India: a Mumbai/Hyderabad region).

---------------------------------------------------------------------------------------------------

## 1. Understand the secrets first

| Secret | What it protects | If it leaks | If it is lost |
|---|---|---|---|
| `DJANGO_SECRET_KEY` | session & CSRF signing | attacker can forge sessions → rotate immediately (everyone is logged out, nothing else lost) | rotate; nothing is lost |
| `FIELD_ENCRYPTION_KEYS` | encrypts diagnosis, symptoms, treatment, allergies, address, e-mail, chronic sheets, tickets | encrypted columns are readable by anyone who also gets the DB | **encrypted data is permanently unreadable** |
| `POSTGRES_PASSWORD` / `DATABASE_URL` | database access | full DB access (still encrypted columns) → rotate | reset inside Postgres |
| `EMAIL_HOST_PASSWORD` | SMTP login | mail abuse → rotate at provider | reset at provider |
| `DJANGO_ADMIN_URL` | hides the emergency back-office | low risk; change it | change it |

**Never** put secrets in the code, in the zip, in chat/e-mail, or in git.

### Generate them (recommended)
```bash
python3 deploy/generate_secrets.py --domain clinic.example.com
```
This writes `.env` (permissions `600`) with a fresh Django key, Fernet key, database password, random back-office
URL and your domain settings. It refuses to overwrite an existing `.env`.

### Or generate them by hand
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"                                  # DJANGO_SECRET_KEY
python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"         # FIELD_ENCRYPTION_KEYS
openssl rand -hex 24                                                                            # POSTGRES_PASSWORD
```
Use a **hex** database password (letters+digits only) so it can sit inside `DATABASE_URL` without URL-escaping.

### Back up the encryption key – now
Copy the `FIELD_ENCRYPTION_KEYS` line into a password manager **and** a printed/offline copy in a safe.
Keep it separate from the server and from database backups: a backup is useless without the key, and a
backup *with* the key beside it defeats the encryption.

---------------------------------------------------------------------------------------------------

## 2. Prepare the server (Ubuntu 24.04)

```bash
# as root on a fresh server
adduser clinic && usermod -aG sudo clinic
rsync --archive --chown=clinic:clinic ~/.ssh /home/clinic        # copy your SSH key, then:
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/; s/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh

apt update && apt -y upgrade
apt -y install ufw unattended-upgrades fail2ban certbot gnupg
ufw default deny incoming && ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
dpkg-reconfigure -plow unattended-upgrades                      # automatic security updates

curl -fsSL https://get.docker.com | sh && usermod -aG docker clinic
```
Log in as `clinic` from now on (open a **new** SSH session first to confirm key login works before closing root).
Enable full-disk/volume encryption at your provider (or LUKS on a local machine) – it protects stolen disks and snapshots.

---------------------------------------------------------------------------------------------------

## 3. Get the code and configure

```bash
sudo mkdir -p /opt/clinic && sudo chown clinic:clinic /opt/clinic
unzip clinic-management-django.zip -d /tmp && cp -a /tmp/clinic/. /opt/clinic/ && cd /opt/clinic
python3 deploy/generate_secrets.py --domain clinic.example.com        # creates .env
nano .env                                                             # review; fill the optional e-mail block if wanted
```
Check in `.env`: `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` and `SITE_URL` match your real domain
(`https://…`), `TRUST_PROXY=1`, and `TIME_ZONE`. The app **refuses to start** if the secrets are missing.

Optional e-mail of temporary passwords: set `EMAIL_HOST`, `EMAIL_PORT` (587), `EMAIL_HOST_USER`,
`EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` (use your provider's SMTP, TLS on). Leave `EMAIL_HOST` empty to disable.

---------------------------------------------------------------------------------------------------

## 4. Domain and HTTPS certificate

1. Create a DNS **A record**: `clinic.example.com → <server IP>` and wait until `dig +short clinic.example.com` shows it.
2. Get a certificate **before** starting the stack (port 80 must be free):
```bash
sudo certbot certonly --standalone -d clinic.example.com -m you@example.com --agree-tos
sudo mkdir -p deploy/certs
sudo cp /etc/letsencrypt/live/clinic.example.com/fullchain.pem /etc/letsencrypt/live/clinic.example.com/privkey.pem deploy/certs/
sudo chmod 600 deploy/certs/privkey.pem
```
3. Automatic renewal (stops nginx for a few seconds, renews, copies, restarts):
```bash
sudo tee /etc/letsencrypt/renewal-hooks/pre/clinic-stop.sh >/dev/null <<'H'
#!/bin/sh
cd /opt/clinic && docker compose stop nginx
H
sudo tee /etc/letsencrypt/renewal-hooks/deploy/clinic-copy.sh >/dev/null <<'H'
#!/bin/sh
D=/etc/letsencrypt/live/clinic.example.com
cp $D/fullchain.pem $D/privkey.pem /opt/clinic/deploy/certs/ && chmod 600 /opt/clinic/deploy/certs/privkey.pem
H
sudo tee /etc/letsencrypt/renewal-hooks/post/clinic-start.sh >/dev/null <<'H'
#!/bin/sh
cd /opt/clinic && docker compose start nginx
H
sudo chmod +x /etc/letsencrypt/renewal-hooks/*/clinic-*.sh
sudo certbot renew --dry-run
```
*No public domain (office-only server)?* Use a real domain you own with DNS-challenge certificates, or a private CA
(e.g. `mkcert`/internal CA) installed on each office PC. Do not run the clinic over plain HTTP, even on the LAN.

---------------------------------------------------------------------------------------------------

## 5. Launch

```bash
cd /opt/clinic
docker compose up -d --build
docker compose logs -f web            # wait for "Listening at: http://0.0.0.0:8000", Ctrl-C to leave
docker compose exec web python manage.py createsuperuser     # this is the ADMIN MASTER account
```
Open `https://clinic.example.com/`, sign in as the master (you will be asked to set a new password – 12+ characters),
then **Users → + New user** to create doctors and pharmacists and hand over their temporary passwords.
Fill in **Clinic details** (letterhead) and import the item **Catalogue**.

### Verify
```bash
curl -sI https://clinic.example.com/accounts/login/ | egrep -i "strict-transport|content-security|x-frame|cache-control"
curl -sI http://clinic.example.com/ | head -1                                    # expect 301 to https
docker compose exec web python manage.py check --deploy                          # only the HSTS-preload note is expected
docker compose ps                                                                 # db, web, nginx all "running"
```
Also test: a wrong password 5 times locks the account (admin → Users → Unlock); idle for 30 min logs you out;
`https://clinic.example.com/admin/` returns 404 (the back-office lives at the random `DJANGO_ADMIN_URL`).

---------------------------------------------------------------------------------------------------

## 6. Backups (do this on day one)

```bash
sudo mkdir -p /root/clinic && sudo sh -c 'openssl rand -base64 32 > /root/clinic/backup.pass' && sudo chmod 600 /root/clinic/backup.pass
# nightly at 02:00
( crontab -l 2>/dev/null; echo '0 2 * * * cd /opt/clinic && BACKUP_PASSPHRASE_FILE=/root/clinic/backup.pass ./deploy/backup.sh' ) | crontab -
```
`backups/*.sql.gz.gpg` are encrypted with the passphrase file. Copy them **off the server** every day
(provider object storage, another machine – e.g. `rclone`/`rsync`). Store `backup.pass` in your password manager too.

**Test a restore** (on a spare machine or after `docker compose down -v` in a test copy):
```bash
gpg --batch --pinentry-mode loopback --passphrase-file /root/clinic/backup.pass -d backups/clinic-YYYYMMDD-HHMM.sql.gz.gpg \
  | gunzip | docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```
The restored data is readable only with the original `FIELD_ENCRYPTION_KEYS`.

---------------------------------------------------------------------------------------------------

## 7. Updating the software

```bash
cd /opt/clinic && ./deploy/backup.sh                       # always back up first
# replace the code (unzip the new version over /opt/clinic – keep .env and deploy/certs), then:
docker compose up -d --build                               # migrations run automatically on start
docker compose logs --tail 50 web
```
Keep the OS patched (`unattended-upgrades` handles security updates; reboot monthly) and rebuild the images regularly
(`docker compose build --pull`) to pick up Python/nginx/Postgres patches.

---------------------------------------------------------------------------------------------------

## 8. Rotating secrets

* **Django secret key** (suspected leak, or yearly): put a new `DJANGO_SECRET_KEY` in `.env`, `docker compose up -d`. Everyone must sign in again.
* **Encryption key**: generate a new key and put it **first**: `FIELD_ENCRYPTION_KEYS=NEWKEY,OLDKEY`, `docker compose up -d`,
  then `docker compose exec web python manage.py rotate_encryption`. Only after that finishes (and a fresh backup is taken)
  remove `OLDKEY`. Keep old keys for as long as old backups must stay readable.
* **Database password**: `docker compose exec db psql -U clinic -c "ALTER USER clinic PASSWORD 'newhex'"`, update both
  `POSTGRES_PASSWORD` and `DATABASE_URL` in `.env`, `docker compose up -d`.
* **A staff member leaves**: admin → Users → untick *Active* (their sessions end immediately).

---------------------------------------------------------------------------------------------------

## 9. Without Docker (alternative)

Install Python 3.12, PostgreSQL 16, nginx. Create a venv, `pip install -r requirements.txt`, put the variables from `.env`
in `/etc/clinic.env` (`chmod 600`), then run as a systemd service:
```ini
# /etc/systemd/system/clinic.service
[Service]
User=clinic
WorkingDirectory=/opt/clinic
EnvironmentFile=/etc/clinic.env
ExecStartPre=/opt/clinic/.venv/bin/python manage.py migrate --noinput
ExecStartPre=/opt/clinic/.venv/bin/python manage.py collectstatic --noinput
ExecStart=/opt/clinic/.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3
Restart=always
NoNewPrivileges=true
ProtectSystem=full
[Install]
WantedBy=multi-user.target
```
Point nginx at `127.0.0.1:8000` using `deploy/nginx.conf` + `deploy/proxy.inc` as the template (TLS, login rate-limit,
`X-Forwarded-For` **overwritten**). Never expose gunicorn directly to the internet.

---------------------------------------------------------------------------------------------------

## 10. Go-live checklist

- [ ] `.env` is `chmod 600`, not in git, and `FIELD_ENCRYPTION_KEYS` is stored offline in two places
- [ ] HTTPS works, HTTP redirects, certificate renewal dry-run passes
- [ ] Firewall allows only SSH/80/443; SSH password login disabled; fail2ban + unattended-upgrades on
- [ ] Master admin password is strong and unique; one named account per doctor/pharmacist; no shared logins
- [ ] Nightly encrypted backup runs, is copied off-site, and a **restore has been tested**
- [ ] Disk/volume encryption enabled; server console/provider account protected with 2FA
- [ ] Staff briefed: lock the screen, never share passwords, report problems via **Support → Raise a ticket**
- [ ] You have taken legal advice on consent, retention and breach-notification duties for patient data (e.g. DPDP Act 2023)

### Troubleshooting
| Symptom | Likely cause |
|---|---|
| `web` exits: "Set DJANGO_SECRET_KEY…" / "Set FIELD_ENCRYPTION_KEYS…" | `.env` missing or values empty – rerun the generator |
| 400 Bad Request | domain not in `DJANGO_ALLOWED_HOSTS` |
| 403 CSRF failed on login | `DJANGO_CSRF_TRUSTED_ORIGINS` must be `https://your-domain`; check you browse via HTTPS |
| Redirect loop | `TRUST_PROXY=1` missing while behind nginx |
| nginx won't start | `deploy/certs/fullchain.pem` / `privkey.pem` missing |
| Fields show "[unreadable – check encryption keys]" | wrong/missing `FIELD_ENCRYPTION_KEYS` – restore the original key |
| Everyone logged out suddenly | `DJANGO_SECRET_KEY` changed (expected after rotation) |
