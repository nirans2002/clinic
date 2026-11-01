"""
Clinic Management – settings.

All deployment-specific values come from environment variables (see .env.example).
With DJANGO_DEBUG unset/0 the site runs in hardened "production" mode and REFUSES
to start without a real SECRET_KEY and field-encryption key.
"""
import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [x.strip() for x in env(name, default).split(",") if x.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)

# --------------------------------------------------------------------------- secrets
SECRET_KEY = env("DJANGO_SECRET_KEY")
FIELD_ENCRYPTION_KEYS = env_list("FIELD_ENCRYPTION_KEYS")  # newest first; Fernet keys
if DEBUG:
    SECRET_KEY = SECRET_KEY or "dev-only-insecure-secret-key-change-me-0123456789abcdef0123456789"
    # Fixed *development-only* Fernet key. Never used when DEBUG is off.
    FIELD_ENCRYPTION_KEYS = FIELD_ENCRYPTION_KEYS or ["ZGV2LW9ubHkta2V5LURPLU5PVC1VU0UtSU4tUFJPRCE="]
else:
    if not SECRET_KEY or len(SECRET_KEY) < 50:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY (50+ random characters).")
    if not FIELD_ENCRYPTION_KEYS:
        raise ImproperlyConfigured(
            "Set FIELD_ENCRYPTION_KEYS (comma-separated Fernet keys, newest first). "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1" if DEBUG else "")
if not DEBUG and not ALLOWED_HOSTS:
    raise ImproperlyConfigured("Set DJANGO_ALLOWED_HOSTS (comma-separated host names).")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")

# --------------------------------------------------------------------------- apps
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "axes",
    "accounts",
    "core",
    "clinic",
    "support",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "axes.middleware.AxesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
    "core.middleware.ForcePasswordChangeMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.app",
            ],
        },
    },
]

# --------------------------------------------------------------------------- database
DATABASES = {
    "default": dj_database_url.config(
        env="DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=60,
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------- auth
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",  # brute-force lockout
    "django.contrib.auth.backends.ModelBackend",
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "clinic:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",  # only to verify/upgrade legacy hashes
]
if "test" in __import__("sys").argv[1:2]:  # speed up the test suite only
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Lock an account after repeated failures (admin can unlock from the Users page).
AXES_FAILURE_LIMIT = int(env("AXES_FAILURE_LIMIT", "5"))
AXES_COOLOFF_TIME = timedelta(minutes=int(env("AXES_COOLOFF_MINUTES", "30")))
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]  # each tracked independently (stops spraying too)
AXES_RESET_ON_SUCCESS = True
if env_bool("TRUST_PROXY", False):  # behind our own reverse proxy: read the client IP it appends
    AXES_IPWARE_PROXY_COUNT = 1
    AXES_IPWARE_META_PRECEDENCE_ORDER = ["HTTP_X_FORWARDED_FOR", "REMOTE_ADDR"]
AXES_LOCKOUT_TEMPLATE = "registration/locked.html"
AXES_ENABLE_ADMIN = False

# --------------------------------------------------------------------------- sessions / cookies
IDLE_TIMEOUT_MINUTES = int(env("IDLE_TIMEOUT_MINUTES", "30"))
SESSION_COOKIE_AGE = IDLE_TIMEOUT_MINUTES * 60
SESSION_SAVE_EVERY_REQUEST = True          # sliding idle timeout
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_NAME = "clinic_sid"
CSRF_COOKIE_NAME = "clinic_csrf"

# --------------------------------------------------------------------------- transport security
TRUST_PROXY = env_bool("TRUST_PROXY", False)  # True only behind your own nginx/Caddy
if TRUST_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", not DEBUG)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = 0 if DEBUG else int(env("SECURE_HSTS_SECONDS", str(60 * 60 * 24 * 365)))
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

# --------------------------------------------------------------------------- e-mail (optional)
EMAIL_HOST = env("EMAIL_HOST", "")
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_PORT = int(env("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
    EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
    EMAIL_TIMEOUT = 10
elif DEBUG:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"  # dev: prints to the terminal
EMAIL_ENABLED = bool(EMAIL_HOST) or DEBUG
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "clinic@localhost")
SITE_URL = env("SITE_URL", "").rstrip("/")  # public https address, used in credential messages
# Which channels the admin may use to hand over a new user's temporary password.
CREDENTIAL_DELIVERY = env_list("CREDENTIAL_DELIVERY", "email,whatsapp")
TEMP_PASSWORD_HOURS = int(env("TEMP_PASSWORD_HOURS", "48"))  # unused temporary passwords expire

# --------------------------------------------------------------------------- app options
ADMIN_URL = env("DJANGO_ADMIN_URL", "console-admin/").strip("/") + "/"  # not /admin/
ENABLE_WHATSAPP_SHARE = env_bool("ENABLE_WHATSAPP_SHARE", True)
DEFAULT_COUNTRY_CODE = env("DEFAULT_COUNTRY_CODE", "91")
FIRST_REG_NO = 1001

# --------------------------------------------------------------------------- i18n / static
LANGUAGE_CODE = "en-in"
TIME_ZONE = env("TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# --------------------------------------------------------------------------- logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"std": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "std"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {"django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}
