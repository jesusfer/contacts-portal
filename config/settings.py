import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("DJANGO_DEBUG", os.environ.get("DEBUG", "true")).lower() in {"1", "true", "yes"}
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", os.environ.get("SECRET_KEY"))
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY (or SECRET_KEY) is required when DEBUG is false.")
    SECRET_KEY = secrets.token_urlsafe(64)
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "DJANGO_ALLOWED_HOSTS", os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
    ).split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]


def configure_oidc(raw, *, debug):
    """Translate environment providers into allauth's generic OIDC applications."""
    try:
        providers = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured("OIDC_PROVIDERS must be a JSON array.") from exc
    if not isinstance(providers, list):
        raise ImproperlyConfigured("OIDC_PROVIDERS must be a JSON array.")
    apps = []
    seen = set()
    for provider in providers:
        required = ("id", "name", "server_url", "client_id", "client_secret")
        if not isinstance(provider, dict) or any(
            not isinstance(provider.get(key), str) or not provider[key].strip()
            for key in required
        ):
            raise ImproperlyConfigured("Each OIDC provider requires non-empty " + ", ".join(required))
        identifier = provider["id"]
        if not all(c.isascii() and (c.isalnum() or c in "_-") for c in identifier):
            raise ImproperlyConfigured("OIDC provider IDs must contain only letters, digits, - or _.")
        if identifier in seen:
            raise ImproperlyConfigured("OIDC provider IDs must be unique.")
        seen.add(identifier)
        url = urlsplit(provider["server_url"])
        if (
            url.scheme not in ({"http", "https"} if debug else {"https"})
            or not url.netloc or url.username or url.password or url.query or url.fragment
        ):
            raise ImproperlyConfigured("OIDC server_url requires an HTTPS issuer (HTTP allowed in DEBUG).")
        apps.append({
            "provider_id": identifier,
            "name": provider["name"],
            "client_id": provider["client_id"],
            "secret": provider["client_secret"],
            "settings": {"server_url": provider["server_url"].rstrip("/")},
        })
    return {"openid_connect": {"APPS": apps}}


SOCIALACCOUNT_PROVIDERS = configure_oidc(os.environ.get("OIDC_PROVIDERS", "[]"), debug=DEBUG)
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.openid_connect",
    "crm",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ],
    },
}]
WSGI_APPLICATION = "config.wsgi.application"
SQLITE_PATH = Path(os.environ.get("SQLITE_PATH", str(BASE_DIR / "data" / "db.sqlite3")))
SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": SQLITE_PATH}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"
ACCOUNT_ADAPTER = "crm.adapters.AccountAdapter"
SOCIALACCOUNT_ADAPTER = "crm.adapters.SocialAccountAdapter"
ACCOUNT_LOGIN_METHODS = {"username"}
ACCOUNT_SIGNUP_FIELDS = ["username*", "email", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = False
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = False
SOCIALACCOUNT_LOGIN_ON_GET = False
ACCOUNT_RATE_LIMITS = {"login": "20/m/ip", "login_failed": "5/5m/key"}
CSRF_FAILURE_VIEW = "crm.views.csrf_failure"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_SSL_REDIRECT = not DEBUG
if os.environ.get("DJANGO_TRUST_PROXY", "false").lower() in {"1", "true", "yes"}:
    # Enable only behind a trusted proxy that overwrites the forwarded header.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
STATICFILES_DIRS = [FRONTEND_DIST] if FRONTEND_DIST.exists() else []
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", str(BASE_DIR / "media")))
MEDIA_URL = "/media/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
