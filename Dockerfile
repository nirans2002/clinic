FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Static files are collected at build time (the runtime container has a read-only filesystem).
# These throw-away values exist only for this one command and are not kept in the image environment.
RUN DJANGO_SECRET_KEY="build-time-only-$(python -c 'import secrets;print(secrets.token_urlsafe(60))')" \
    FIELD_ENCRYPTION_KEYS="$(python -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" \
    DJANGO_ALLOWED_HOSTS=build python manage.py collectstatic --noinput \
 && chmod +x deploy/entrypoint.sh && chown -R root:root /app && chmod -R a+rX /app
USER app
EXPOSE 8000
ENTRYPOINT ["deploy/entrypoint.sh"]
