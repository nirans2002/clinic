# Hosting on a Google Compute Engine (E2) VM

One VM runs the whole stack with Docker Compose (nginx → Django/gunicorn → PostgreSQL), exactly as in
[DEPLOYMENT.md](DEPLOYMENT.md). This guide adds the Google Cloud parts: project, network, VM, static IP, SSH without
opening port 22, snapshots, off-site backups to Cloud Storage, monitoring, and cost/security notes.

```
 Staff browsers ──HTTPS 443──▶ [ static external IP ]──▶ VM (E2, Ubuntu 24.04, Shielded VM)
                                                          ├─ nginx  (TLS, login rate-limit)
 Admin laptop ──IAP tunnel (no public SSH)──────────────▶ ├─ web    (Django + gunicorn)
                                                          └─ db     (PostgreSQL, volume on the VM disk)
 VM disk ── daily snapshots (14 days)      encrypted pg_dump ──▶ Cloud Storage bucket (90 days)
```

> Prices and limits change – use the **Google Cloud Pricing Calculator** and check the current docs before committing.
> The commands below were written for the `gcloud` CLI; run them in your terminal or in **Cloud Shell**.

---------------------------------------------------------------------------------------------------

## 0. Decisions and prerequisites

| Decision | Recommendation |
|---|---|
| Machine type | **e2-medium** (2 shared vCPU, 4 GB) for a clinic with up to ~15 concurrent users. e2-small (2 GB) works for a handful of users if you add swap (§6). Not the free-tier e2-micro (US regions only, 1 GB). |
| Region | **asia-south1 (Mumbai)** or **asia-south2 (Delhi)** – lowest latency and Indian data location. Pick a zone, e.g. `asia-south1-b`. |
| OS | Ubuntu 24.04 LTS (Shielded VM image). |
| Disk | 40 GB `pd-balanced` boot disk (PostgreSQL volume lives on it). |
| Access | SSH **only** through Identity-Aware Proxy (IAP) + OS Login. No port 22 on the internet. |

You need: a Google Cloud account with **billing enabled**, the `gcloud` CLI (or Cloud Shell), a **domain name** you control,
and the project zip (`clinic-management-django.zip`). Turn on **2-Step Verification** for every Google account that can
administer the project – these accounts can reach patient data.

---------------------------------------------------------------------------------------------------

## 1. Project and variables

```bash
gcloud auth login
gcloud projects create clinic-prod-12345 --name="Clinic"          # pick a globally unique id; or reuse a project
gcloud config set project clinic-prod-12345
gcloud billing projects link clinic-prod-12345 --billing-account=XXXXXX-XXXXXX-XXXXXX   # gcloud billing accounts list

export PROJECT_ID=$(gcloud config get-value project)
export REGION=asia-south1
export ZONE=asia-south1-b
export VM=clinic-vm
export DOMAIN=clinic.example.com
export BUCKET=${PROJECT_ID}-clinic-backups
export ME=you@example.com                                          # the Google account that will administer the VM

gcloud config set compute/region $REGION && gcloud config set compute/zone $ZONE
gcloud services enable compute.googleapis.com iap.googleapis.com storage.googleapis.com logging.googleapis.com monitoring.googleapis.com
```

---------------------------------------------------------------------------------------------------

## 2. Network and firewall

A dedicated VPC; only web traffic is public, SSH comes through IAP's documented range `35.235.240.0/20`.

```bash
gcloud compute networks create clinic-net --subnet-mode=custom
gcloud compute networks subnets create clinic-subnet --network=clinic-net --region=$REGION --range=10.10.0.0/24

gcloud compute firewall-rules create clinic-web     --network=clinic-net --direction=INGRESS --action=ALLOW \
  --rules=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 --target-tags=clinic-web
gcloud compute firewall-rules create clinic-iap-ssh --network=clinic-net --direction=INGRESS --action=ALLOW \
  --rules=tcp:22 --source-ranges=35.235.240.0/20 --target-tags=clinic-iap
```
Never create rules for 5432 (PostgreSQL) or 8000 (gunicorn): they are only reachable inside Docker.
(A custom VPC has no default allow-SSH/RDP rules, which is what you want.)

---------------------------------------------------------------------------------------------------

## 3. Service account, static IP and the VM

```bash
# least-privilege identity for the VM (it can only write logs/metrics and upload backups – see §8)
gcloud iam service-accounts create clinic-vm --display-name="Clinic VM"
export SA=clinic-vm@${PROJECT_ID}.iam.gserviceaccount.com

# static external IP (so DNS and the certificate keep working across restarts)
gcloud compute addresses create clinic-ip --region=$REGION
export IP=$(gcloud compute addresses describe clinic-ip --region=$REGION --format='value(address)'); echo $IP

gcloud compute instances create $VM \
  --zone=$ZONE --machine-type=e2-medium \
  --subnet=clinic-subnet --address=$IP \
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --boot-disk-size=40GB --boot-disk-type=pd-balanced \
  --tags=clinic-web,clinic-iap --labels=app=clinic \
  --service-account=$SA --scopes=storage-rw,logging-write,monitoring-write \
  --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
  --metadata=enable-oslogin=TRUE,block-project-ssh-keys=TRUE \
  --deletion-protection
```
Notes: E2 VMs live-migrate during Google's host maintenance and restart automatically after a failure by default.
Disks are encrypted at rest by Google automatically (you can add your own key with Cloud KMS/CMEK if policy requires it).

### Who may log in
```bash
gcloud projects add-iam-policy-binding $PROJECT_ID --member="user:$ME" --role="roles/iap.tunnelResourceAccessor"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="user:$ME" --role="roles/compute.osAdminLogin"      # sudo
gcloud iam service-accounts add-iam-policy-binding $SA --member="user:$ME" --role="roles/iam.serviceAccountUser"
```
Give each administrator their own account – never share a login. Remove the binding when someone leaves.

---------------------------------------------------------------------------------------------------

## 4. DNS

Create an **A record** `clinic.example.com → $IP` at your registrar (or in Cloud DNS). Check:
```bash
dig +short $DOMAIN        # must print the static IP before you request a certificate
```

---------------------------------------------------------------------------------------------------

## 5. Connect (no public SSH)

```bash
gcloud compute ssh $VM --zone=$ZONE --tunnel-through-iap
```
If it fails: confirm the `clinic-iap-ssh` firewall rule, the three IAM bindings, and wait a minute after creating them.

---------------------------------------------------------------------------------------------------

## 6. Prepare the VM (run on the VM)

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install unattended-upgrades certbot gnupg unzip
sudo dpkg-reconfigure -plow unattended-upgrades          # automatic security updates
sudo snap install google-cloud-cli --classic              # only if `gcloud` is missing (needed for backup upload)

curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER
# keep container logs from filling the disk
sudo tee /etc/docker/daemon.json >/dev/null <<'J'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "5" } }
J
sudo systemctl restart docker

# e2-small only (2 GB RAM): a 2 GB swap file prevents out-of-memory kills
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
exit        # log in again so the docker group applies
```

### Optional: monitoring agent
```bash
curl -sSO https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh && sudo bash add-google-cloud-ops-agent-repo.sh --also-install
```
This sends VM CPU/memory/disk metrics and system logs to Cloud Monitoring/Logging.

---------------------------------------------------------------------------------------------------

## 7. Install the application

From your **own computer** (where the zip is):
```bash
gcloud compute scp clinic-management-django.zip $VM:~ --zone=$ZONE --tunnel-through-iap
```
On the VM:
```bash
sudo mkdir -p /opt/clinic && sudo chown $USER:$USER /opt/clinic
unzip ~/clinic-management-django.zip -d /tmp && cp -a /tmp/clinic/. /opt/clinic/ && rm -rf /tmp/clinic ~/clinic-management-django.zip
cd /opt/clinic
python3 deploy/generate_secrets.py --domain clinic.example.com       # writes .env (mode 600) with fresh random secrets
```
**Back up the encryption key immediately.** Copy the `FIELD_ENCRYPTION_KEYS` line to a password manager and a printed copy
kept in a safe. If you also want a copy in Google Secret Manager (readable only by named administrators, *not* by the VM's
service account), run this **from your own computer**:
```bash
printf '%s' 'PASTE_THE_KEY' | gcloud secrets create clinic-field-key --data-file=- --replication-policy=user-managed --locations=$REGION
```
(Needs `gcloud services enable secretmanager.googleapis.com`.) Treat that as a convenience – the offline copy is the real backup.

Review `.env` (`nano .env`): domain values, `TRUST_PROXY=1`, `TIME_ZONE`. Optional e-mail settings are described in DEPLOYMENT.md §3.

---------------------------------------------------------------------------------------------------

## 8. HTTPS certificate

Port 80 must be free and DNS must point at the VM (§4).
```bash
cd /opt/clinic
sudo certbot certonly --standalone -d clinic.example.com -m you@example.com --agree-tos
mkdir -p deploy/certs
sudo cp /etc/letsencrypt/live/clinic.example.com/{fullchain,privkey}.pem deploy/certs/ && sudo chmod 600 deploy/certs/privkey.pem

# auto-renewal (stops nginx for a few seconds, renews, copies, restarts)
sudo tee /etc/letsencrypt/renewal-hooks/pre/clinic-stop.sh   >/dev/null <<'H'
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
(Alternative: put a Google Cloud HTTP(S) Load Balancer with a Google-managed certificate in front of the VM. It is more
robust but costs more and needs extra steps; the approach above is fine for one clinic.)

---------------------------------------------------------------------------------------------------

## 9. Launch

```bash
cd /opt/clinic
docker compose up -d --build
docker compose logs --tail 50 web                              # look for "Listening at"
docker compose exec web python manage.py createsuperuser       # the ADMIN MASTER
```
Browse to `https://clinic.example.com/`, sign in, choose a new password, then create doctors/pharmacists under **Users**.

Verify:
```bash
curl -sI https://clinic.example.com/accounts/login/ | egrep -i "strict-transport|content-security|x-frame"
curl -s  https://clinic.example.com/healthz                    # prints: ok
curl -sI http://clinic.example.com/ | head -1                  # 301 → https
docker compose exec web python manage.py check --deploy
```
Then sign in once and open **Audit log** (as admin): the **IP** column should show *your* public IP. (Direct-to-VM traffic
shows the real address.)

---------------------------------------------------------------------------------------------------

## 10. Backups – three layers

**Layer 1 – disk snapshots (whole-VM recovery).** Daily, kept 14 days, stored in the Asia multi-region:
```bash
gcloud compute resource-policies create snapshot-schedule clinic-daily --region=$REGION \
  --max-retention-days=14 --on-source-disk-delete=keep-auto-snapshots \
  --daily-schedule --start-time=21:00 --storage-location=asia            # 21:00 UTC = 02:30 IST
gcloud compute disks add-resource-policies $VM --zone=$ZONE --resource-policies=clinic-daily
```
Snapshots are crash-consistent; they are your "the VM is gone" safety net, not your only backup.

**Layer 2 – encrypted database dumps to Cloud Storage (off the VM).** From your computer:
```bash
gcloud storage buckets create gs://$BUCKET --location=$REGION --uniform-bucket-level-access --public-access-prevention
cat > lifecycle.json <<'J'
{"rule":[{"action":{"type":"Delete"},"condition":{"age":90}}]}
J
gcloud storage buckets update gs://$BUCKET --lifecycle-file=lifecycle.json
# the VM may ADD objects but cannot delete or overwrite them – a compromised VM cannot destroy your backups
gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member="serviceAccount:$SA" --role="roles/storage.objectCreator"
```
On the VM:
```bash
sudo mkdir -p /root/clinic && sudo sh -c 'openssl rand -base64 32 > /root/clinic/backup.pass' && sudo chmod 600 /root/clinic/backup.pass
sudo cat /root/clinic/backup.pass          # store this passphrase in your password manager too
( sudo crontab -l 2>/dev/null; echo "0 2 * * * cd /opt/clinic && GCS_BUCKET=$BUCKET BACKUP_PASSPHRASE_FILE=/root/clinic/backup.pass ./deploy/backup.sh" ) | sudo crontab -
sudo GCS_BUCKET=$BUCKET BACKUP_PASSPHRASE_FILE=/root/clinic/backup.pass /opt/clinic/deploy/backup.sh && gcloud storage ls gs://$BUCKET
```
(Replace `$BUCKET` with the literal bucket name inside the cron line if the shell variable is not set on the VM.)
The dumps are AES-256 encrypted with your passphrase, and the sensitive clinical columns are *also* encrypted by the application key.

**Layer 3 – offline keys.** `FIELD_ENCRYPTION_KEYS` and `backup.pass` in a password manager + paper copy. Without the
encryption key, any backup of clinical text is unreadable.

**Restore test (do this once before go-live, on a spare VM or a scratch copy):**
```bash
gcloud storage cp gs://$BUCKET/clinic-YYYYMMDD-HHMM.sql.gz.gpg .
gpg --batch --pinentry-mode loopback --passphrase-file /root/clinic/backup.pass -d clinic-YYYYMMDD-HHMM.sql.gz.gpg \
  | gunzip | docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```
To rebuild from a snapshot instead: Compute Engine → Snapshots → *Create instance / disk from snapshot*, attach the static IP.

---------------------------------------------------------------------------------------------------

## 11. Monitoring and alerts

* **Uptime check:** Cloud Console → *Monitoring → Uptime checks → Create*: protocol HTTPS, host `clinic.example.com`,
  path `/healthz`, check every 5 min, expect HTTP 200. Add an **alerting policy** that e-mails/SMS the clinic admin when it fails.
  (`/healthz` needs no login and touches no patient data.)
* **Resource alerts:** with the Ops Agent installed, alert when disk > 80 %, memory > 90 %, or CPU > 90 % for 15 min.
* **Budget alert:** *Billing → Budgets & alerts* so a surprise cost reaches you.
* **Application audit trail:** the app's own **Audit log** (admin) records sign-ins, failures, lock-outs, views, edits, prints.
  **Cloud Audit Logs** separately record who changed the Google Cloud setup (admin activity logs are on by default).

---------------------------------------------------------------------------------------------------

## 12. Operating it

```bash
# update the app (take a backup first)
cd /opt/clinic && sudo GCS_BUCKET=$BUCKET BACKUP_PASSPHRASE_FILE=/root/clinic/backup.pass ./deploy/backup.sh
#   copy the new zip up with `gcloud compute scp`, unzip over /opt/clinic (keep .env and deploy/certs), then:
docker compose up -d --build && docker compose logs --tail 50 web

# OS patches: unattended-upgrades installs security fixes; reboot monthly:  sudo reboot
# resize later (stop → change → start):
gcloud compute instances stop $VM --zone=$ZONE
gcloud compute instances set-machine-type $VM --zone=$ZONE --machine-type=e2-standard-2
gcloud compute instances start $VM --zone=$ZONE
```
Secret and key rotation: see DEPLOYMENT.md §8. Staff leaving: untick **Active** under *Users*; remove their Google IAM bindings if they had VM access.

---------------------------------------------------------------------------------------------------

## 13. Security hardening specific to Google Cloud

- [ ] SSH only via IAP + OS Login; no public port 22; `block-project-ssh-keys=TRUE`; per-person IAM, 2-Step Verification enforced
- [ ] Shielded VM (secure boot, vTPM, integrity monitoring) – enabled in §3
- [ ] VM service account has **no** project roles; only the bucket `objectCreator` binding; broad `cloud-platform` scope is *not* used
- [ ] Ports 5432/8000 closed; only 80/443 public; HTTP redirects to HTTPS; HSTS on
- [ ] `--deletion-protection` on the VM; bucket has public-access prevention and a lifecycle rule
- [ ] Snapshot schedule attached; **restore tested**; budget + uptime alerts configured
- [ ] Optional: Cloud KMS customer-managed key for disk/bucket encryption; Cloud Armor in front of a load balancer for DDoS/WAF
- [ ] You have taken legal advice on consent, retention and breach-notification duties (e.g. India's DPDP Act 2023)

### Optional: managed PostgreSQL (Cloud SQL) instead of the container
Cloud SQL gives automated backups and point-in-time recovery, at higher cost and some extra setup: create a PostgreSQL
instance with a **private IP** in `clinic-net` (or use the Cloud SQL Auth Proxy), create a database/user, set `DATABASE_URL`
in `.env` to it, and remove the `db` service from `docker-compose.yml`. Keep `FIELD_ENCRYPTION_KEYS` exactly as before.

---------------------------------------------------------------------------------------------------

## 14. Tear-down / troubleshooting

```bash
gcloud compute instances update $VM --zone=$ZONE --no-deletion-protection
gcloud compute instances delete $VM --zone=$ZONE
gcloud compute addresses delete clinic-ip --region=$REGION      # stop paying for the reserved IP
```
(Keep the bucket and snapshots as long as you need the backups.)

| Symptom | Likely cause / fix |
|---|---|
| `gcloud compute ssh` hangs or "failed to connect" | missing `clinic-iap-ssh` rule, IAM roles not granted yet, or the VM lacks the `clinic-iap` tag |
| certbot "connection refused / timeout" | DNS not pointing at the static IP yet, `clinic-web` tag/rule missing, or something already on port 80 |
| Browser: site can't be reached | firewall rule `clinic-web` not applied (check the VM's network tags) |
| 400 Bad Request | domain not in `DJANGO_ALLOWED_HOSTS` in `.env` |
| CSRF error on login | `DJANGO_CSRF_TRUSTED_ORIGINS` must be `https://your-domain`; use the HTTPS address |
| `web` container restarts | `docker compose logs web` – usually a missing/invalid value in `.env` |
| Slow / killed processes on e2-small | add the swap file (§6) or move to e2-medium |
| Backup upload "AccessDenied" | VM service account lacks `objectCreator` on the bucket, or the VM was created without the `storage-rw` scope |
| Disk filling up | Docker logs (set rotation in §6), old `backups/` (the script prunes after 30 days), or snapshots are billed separately |
