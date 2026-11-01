# Clinic Management – Dr. Nipun's Homoeopathy

Django 5.2 application rebuilt from the single-page prototype (`Dr__Nipun_s_Homoeopathy.html`): same screens and
operations (registration → case/Rx → approve → pharmacy dispense, appointments, billing, catalogue), now with real
user accounts, shared multi-user data, full attribution, and a security layer suitable for confidential doctor–patient data.

## Roles

| | Admin (master) | Doctor | Pharmacist |
|---|---|---|---|
| Create users / assign roles / reset passwords / unlock | ✅ | – | – |
| Audit log (view, filter, CSV export) | ✅ | – | – |
| Clinic letterhead details | ✅ | – | – |
| Patients, visits (case/Rx), appointments | – | ✅ create & edit (shared by all doctors) | – |
| Approve Rx → sends to pharmacy | – | ✅ | – |
| Dispense queue / dispensed history | – | read-only status ("Sent Rx") | ✅ shared by all pharmacists |
| Billing (create bills) | – | ✅ + void | ✅ counter bills |
| Item catalogue (CSV import) | ✅ | ✅ | read-only (price autofill) |

* Data is **shared inside a role**: any doctor sees/edits every patient & visit; any pharmacist sees the whole queue.
* Every record stores **created by / created at / last edited by / last edited at**, plus *approved by* and *dispensed by*
  on prescriptions. Patient, visit and appointment pages show an **Activity history** panel.
* The admin master deliberately **cannot open patient records** (least privilege); pharmacists see only what is needed to
  dispense (name, age, allergies, treatment, fees) – not the clinical history.

## Workflow improvements over the prototype

* *Save & start visit* straight after registration; duplicate-patient warning (same name + phone).
* Appointments carry a status (booked → arrived → completed) with a one-click **Start visit** that links the visit.
* Only one open draft per patient per day (a second doctor continues the same draft instead of creating a duplicate).
* **Optimistic locking**: if two doctors edit the same record, the second save is refused with "changed by Dr X at HH:MM".
* Approved Rx stay editable until dispensed; edits are flagged **Revised** in the pharmacy queue. Dispensed Rx are read-only.
* Daily **token numbers** and bill numbers are allocated atomically (no duplicates, even with simultaneous users).
* Pharmacy queue auto-refreshes; double-dispensing is blocked ("Already dispensed by …"); optional dispense note.
* Allergies are shown prominently on the visit form and in the pharmacy queue.
* Fee totals are computed on the server (the browser total is only a preview). Bills are never deleted – they are **voided with a reason**.
* **Handing over a new user's password:** when the admin creates an account (or resets a password) the one-time temporary password can be **e-mailed** (needs SMTP settings) and/or sent through a one-click **WhatsApp** button (opens a pre-filled message to the user's mobile; the admin presses Send). Unused temporary passwords **expire after 48 h**, and the user must replace it at first sign-in. The password is never stored or written to the audit log (only "sent by e-mail / failed"). Both channels can be switched off with `CREDENTIAL_DELIVERY`.
* **Past visits side panel** on every visit screen: earlier visits (symptoms, investigation, treatment, who saw the patient) beside the form, with a one-click **Copy Rx to this visit** to repeat a prescription. Opening it is audited.
* **Chronic case taking** (patient page, registration screen and visit screen): the classical homoeopathic sheet — presenting complaints with modalities & concomitants, past/family history, personal & physical generals, menstrual/obstetric, mental generals, system-wise particulars, examination, analysis (miasm, totality, repertorisation) and prescription/plan. Collapsible sections, save-and-continue across sittings, shared by all doctors with edit attribution, encrypted at rest, printable.
* **Support tickets**: doctors and pharmacists raise tickets to the admin (category, priority, threaded replies, new-reply badges); users see only their own, the admin sees all and sets the status. Ticket text is encrypted and every action is audited.
* Every password box has a **Show** toggle (auto-hides after 20 s).
* **Print history** on a patient page prints every visit (symptoms, investigations, medicines; optional fees and date range), audited.
* Collection figures live on their own **Collection** tab (date range, per-day and per-payment-method totals).
* Custom dose can be typed next to the standard dose list; every user has a **Profile** tab with the change-password form; phone numbers are validated and normalised (10-digit Indian mobile or +country code).
* Pharmacist "clear dispensed history" replaced by Today / 7 / 30-day filters (nothing is hidden or destroyed).

## Security measures

**Accounts** – Argon2id password hashing · 12+ char passwords with common/similar/numeric checks · admin-issued random temporary
password shown once, **forced change at first login** · lock-out after 5 failures (per username *and* per IP, 30 min; admin can unlock) ·
identical error for unknown user / wrong password · deactivating or resetting a user ends their live sessions · last admin can't be demoted.

**Sessions** – 30-minute sliding idle timeout enforced on the server *and* by the browser (auto-logout) · session ends when the browser closes ·
HttpOnly / Secure / SameSite cookies · logout is POST-only · CSRF on every form.

**Data** – confidential text (address, e-mail, allergies, diagnosis, symptoms, investigations, treatment, remarks, appointment notes)
is **encrypted at the field level (Fernet/AES + HMAC)** before it reaches the database, with key rotation (`python manage.py rotate_encryption`).
Name, phone and reg-no stay searchable. Also use disk/volume encryption on the server. Clinical models are *not* registered in Django admin.

**Audit trail** – append-only log (cannot be edited/deleted in app or admin) of sign-ins, failures, lock-outs, logouts, record
create/edit (field *names* only, never values), **views of patient/visit records**, prints, WhatsApp shares, approvals, dispenses, voids,
user management, denied-access attempts, exports – with user, role, IP, time.

**Web hardening** – strict Content-Security-Policy (no inline scripts/styles, `frame-ancestors 'none'`) · HSTS · HTTPS redirect ·
`no-store` caching of every page · nosniff, referrer and permissions policies · host-header validation · non-default back-office URL ·
role checks on every view (403 + audit entry) · output auto-escaping · CSV-formula-injection guard · upload size/type limits ·
the app **refuses to start in production without a real `SECRET_KEY` and encryption key**.

**Deployment** – Docker: non-root, read-only filesystem, all capabilities dropped; Postgres not exposed; nginx TLS + login rate-limit;
encrypted nightly backup script.

### Things the software cannot do for you
* Keep `FIELD_ENCRYPTION_KEYS` and `.env` safe and backed up **separately from the database** – lose the keys and the encrypted data is unrecoverable.
* Use HTTPS with a real certificate, keep the OS/Docker patched, restrict SSH, back up off-site, and test restores.
* Train staff: one account per person, never share passwords, lock the screen.
* Two-factor authentication is **not** included (recommended next step: `django-otp`/`django-allauth` MFA for doctors & admin).
* WhatsApp share sends text via wa.me (a third party). Set `ENABLE_WHATSAPP_SHARE=0` to disable. Check your local data-protection obligations
  (e.g. India's DPDP Act, 2023) and get legal advice for consent / retention rules – this README is not legal advice.

## Quick start (development)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DJANGO_DEBUG=1                      # dev mode only: insecure fallback keys, no HTTPS redirect
python manage.py migrate
python manage.py createsuperuser           # this becomes the ADMIN MASTER
python manage.py runserver
```
Sign in as the master → **Users → + New user** → choose Doctor or Pharmacist → hand over the one-time temporary password.
Run the tests: `DJANGO_DEBUG=1 python manage.py test` (67 tests: roles, sharing, audit, locking, encryption, lockout, CSP, workflow).

## Production (Docker)

**Full step-by-step guide (secrets, server hardening, HTTPS, backups, updates, rotation): see [DEPLOYMENT.md](DEPLOYMENT.md).**
Quick version:

```bash
python3 deploy/generate_secrets.py --domain your-domain   # writes .env with fresh random secrets
# put TLS certs in deploy/certs/fullchain.pem and privkey.pem
docker compose up -d --build
docker compose exec web python manage.py createsuperuser     # the admin master
```
Open `https://your-domain/`. Without Docker: set the same variables, `python manage.py collectstatic migrate`, run `gunicorn config.wsgi`
behind your own TLS proxy (and set `TRUST_PROXY=1` only if the proxy overwrites `X-Forwarded-For`).

Back-ups: `BACKUP_PASSPHRASE_FILE=/secure/pass.txt deploy/backup.sh` (cron nightly). Key rotation: prepend a new key to `FIELD_ENCRYPTION_KEYS`,
restart, run `python manage.py rotate_encryption`, then drop the old key.

## Layout
`accounts/` users, roles, profile/password, admin user-management · `core/` audit log, encryption field, middleware (CSP, no-store, forced password change)
· `support/` tickets · `clinic/` patients, visits, appointments, pharmacy, billing, catalogue · `templates/`, `static/` · `deploy/` nginx, entrypoint, backup.

Existing data in the prototype lived in the browser/shared DB of that HTML file and is **not** migrated automatically.
