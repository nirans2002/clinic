#!/bin/sh
# Encrypted nightly backup. Cron example:  0 2 * * *  /path/to/deploy/backup.sh
# Needs BACKUP_PASSPHRASE_FILE (chmod 600). Store backups OFF the server, and keep a copy of
# FIELD_ENCRYPTION_KEYS separately: without it the encrypted columns cannot be read.
set -eu
STAMP=$(date +%Y%m%d-%H%M)
mkdir -p backups
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  | gzip | gpg --batch --yes --pinentry-mode loopback --passphrase-file "${BACKUP_PASSPHRASE_FILE:?set BACKUP_PASSPHRASE_FILE}" \
        --symmetric --cipher-algo AES256 -o "backups/clinic-$STAMP.sql.gz.gpg"
find backups -name 'clinic-*.gpg' -mtime +30 -delete
