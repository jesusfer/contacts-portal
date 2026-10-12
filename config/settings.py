import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
GRAPH_SCOPES = [
    "openid", "profile", "email", "offline_access",
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Calendars.Read",
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Presence.Read",
]
TEAMS_CLIENT_IDS = [
    "1fec8e78-bce4-4aaf-ab1b-5451cc387264",
    "5e3ce6c0-2b1f-4285-8d4b-75ee78787346",
]
DEBUG = os.environ.get("DJANGO_DEBUG", os.environ.get("DEBUG", "true")).lower() in {"1", "true", "yes"}
EXTERNAL_CALL_LOG_LEVEL = os.environ.get("EXTERNAL_CALL_LOG_LEVEL", "INFO").upper()
if EXTERNAL_CALL_LOG_LEVEL not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
    raise ImproperlyConfigured("EXTERNAL_CALL_LOG_LEVEL must be a standard logging level.")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "external_calls": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "external_calls_console": {"class": "logging.StreamHandler", "formatter": "external_calls"},
    },
    "loggers": {
        "crm.external_calls": {
            "handlers": ["external_calls_console"],
            "level": EXTERNAL_CALL_LOG_LEVEL,
            "propagate": False,
        },
    },
}
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
    for origin in os.environ.get(
        "DJANGO_CSRF_TRUSTED_ORIGINS", os.environ.get("CSRF_TRUSTED_ORIGINS", "")
    ).split(",")
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
        app_settings = {"server_url": provider["server_url"].rstrip("/")}
        graph_enabled = provider["id"].lower().startswith("entraid")
        scope = provider.get("scope", GRAPH_SCOPES if graph_enabled else None)
        if scope is not None:
            if not isinstance(scope, list) or not scope or any(
                not isinstance(value, str) or not value.strip() or any(c.isspace() for c in value)
                for value in scope
            ):
                raise ImproperlyConfigured("OIDC scope must be a non-empty array of scope strings.")
            if graph_enabled and not set(GRAPH_SCOPES).issubset(scope):
                raise ImproperlyConfigured("entraid providers require the delegated Graph scopes and offline_access.")
            app_settings["scope"] = scope
        if graph_enabled:
            parts = url.path.rstrip("/").split("/")
            tenant_id = None
            if len(parts) == 3 and parts[2] == "v2.0":
                if parts[1] == "organizations":
                    tenant_id = "organizations"
                else:
                    try:
                        tenant_id = str(UUID(parts[1]))
                    except ValueError:
                        pass
            if (
                not tenant_id or url.scheme != "https"
                or url.netloc != "login.microsoftonline.com"
            ):
                raise ImproperlyConfigured(
                    "entraid providers require a tenant UUID or organizations issuer on login.microsoftonline.com."
                )
            app_settings["graph_tenant_id"] = tenant_id
        teams = provider.get("teams")
        if teams is not None:
            if not graph_enabled or not isinstance(teams, dict):
                raise ImproperlyConfigured("teams settings require an entraid provider and a JSON object.")
            if set(teams) - {"tenant_id", "audience", "resource", "authorized_client_ids"}:
                raise ImproperlyConfigured("Unknown teams OIDC setting.")
            try:
                client_id = str(UUID(provider["client_id"]))
                configured_tenant = teams.get("tenant_id", tenant_id)
                teams_tenant = (
                    "organizations" if configured_tenant == "organizations"
                    else str(UUID(configured_tenant))
                )
            except (ValueError, TypeError, AttributeError) as exc:
                raise ImproperlyConfigured(
                    "Teams tenant_id must be a UUID or organizations."
                ) from exc
            if teams_tenant != tenant_id:
                raise ImproperlyConfigured("Teams tenant_id must match the OIDC issuer tenant.")
            audience = teams.get("audience", client_id)
            if audience != client_id:
                raise ImproperlyConfigured("Teams audience must equal client_id for v2 access tokens.")
            resource = teams.get("resource")
            resource_url = urlsplit(resource) if isinstance(resource, str) else None
            if (
                not resource_url or resource_url.scheme != "api" or not resource_url.hostname
                or resource_url.username or resource_url.password or resource_url.query
                or resource_url.fragment or resource_url.path != f"/{client_id}"
            ):
                raise ImproperlyConfigured("Teams resource must be api://<portal hostname>/<client_id>.")
            clients = teams.get("authorized_client_ids", TEAMS_CLIENT_IDS)
            try:
                if not isinstance(clients, list) or not clients:
                    raise ValueError
                clients = [str(UUID(value)) for value in clients]
            except (ValueError, TypeError, AttributeError) as exc:
                raise ImproperlyConfigured("Teams authorized_client_ids must be a non-empty UUID array.") from exc
            app_settings["teams"] = {
                "tenant_id": teams_tenant, "audience": audience,
                "resource": resource, "authorized_client_ids": clients,
            }
        apps.append({
            "provider_id": identifier,
            "name": provider["name"],
            "client_id": client_id if teams is not None else provider["client_id"],
            "secret": provider["client_secret"],
            "settings": app_settings,
        })
    return {"openid_connect": {
        "APPS": apps,
        "provider_class": "crm.openid_connect.TenantAwareOpenIDConnectProvider",
    }}


SOCIALACCOUNT_PROVIDERS = configure_oidc(os.environ.get("OIDC_PROVIDERS", "[]"), debug=DEBUG)
TEAMS_ENABLED = any(
    app["settings"].get("teams")
    for app in SOCIALACCOUNT_PROVIDERS["openid_connect"]["APPS"]
)
TEAMS_GRAPH_ONLY = os.environ.get("TEAMS_GRAPH_ONLY", "true").lower() in {"1", "true", "yes"}
TEAMS_BOT_APP_ID = os.environ.get("TEAMS_BOT_APP_ID", "")
TEAMS_BOT_APP_PASSWORD = os.environ.get("TEAMS_BOT_APP_PASSWORD", "")
TEAMS_BOT_TENANT_ID = os.environ.get("TEAMS_BOT_TENANT_ID", "")
TEAMS_BOT_PROVIDER_ID = os.environ.get("TEAMS_BOT_PROVIDER_ID", "")
TEAMS_API_PROVIDER_ID = os.environ.get("TEAMS_API_PROVIDER_ID", "")
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
    "crm.middleware.TeamsFrameMiddleware",
    "django.middleware.common.CommonMiddleware",
    "crm.middleware.ProfileCsrfViewMiddleware",
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
    "DIRS": [BASE_DIR / "templates"],
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
SOCIALACCOUNT_STORE_TOKENS = False
ACCOUNT_RATE_LIMITS = {"login": "20/m/ip", "login_failed": "5/5m/key"}
CSRF_FAILURE_VIEW = "crm.views.csrf_failure"
SESSION_COOKIE_HTTPONLY = True
SECURE_COOKIES = os.environ.get("DJANGO_SECURE_COOKIES", str(not DEBUG)).lower() in {"1", "true", "yes"}
SESSION_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_SECURE = SECURE_COOKIES
if TEAMS_ENABLED and not DEBUG and not SECURE_COOKIES:
    raise ImproperlyConfigured("Teams integration requires secure cookies in production.")
SESSION_COOKIE_SAMESITE = "None" if TEAMS_ENABLED and SECURE_COOKIES else "Lax"
CSRF_COOKIE_SAMESITE = "None" if TEAMS_ENABLED and SECURE_COOKIES else "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "false").lower() in {"1", "true", "yes"}
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
