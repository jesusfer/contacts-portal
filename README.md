# Contacts Portal

A Django + Preact CRM for agents, company contacts, calls, appointments, and
inbox tracking. Django sessions protect the JSON API; SQLite stores application
data. The same container serves the frontend and Django static files using
WhiteNoise.

## Run with Docker Compose

1. Create a private `.env` file containing `DJANGO_SECRET_KEY`, generated with:
   ```
   python -c "import secrets; print(secrets.token_urlsafe(64))"
   ```
   Do not commit this file. Keep the key stable across restarts.
2. Run `docker compose up --build -d`.
3. Create an administrator:
   ```
   docker compose exec portal python manage.py createsuperuser
   ```
4. Open http://localhost:8000/admin/ to create a company, local users, and an
   **Agent** for each user linked to that company. Create contacts and optionally
   import received emails through the admin.
5. Open http://localhost:8000/ and sign in.

The `sqlite_data` and `django_media` named volumes persist the SQLite database
and Django media independently. Migrations run on container startup. Do not use
`docker compose down -v` unless you intend to delete that data. Back up the
database using SQLite's backup facility (or stop the service before copying the
database), and back up the media volume separately. Run a single application
container with SQLite; heavier write workloads require a database migration.

The image runs as UID/GID 10001. If using bind mounts instead of named volumes,
ensure both mounted directories are writable by that user. Media is persistent
but is not exposed publicly by WhiteNoise; this application has no upload UI.

## Local development

Requires Python 3.12+ and Node.js 22.12+.

```
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
cd frontend
npm ci
npm run build
cd ..
python manage.py runserver
```

Provision companies and agents in `/admin/` as above. For hot reload, keep
Django running on port 8000 and run `npm run dev` in `frontend/`; Vite proxies
the API and authentication routes to Django. Development enables Django debug
mode by default; never use this mode for deployment.

Validation:

```
python manage.py test
python manage.py check
python manage.py makemigrations --check --dry-run
cd frontend
npm run build
npm audit
```

## Authentication and OIDC

Local authentication uses Django usernames/passwords; public password signup
is disabled. OIDC uses django-allauth's OpenID Connect provider. Configure
`OIDC_PROVIDERS` as a JSON array in the environment (or a single-quoted value
in `.env`), with one object per identity provider:

```
[
  {
    "id": "work",
    "name": "Work identity",
    "server_url": "https://identity.example.org",
    "client_id": "contacts-portal",
    "client_secret": "<provider-issued-secret>"
  }
]
```

Use each provider's issuer/discovery base URL for `server_url`. Register
`https://your-host/accounts/oidc/work/login/callback/` as its redirect URI,
replacing `work` with that provider's unique ID. Provider secrets must stay in
the environment, not source control. Multiple provider identities can link to
one local user.

Accounts are **not automatically linked by email**. Administrators can link
provider subjects to existing users using allauth's Social Accounts admin;
signed-in users can also manage connections at `/accounts/3rdparty/`.
New OIDC users receive local accounts but cannot access CRM data until an
administrator provisions their Agent/company association.

## Deployment settings

| Environment variable | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Required with debug disabled; stable private signing key |
| `DJANGO_DEBUG` | `0` in deployment, `1` only for local development |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames, without schemes |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated origins, including schemes |
| `DJANGO_SECURE_COOKIES` | Set to `1` behind HTTPS; Compose defaults to `0` for localhost HTTP |
| `DJANGO_SECURE_SSL_REDIRECT` | Set to `1` to redirect HTTP to HTTPS |
| `DJANGO_TRUST_PROXY` | Set to `1` only when a trusted proxy overwrites `X-Forwarded-Proto` |
| `SQLITE_PATH` | Database file; image default `/app/data/db.sqlite3` |
| `MEDIA_ROOT` | Media directory; image default `/app/media` |
| `OIDC_PROVIDERS` | JSON array of configured OIDC providers |

For production, put a TLS reverse proxy in front of the localhost-bound port,
configure the public hostname and HTTPS CSRF origin, and enable secure cookies.
Only enable forwarded-protocol trust when the application is unreachable
except through a proxy that strips untrusted forwarded headers.

## Domain and behavior

- Each user has one Agent/company association (their employer). Agents see
  only their own calls, appointments, and emails. The company/contact
  directory is shared by provisioned agents; contacts can belong to different
  companies, allowing an agent to work with multiple customer organizations.
- Calls have `scheduled`, `completed`, `missed`, `rescheduled`, or `cancelled`
  status. Only completed calls accept contact ratings, on a 1–5 scale.
- Each appointment is linked to one call, with a start, end, and notes.
  Moving a pending appointment records the call as rescheduled.
- The dashboard highlights upcoming appointments and past calls needing
  status, summary, or rating updates. The calendar also has next/past agendas.
- KPIs include call counts, completed-call ratings, and breakdowns by contact
  and company. Missed/rescheduled percentages use non-scheduled calls as the
  denominator; unrated calls do not affect rating averages. Rescheduling is
  remembered even when the call is subsequently completed.
- Inbox controls track read/replied state. They **do not send or fetch email**.
  Received messages can be entered/imported by an administrator or posted to
  the agent-scoped `/api/inbox/` endpoint. Marking replied records an action
  performed outside the portal.

All mutation endpoints require Django's CSRF token and a signed-in session;
API routes live under `/api/`. User and agent provisioning is deliberately
restricted to administrators; agents can add companies and contacts to the
shared directory.