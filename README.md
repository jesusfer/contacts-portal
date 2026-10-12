# Contacts Portal

A Django 6 + Preact CRM for agents, company contacts, calls, appointments, and
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
but is not exposed publicly by WhiteNoise. Profile pictures are served through
an authenticated, private endpoint.

## Automated image builds

The **Build and publish Docker image** GitHub Actions workflow builds the Dockerfile on
pushes to `main` that change `crm/`, `frontend/`, or `Dockerfile`. It also
watches the remaining image inputs (`config/`, Python requirements, `manage.py`,
the entrypoint, `.dockerignore`, and authentication `templates/`) and its own
workflow definition.
Builds use GitHub Actions caching and publish commit-SHA image tags.

Configure these repository settings under **Settings → Secrets and variables
→ Actions** before running the workflow:

| Type | Name | Value |
| --- | --- | --- |
| Variable | `REGISTRY_HOST` | Registry hostname, optionally with a port, without a scheme or trailing slash (for example, `ghcr.io` or `registry.example.com:5000`) |
| Secret | `REGISTRY_USERNAME` | Registry login username |
| Secret | `REGISTRY_PASSWORD` | Registry password or access token with push permission |

The published image is
`<REGISTRY_HOST>/jesusfer/contacts-portal:<commit-sha>` (the namespace/image
path follows the GitHub repository name). Ensure the credentials can push to
that image path. For GHCR, use a token with package write access as the password;
this workflow uses the configured secrets rather than `GITHUB_TOKEN`.
Credentials are used only for registry login, not passed into the image build.

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

## Demo data

After running migrations, seed a fictional workspace with companies, contacts,
calls covering every status, past/upcoming appointments, contact ratings, and
inbox messages with different read/replied states:

```
python manage.py seed_demo
python manage.py changepassword demo-agent
```

Sign in as `demo-agent` using the password you set. A newly created demo user
has no usable password until you set one, and has no administrator privileges.
No sample password is bundled with the application.

For Docker Compose:

```
docker compose exec portal python manage.py seed_demo
docker compose exec portal python manage.py changepassword demo-agent
```

To populate an existing, empty agent workspace instead:

```
python manage.py seed_demo --username your-username
```

The command preserves existing user credentials and agent/company associations.
It skips the entire operation if the selected agent already has calls or inbox
messages, so repeat runs do not duplicate activity, reset edits, or move
appointments. Demo company/contact directory entries are reused, not overwritten;
they are shared with other agents just like ordinary directory entries.
Appointments are relative to the initial seed time. Seeding is explicit and
never runs automatically on startup; use a development/demo database rather
than a production database. Inbox messages are fictional tracking records,
not actual received or sent emails.

## Authentication and OIDC

Teams tabs default to a Graph-only SPA with automatic Teams SSO and direct SDK
Graph reads. **Open full dashboard** explicitly uses the existing server-side
OBO flow. Set `TEAMS_GRAPH_ONLY=0` (default: `1`) to retain automatic OBO/dashboard
startup. See [Teams integration](teams.md) for both authentication diagrams,
Entra setup, and deployment requirements.

Local authentication uses Django usernames/passwords; public password signup
is disabled. Login throttling uses Django's default process-local cache, so its
limits apply independently to each worker; put shared rate limiting at the
reverse proxy for an internet-facing deployment.
OIDC uses django-allauth's OpenID Connect provider. Configure
`OIDC_PROVIDERS` as a JSON array in the environment (or a single-quoted value
in `.env`), with one object per identity provider:

```
[
  {
    "id": "entraid-work",
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

OIDC login, error, cancellation, signup, connection management, and callback
redirect pages share the portal's styled authentication layout. These templates
are included in the Docker image; rebuild and redeploy the image to apply changes.

The dashboard shows the current session's authentication source: **Local** for
username/password sign-ins, or the provider's exact configured `name` (for
example, Entra ID or Ping) for OIDC sign-ins. This reflects the service used
to sign in, not all identities linked to the account. Connecting another
identity does not change the displayed source; signing in again does.
Sessions created before source tracking was introduced display Local until
the next sign-in. If the active provider is removed from configuration, the
dashboard displays OIDC rather than guessing a provider name.

## Teams customer messaging extension

The Python Bot Framework endpoint at `/api/teams/messages/` lets provisioned
agents search customer company names and insert an Adaptive Card containing
the company name, customer ID, contact count, and contact names/emails into
a Teams conversation. Select a search result to add it to the compose box,
then send it. No calls, appointments, inbox records, or Graph tokens are shared.

1. Register a **single-tenant Azure Bot** with a client-secret credential, enable
   its **Microsoft Teams** channel, and set its HTTPS messaging endpoint to
   `https://<portal-host>/api/teams/messages/`. The bot registration can be
   separate from the portal's OIDC application.
2. Configure these environment variables (Compose passes them to the portal):
   - `TEAMS_BOT_APP_ID`: the bot's Microsoft application/client ID.
   - `TEAMS_BOT_APP_PASSWORD`: its client-secret **value**, kept only in private
     environment configuration; never include it in the Teams app package.
   - `TEAMS_BOT_TENANT_ID`: the allowed Entra tenant UUID.
   - `TEAMS_BOT_PROVIDER_ID`: an existing `entraid`-prefixed `OIDC_PROVIDERS`
     provider ID whose issuer accepts that tenant.
3. Each user must first sign in to the portal through that OIDC provider so
   their Entra object/tenant identity is linked to an active local account.
   An administrator must provision their **Agent**. The extension uses the
   verified Bot Framework channel identity, not browser session cookies, email
   matching, or Graph permissions. Unlinked or unprovisioned users receive an
   authorization error; sign in to the portal and retry.
4. Customize `teams/manifest.json`: replace `<TEAMS_APP_ID>` with a new Teams app
   UUID, `<TEAMS_BOT_APP_ID>` with the bot client ID, and `<PORTAL_HOST>` with
   your public hostname. Set real developer, privacy, and terms URLs (the portal
   does not provide privacy/terms pages). Supply your app's `color.png` (192×192)
   and transparent white `outline.png` (32×32). Zip the manifest and both icons
   at the archive root, then upload the package through Teams **Manage your
   apps → Upload an app** or your organization's app catalog. To add this command
   to an existing tab app, merge `composeExtensions` into its manifest instead.

The extension is disabled (HTTP 503) unless all four bot settings are present.
It always requires a signed Bot Framework channel bearer token, including in
debug mode; the Bot Framework Emulator is deliberately not accepted.
Search is case-insensitive, returns up to five results per page, and accepts
up to 200 characters. Cards show up to 20 contacts and are size-bounded for
Teams; any omitted contacts are explicitly noted. Companies and contacts use
the existing shared directory, not the agent's company memberships.
Sending a card shares its contents with everyone in that conversation,
including external participants; only share customer data with authorized
recipients. Cards are snapshots and do not update after portal edits.
Test the deployed bot in Teams by searching a customer (including one with no
contacts), selecting it, and sending its card.

## User profiles

For Microsoft Teams static/configurable tabs, automatic SSO, and delegated
Microsoft Graph calendar, inbox, profile, and presence integration, see
[teams.md](teams.md). Providers whose configured ID starts with `entraid`
use Graph-backed, read-only profile/calendar/inbox views; the local behavior
below applies to other logins.

Signed-in users can open **Profile** to update their first and last names and
upload, replace, or remove their profile picture. Profile access is available
even before an administrator provisions an Agent. Names and pictures update
in the signed-in interface after saving.

Username and email are displayed read-only because they identify the account;
profile changes do not modify credentials, identity connections, company
membership, or permissions.

Pictures must be JPEG, PNG, or WebP, at most 5 MiB and 4096 pixels on either
side. The backend validates image content, corrects orientation, reduces it to
at most 512 × 512 pixels, and stores a JPEG without source metadata. Uploaded
filenames are not used as storage paths. Only the current user can retrieve
their picture; the media directory is not publicly browsable. Pictures persist
in the existing `django_media` volume.

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
  performed outside the portal. This describes the local CRM inbox; `entraid`
  sessions instead display the user's read-only Microsoft Graph Inbox and
  default calendar, without importing those items into CRM records.

All mutation endpoints require Django's CSRF token and a signed-in session;
API routes live under `/api/`. User and agent provisioning is deliberately
restricted to administrators; agents can add companies and contacts to the
shared directory.