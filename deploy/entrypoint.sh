#!/bin/sh
set -e
python manage.py check --deploy --fail-level ERROR
python manage.py migrate --noinput
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 60 \
     --access-logfile - --error-logfile - --limit-request-line 4094
