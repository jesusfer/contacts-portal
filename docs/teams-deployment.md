# Teams app setup and deployment

Contacts Portal ships two separate Teams app packages under `teams/`:

- `tabs-bot-extension/manifest.json` defines the personal tab, configurable
  customer tabs, customer-search command, and call-summary action.
- `api-extension/api-manifest.json` defines the separate Contact Cards API
  extension.

Each Teams manifest can define only one messaging extension, so the Bot
Framework extension and API-based Contact Cards extension are separate Teams
apps. The portal itself hosts the tabs and backend endpoints; do not deploy a
separate scaffold sample server.

## Configure and package the apps

Use the latest Microsoft 365 Agents Toolkit in VS Code or Teams Developer Portal
to validate the packages. Keep each manifest's schema/version, developer
metadata, icon filenames, privacy/terms URLs, commands, and capabilities
consistent with the checked-in manifest. Replace placeholders with:

- The portal's public HTTPS hostname.
- A stable Teams package ID for each app.
- The Entra SSO registration ID and Application ID URI.
- The configured OIDC provider ID for Teams SSO.
- The Bot Framework app ID for the tabs-and-bot app.

The `webApplicationInfo.id` is the Entra SSO registration ID, not necessarily
the Teams package's own `id`. Its `resource` must exactly match **Expose an
API** and the selected provider's `teams.resource`. The checked-in manifests
use `api://<portal-host>/botid-<entra-client-id>`; backend configuration also
accepts `api://<portal-host>/<entra-client-id>`.

The tab-and-bot app uses `TEAMS_BOT_APP_ID` for the bot and the OIDC client ID
for Teams SSO. The Contact Cards app uses the OIDC client ID for SSO and its
own stable Teams app ID. Set `TEAMS_BOT_PROVIDER_ID` and
`TEAMS_API_PROVIDER_ID` to the configured OIDC provider IDs used by each
extension.

Configure the six values documented in `teams/teams.env.example`. The
packaging script reads `teams/teams.env` and creates
`teams/dist/tabs-bot-extension.zip` and `teams/dist/api-extension.zip`:

```powershell
python teams\package_manifests.py
```

Each archive contains a root-level manifest, its JSON resources, and icons.
See the [manifest packaging guide](../teams/README.md) for environment values
and packaging details. Validate the packages, enable custom app upload in the
Teams tenant, and upload them. Add the personal app to test its static tab; use
**Add a tab** in a channel or chat to test the configurable customer tab.

The static tab shows the signed-in user's Microsoft 365 workspace. The
configurable tab shows the selected customer's CRM contacts and the current
Agent's calls, not a shared channel mailbox or another Agent's calls.

## Entra registration and provider setup

Follow [Teams sign-in and Microsoft Graph](teams-authentication.md) to register
the confidential web app, delegated Graph scopes, authorized Teams clients,
`access_as_user` API scope, and `OIDC_PROVIDERS` entry. Keep the client secret
in private deployment settings. Both Teams packages use the portal's public
HTTPS hostname; the Entra Application ID URI, manifest resource, OIDC provider,
and deployed portal must agree.

## Production settings

Deploy over HTTPS with `DJANGO_DEBUG=0`, `DJANGO_SECURE_COOKIES=1`, correct
`DJANGO_ALLOWED_HOSTS`, and the portal HTTPS origin in
`DJANGO_CSRF_TRUSTED_ORIGINS`. Set `DJANGO_TRUST_PROXY=1` only behind a trusted
proxy that overwrites forwarded headers. Compose's localhost defaults are not
suitable for Teams. Rebuild the image after changing frontend dependencies or
code.

When Teams is enabled, session and CSRF cookies use `SameSite=None` and secure
cookies so they can work in an embedded tab. CSRF protection remains enabled.
Teams tab pages, including the dashboard at `/teams/tab/#/dashboard`, may be
framed only by the portal itself and Microsoft Teams/Microsoft 365 hosts through
CSP `frame-ancestors`. Ordinary portal, admin, and account pages retain
clickjacking protection. Preserve the portal response security headers in any
reverse proxy; do not add a conflicting `X-Frame-Options: DENY` to tab content.

Some browsers block third-party cookies even with `SameSite=None`. Allow the
portal's embedded cookies in the Teams client/browser or open the portal in a
normal browser. An HTTPS tunnel can be used in development, but its hostname
must match the API resource, app package, and deployment settings.

## Verify a deployment

SSO is attempted automatically on the personal tab. The configuration page
stays open for Teams' Save flow. Outside Teams, or when SSO or consent fails,
the page shows an explicit error and retry instead of silently switching to a
different browser user's session. Users can establish the account or renew
consent through browser OIDC, then return to Teams.

The SDK uses nested app authentication on supported Teams hosts. If silent
Graph consent is unavailable, an interactive MSAL popup establishes the
account; allow sign-in popups and retry if the browser blocks one. Graph
failures are surfaced, not replaced with local Inbox or calendar data.
Pagination is read-only and restricted to Graph. Email bodies are plain text,
not rendered as HTML. Backend HTTP calls use timeouts without forwarding
credentials on redirects. Graph data is not imported into the CRM database.

Use a real tenant to check:

1. Browser Entra sign-in: confirm Graph name, email, photo, default calendar,
   and Inbox; confirm profile edits and Inbox flags are unavailable.
2. Personal tab with the default `TEAMS_GRAPH_ONLY=1`: confirm automatic Teams
   identity validation and the correct user's Graph-only views, with no
   `/api/session/`, `/api/graph/`, CRM API, or `/api/teams/sso/` requests before
   opening the full dashboard. Test a user without a linked CRM account. Then
   choose **Open full dashboard** and confirm OBO happens only then, including
   failure for an unlinked account and access-pending UI for a missing Agent.
   With `TEAMS_GRAPH_ONLY=0`, confirm automatic OBO and the embedded dashboard.
3. Channel/chat customer tab: save the configuration, reopen the saved tab,
   and verify the selected customer and viewer-specific calls.
4. Change Teams availability and confirm the presence badge updates without
   clicking, including after returning to the tab.
5. Confirm local and non-`entraid` OIDC accounts retain their existing behavior.
6. Test revoked consent, expired tokens, missing photo/mailbox, multiple
   providers, multitenant users from distinct directories, and blocked
   third-party cookies. An unprovisioned user must not gain CRM directory or
   activity access.
7. Test both messaging extensions with different Agents. Confirm bot customer
   search and call-summary choices respect Agent membership, and Contact Cards
   expose only the requesting Agent's calls. See the
   [bot extension](teams-bot-extension.md) and
   [Contact Cards](teams-contact-cards.md) guides.

Automated validation uses the existing Django test/check commands and frontend
build described in `README.md`. Graph and SSO tests mock Microsoft endpoints;
real Teams client and tenant testing is still required before production.
