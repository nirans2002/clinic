#!/bin/sh
set -e
python manage.py check --deploy --fail-level ERROR
python manage.py migrate --noinput
python manage.py bootstrap_admin
# PORT is provided by Render (default 10000); WEB_CONCURRENCY lets small instances use fewer workers.
exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers "${WEB_CONCURRENCY:-3}" --timeout 60 \
     --access-logfile - --error-logfile - --limit-request-line 4094
