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
pushes to `main` that change `backend/`, `frontend/`, `Dockerfile`, `.dockerignore`,
or its own workflow definition.
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

### External-call tracing

The backend logs Graph requests (including photos), Entra signing-key fetches,
on-behalf-of exchanges and refreshes, and allauth OIDC discovery, token,
userinfo and signing-key HTTP requests to the console, including when debug
mode is disabled. Follow them with `docker compose logs -f portal`.

Each outbound call has matching `external_call.start` and
`external_call.complete` (or `external_call.failed`) entries with a unique
`call_id`, service, operation, method, hostname, elapsed milliseconds and HTTP
status. Microsoft `request-id` and `client-request-id` response headers
(falling back to `x-ms-request-id` and `x-ms-correlation-id`) are
included only when they are valid UUIDs, for upstream troubleshooting.
HTTP non-success responses and transport failures are warnings; failures
include the exception type, never its potentially sensitive message.

`EXTERNAL_CALL_LOG_LEVEL` defaults to `INFO`. Set it to `WARNING` to show only
unsuccessful calls, or `ERROR` to silence this tracer. Request/response bodies,
query strings, URL paths (except fixed Graph operation labels), credentials,
authorization headers, cookies and personal profile/mail/calendar data are
never logged. Tracing does not enable verbose logging in HTTP libraries or
the Bot Framework SDK.

Requires Python 3.12+ and Node.js 22.12+.

```
python -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
python backend/manage.py migrate
python backend/manage.py createsuperuser
cd frontend
npm ci
npm run build
cd ..
python backend/manage.py runserver
```

Provision companies and agents in `/admin/` as above. For hot reload, keep
Django running on port 8000 and run `npm run dev` in `frontend/`; Vite proxies
the API and authentication routes to Django. Development enables Django debug
mode by default; never use this mode for deployment.

Validation:

```
python backend/manage.py test crm
python backend/manage.py check
python backend/manage.py makemigrations --check --dry-run
cd frontend
npm run build
npm audit
```

## Demo data

After running migrations, seed a fictional workspace with companies, contacts,
calls covering every status, past/upcoming appointments, contact ratings, and
inbox messages with different read/replied states:

```
python backend/manage.py seed_demo
python backend/manage.py changepassword demo-agent
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
python backend/manage.py seed_demo --username your-username
```

The command preserves existing user credentials and agent/company associations.
It also associates the selected agent with both demo contact companies, so all
seeded calls and emails involve contacts at companies associated with that agent.
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
startup. See the [Teams guides](docs/README.md) for tab behavior, authentication,
messaging extensions, and deployment requirements.

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
4. Customize `teams/tabs-bot-extension/manifest.json` using the placeholders below. Set real
   developer, privacy, and terms URLs (the portal
   does not provide privacy/terms pages). The `teams/` directory includes the
   manifest's Gather-branded `color.png` (192×192) and transparent white
   `outline.png` (32×32). Zip the customized manifest and both bundled icons
   at the archive root, then upload the package through Teams **Manage your
   apps → Upload an app** or your organization's app catalog. To add these features
   to an existing app, merge `staticTabs`, `configurableTabs`, `composeExtensions`,
   and `webApplicationInfo` into its manifest instead.

### Combined Teams app manifest and SSO

The manifest includes the **Gather** personal static tab at `/teams/tab/`,
configurable customer tabs for teams and group chats at `/teams/config/`, and
the customer search messaging extension. Tab configuration selects the provider
and customer and saves a `/teams/customer/` content URL.

Replace every occurrence of these explicit placeholder variables before
packaging; Teams does **not** substitute them automatically:

| Manifest placeholder | Replacement |
| --- | --- |
| `<TEAMS_APP_ID>` | The Teams app's unique UUID (keep it stable when updating an installed app) |
| `<PORTAL_HOST>` | Public HTTPS portal hostname, without a scheme or trailing slash |
| `<TEAMS_BOT_APP_ID>` | Bot registration client ID, matching `TEAMS_BOT_APP_ID` |
| `<TEAMS_SSO_APP_ID>` | Portal Entra/OIDC application client ID, matching the selected provider's `client_id`; not the bot ID unless both registrations intentionally share an application |
| `<TEAMS_OIDC_PROVIDER_ID>` | The selected `entraid`-prefixed `OIDC_PROVIDERS` provider `id`, also matching `TEAMS_BOT_PROVIDER_ID` for this single-tenant app |

For tab SSO, configure that OIDC provider's `teams` settings with `tenant_id`
matching its issuer, `audience` equal to `<TEAMS_SSO_APP_ID>`, and `resource`
exactly `api://<PORTAL_HOST>/<TEAMS_SSO_APP_ID>`. The manifest's
`webApplicationInfo.id` and `.resource` must match those values.
In the portal's Entra app registration, set the same Application ID URI under
**Expose an API**, expose the delegated `access_as_user` scope, and authorize the
Teams desktop/mobile client `1fec8e78-bce4-4aaf-ab1b-5451cc387264` and web client
`5e3ce6c0-2b1f-4285-8d4b-75ee78787346` for that scope. Configure v2 access tokens
(`api.requestedAccessTokenVersion: 2`) and grant the delegated Graph permissions
used by the portal (`User.Read`, `Calendars.Read`, `Mail.Read`, `Presence.Read`).
Retain the provider's browser OIDC callback and client-secret configuration.
Use production HTTPS, secure cookies, and the portal's existing Teams settings.
The SSO entry enables the tabs; bot invokes still use their separate Bot Framework
channel authentication. After upload, verify the personal tab signs in and a
customer tab can be configured in a team or group chat.

The extension is disabled (HTTP 503) unless all four bot settings are present.
It always requires a signed Bot Framework channel bearer token, including in
debug mode; the Bot Framework Emulator is deliberately not accepted.
Search is case-insensitive, honors Teams' requested page size (up to 25), and
accepts up to 200 characters. Search results are lightweight previews; selecting
a customer fetches its current details. Cards show up to 20 contacts and are size-bounded for
Teams; any omitted contacts are explicitly noted. Companies and contacts use
the existing shared directory, not the agent's company memberships.
Sending a card shares its contents with everyone in that conversation,
including external participants; only share customer data with authorized
recipients. Cards are snapshots and do not update after portal edits.
The manifest also defines the **Log call summary** action command (`logCallSummary`),
available from a message's **More actions** menu, the compose box, or the command box.
A dialog asks the user to pick a customer, a contact, and one of their own calls with
dropdowns, then to edit the selected call's current summary. When invoked from a message,
the message text is added below the existing summary in the editable field. Saving
replaces the call's summary with the edited text (up to 4000 characters). Dropdowns show at
most 100 entries; only companies linked to the agent, their contacts, and the agent's
own calls are offered. Company membership is checked again on each submitted step.
Test the deployed bot in Teams by searching a customer (including one with no
contacts), selecting it, and sending its card.

## Teams API-based contact messaging extension

The separate **Contact Cards** app uses the
[Teams API-based messaging extension framework](https://learn.microsoft.com/en-us/microsoftteams/platform/messaging-extensions/api-based-overview),
not a bot. Search by customer company name, contact name, or email; combine words
to narrow results (for example, a customer name and a contact name). Select a
contact to insert an Adaptive Card into the compose box, then send it to the chat.
Each card contains the contact name, email, customer, and your latest three call
records, newest ID first (the existing CRM call ordering). Call status, appointment
time in UTC when available, rating when available, and summary are included.
Scheduled calls remain call records; this is not a list of only completed calls.
Contacts with fewer than three calls show all available records; contacts without
calls show **No calls recorded**. Summaries over 200 characters are explicitly
shortened to keep cards bounded.

1. Configure an existing `entraid`-prefixed OIDC provider's `teams` settings and
   Entra registration as described under **Combined Teams app manifest and SSO**
   above: v2 access tokens, the `access_as_user` delegated scope, matching audience
   and resource, and both authorized Teams clients. Set `TEAMS_API_PROVIDER_ID`
   to that provider's ID in the portal environment (Compose passes it through).
   This extension does not require any `TEAMS_BOT_*` settings or Graph token exchange.
2. Each user must first sign in through that OIDC provider to link their Entra
   object/tenant identity to an active account, and have a provisioned **Agent**.
   The API validates the delegated bearer token; browser session cookies are not
   accepted as authorization. Missing configuration returns HTTP 503, invalid or
   missing tokens return 401, and unlinked/inactive/unprovisioned users return 403.
3. Customize `teams/api-manifest.json` and `teams/contact-search-openapi.json`.
   Replace `<PORTAL_HOST>` in both files with the public HTTPS hostname and
   `<TEAMS_SSO_APP_ID>` with the configured provider's client ID. Give
   `<TEAMS_API_APP_ID>` a new stable Teams app UUID, different from the existing
   bot/tab app ID. Set real developer, privacy, and terms URLs.
4. Package the customized `api-manifest.json` **renamed to `manifest.json`**,
   `contact-search-openapi.json`, `contact-card-template.json`, `color.png`, and
   `outline.png` together at the ZIP archive root. Upload through Teams **Manage
   your apps → Upload an app** or your organization's catalog. Do not include the
   existing bot manifest: Teams supports only one compose extension per app.
   The original `teams/tabs-bot-extension/manifest.json`, bot endpoint, and tabs remain unchanged.

The minimal OpenAPI 3.0.1 definition describes only
`GET /api/teams/contact-search/?query=...`, with one required search parameter,
bearer authentication, and the JSON fields used by the rendering template.
Teams calls this endpoint directly and renders its `contacts` array using the
packaged preview and Adaptive Card templates; no select-item bot invoke is used.
Queries accept 1–200 characters, cannot be whitespace-only, and return up to 25 contacts in
name/ID order. All search words must match across the customer, contact name,
or email. Refine the query if the desired contact is outside the first 25 results.
The directory is shared, but call history is limited to the requesting agent's
records for each contact. No other agents' calls, appointment notes, inbox data,
credentials, or Graph tokens are returned. Responses are private and not cached.

**Sending a card shares its contact details and call summaries with everyone in
the conversation, including external participants.** Share only with authorized
recipients. Cards are snapshots, not live updates. After deployment, test customer
and contact searches, empty results, contacts with zero/fewer/more than three
calls, and two agents with different call histories; verify that selecting and
sending a result shows only the requesting agent's latest three records.

## User profiles

For Microsoft Teams tabs, automatic SSO, and delegated Microsoft Graph calendar,
inbox, profile, and presence integration, see the
[Teams tab guide](docs/teams-tabs.md) and
[Teams authentication guide](docs/teams-authentication.md). Providers whose configured ID starts with `entraid`
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

- Each user has one Agent, which can be linked to multiple companies. In the CRM
  workspace, agents see and interact only with contacts at their linked companies,
  and only their own calls, appointments, and emails for those contacts.
  The overview and KPIs use the same scope. Removing a company link hides its
  contacts and activity without deleting historical records.
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
restricted to administrators; agents can add companies (automatically linked to
their own Agent) and create contacts only at their linked companies.