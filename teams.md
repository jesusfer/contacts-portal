# Microsoft Teams and Microsoft Graph integration

## What changed

- Teams tabs call Graph using `@microsoft/teams.client` and
  `@microsoft/teams.graph-endpoints`. An `App` starts with explicit
  `User.Read`, `Calendars.Read`, `Mail.Read`, and `Presence.Read` prewarm scopes.
  `@microsoft/teams-js` remains responsible for portal SSO and tab configuration.
- `/teams/tab/` is the content page for a personal static tab and configured
  channel/group-chat tabs. It initializes Teams, requests the host user's SSO
  token automatically, and exchanges it for a portal session before opening the
  dashboard. `/teams/config/` registers the Teams tab configuration/save handler.
- Providers whose configured **name**, not ID, starts with `entraid`
  (case-insensitive) use delegated Graph access, including ordinary browser OIDC
  sessions. Other providers and local sign-ins keep the existing CRM behavior.
- Graph reads the signed-in user's default calendar, Inbox folder, profile
  photo, given name, surname, email, and Teams presence. Calendar and Inbox are
  read-only views; CRM calls, appointment scheduling, KPIs, and the shared
  company/contact directory remain separate local data.
- Graph-backed profile names/photos cannot be edited in the portal. Edit them in
  Microsoft 365. Email uses `mail`, falling back to `userPrincipalName`.
- The sidebar presence badge refreshes automatically every 30 seconds and when
  the page becomes visible. This is polling, not a webhook/push subscription:
  changes normally appear within 30 seconds plus Graph propagation latency.
  The portal never changes presence. Errors display an unknown status.
- New server endpoints live under `/api/teams/` and `/api/graph/`. Browser OIDC
  Graph access and portal SSO still use server-side access/refresh tokens, never
  exposed in JSON responses. Teams tabs additionally acquire delegated Graph
  tokens through the client SDK/MSAL, configured with an in-memory cache, and call
  Graph directly for profile, photo, calendar, Inbox, and presence. The SDK Graph
  identity must match the server-verified portal identity before opening the
  workspace. No application-managed tokens are written to browser storage.

Protect the session database and its backups as credentials: they contain
delegated tokens. Keep Django's server-side session backend; do not switch to
signed-cookie sessions. Use Django's existing `clearsessions` command in your
operational maintenance to remove expired session rows.

## Where Graph calls run

The SDK integration **did not remove the server-side Graph implementation**.
There are two delegated Graph clients: the Teams tab's SDK client and Django's
`GraphClient`. All Graph paths below are relative to
`https://graph.microsoft.com/v1.0`; all requests are read-only `GET` requests.

| Operation | Teams tab | Ordinary browser Entra OIDC session |
| --- | --- | --- |
| Sign-in identity/profile check: `/me` | Django calls Graph during `/api/teams/sso/`, before SDK initialization | Django obtains Graph tokens through OIDC; profile reads run server-side |
| Bootstrap identity: `/me` | Tab requests `/api/graph/profile/`; Django calls Graph before the SDK is active | Not an SDK bootstrap step |
| SDK identity check: `/me?$select=id` | SDK calls Graph directly and compares the ID with the server-returned profile ID | Not used |
| Profile view: `/me` | SDK calls Graph directly after initialization | Django calls Graph through `/api/profile/` or `/api/graph/profile/` |
| Profile photo: `/me/photo/$value` | SDK HTTP client retrieves a blob directly; the UI uses a temporary object URL | Django proxies the photo through `/api/graph/picture/` |
| Calendar: `/me/calendarView` | SDK calls Graph directly, including subsequent pages | Django calls Graph through `/api/graph/calendar/` |
| Inbox: `/me/mailFolders/inbox/messages` | SDK calls Graph directly, including subsequent pages | Django calls Graph through `/api/graph/inbox/` |
| Presence: `/me/presence` | SDK calls Graph directly on the polling schedule | Django calls Graph through `/api/graph/presence/` |
| Session response profile: `/me` | Django still reads Graph when generating `/api/session/` responses, even after SDK initialization | Django reads Graph when generating session responses |
| Server token-refresh identity check: `/me` | Django checks the stored object ID when refreshing its Graph token, if one is recorded | Same server-side behavior |

After initialization, the tab's frontend request helper intercepts Graph/profile
reads and routes them to `app.graph.call(...)`. Photo and pagination requests use
the SDK's authenticated `app.graph.http` client; pagination query parameters are
validated and applied to fixed Graph paths. These UI reads do **not** pass through
Django's Graph endpoints. However, Django session responses still use the
server-side Graph client; SDK initialization does not disable that behavior.
Profile data already returned in the portal session also remains server-derived.

Portal SSO, session management, logout, and local CRM APIs still call Django.
CRM calls, appointments, companies, contacts, and KPIs are database operations,
not Microsoft Graph requests. Local and non-`entraid` sign-ins do not use either
Graph client.

## Authentication: Teams SSO, OBO, and SDK tokens

### Teams portal sign-in: server-side OBO

The current tab startup performs the following sequence:

1. The tab gets public provider configuration from `/api/teams/config/`, initializes
   `@microsoft/teams-js`, and calls `authentication.getAuthToken()`. The returned
   delegated token targets the **portal API**, not Microsoft Graph.
2. The tab sends that token and the selected provider ID to `/api/teams/sso/`.
   This POST requires the portal's CSRF token.
3. Django validates the token's signature, tenant-specific issuer, audience,
   timestamps, tenant, `access_as_user` scope, and authorized Teams client.
   It matches the verified `oid`/`tid` to an explicitly linked, active OIDC
   account; it does not trust an email address or Teams context ID for linking.
4. Django performs **on-behalf-of (OBO)** at
   `https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token`, using the
   configured client ID and server-only client secret, the portal token as the
   `assertion`, `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`,
   `requested_token_use=on_behalf_of`, and scope
   `https://graph.microsoft.com/.default offline_access`.
5. Entra returns a delegated **Graph** access token and, when available, a
   refresh token. Django calls Graph `/me` with that access token and requires
   its ID to match the verified Teams token's `oid`.
6. Only after those checks does Django sign in the linked portal user and store
   the Graph credentials, expiry, tenant, and object ID in its server-side
   session. The response contains portal session/profile data, not Graph tokens
   or the client secret. The browser uses the Django session cookie for CRM APIs.

### Teams Graph views: independent client-side SDK authentication

After portal SSO succeeds, the tab fetches `/api/graph/profile/` through Django
to obtain the expected Graph identity. It then starts a tenant-bound
`@microsoft/teams.client` `App` with the four explicit delegated read scopes.
The SDK/MSAL uses nested app authentication on supported hosts; if silent consent
is unavailable, the tab requests an interactive token with an MSAL popup and
sets the returned active account.

The SDK calls Graph `/me?$select=id` directly and verifies that it matches the
server-returned identity before exposing the workspace. Subsequent tab Graph
views use tokens acquired and managed by SDK/MSAL, with an in-memory cache.
They do **not** reuse Django's OBO Graph token, send the portal SSO token to Graph,
or perform OBO in browser code. No client secret is available to the SDK.

Thus the current Teams implementation has **two separate token acquisition
paths for the same user**: server-side OBO for portal bootstrap/session Graph
work, and SDK/MSAL for direct tab Graph reads. A successful portal SSO does not
guarantee SDK consent, and vice versa. OBO or SDK initialization/identity failure
prevents the tab from opening the workspace; SDK Graph read failures do not
silently fall back to Django Graph reads once the SDK is active.

### Ordinary browser OIDC and token renewal

Browser Entra sign-in uses django-allauth's OIDC authorization-code flow, not
the Teams assertion/OBO flow. Django captures the resulting delegated Graph
access/refresh tokens in the server-side session. Without an active Teams SDK
client, frontend Graph reads call Django, which attaches the server-held token.

For either kind of server Graph session, Django reuses an access token while
more than 60 seconds of validity remain. Otherwise, it uses the stored refresh
token at the tenant token endpoint with `grant_type=refresh_token`; this is
**token renewal, not another OBO exchange**. If a stored object ID exists, Django
checks `/me` again after renewal. Missing refresh tokens, revoked authorization,
or identity mismatch require signing in again. SDK/MSAL manages its own token
acquisition separately from this server renewal path.

The retained server Graph code is therefore still used and cannot simply be
deleted: ordinary browser Graph views, Teams SSO/bootstrap, and session/profile
responses depend on it. Removing Teams-specific OBO would require changing that
authentication/bootstrap design while retaining an appropriate Graph path for
browser OIDC sessions.

Implementation references: `frontend/src/app.jsx`, `frontend/src/teams.js`,
`frontend/src/teams-graph.js`, `crm/views.py`, `crm/teams.py`, `crm/graph.py`,
and `crm/signals.py`.

## Entra application registration

Use a confidential web application. Public-cloud Microsoft Graph supports both
UUID tenant-specific v2 issuers and the multitenant `organizations` issuer.
`common`, consumer accounts, and sovereign clouds are not supported.

1. Register the portal's web redirect URI:
   `https://portal.example.com/accounts/oidc/work/login/callback/`
   (replace `work` with the configured provider ID).
   For multitenant sign-in, set **Supported account types** to accounts in any
   organizational directory and use
   `https://login.microsoftonline.com/organizations/v2.0` as the OIDC issuer.
   For a single-tenant app, use its tenant UUID in the issuer URL instead.
2. Create a client secret and keep it only in deployment secrets/environment.
   Use the same client ID for OIDC and the Teams manifest.
3. Under **Expose an API**, set the Application ID URI to
   `api://portal.example.com/<client-id>`. Add the delegated `access_as_user`
   scope. Configure the API to issue **v2 access tokens**
   (`api.requestedAccessTokenVersion: 2`).
4. Preauthorize these Teams clients for `access_as_user`:
   - Desktop/mobile: `1fec8e78-bce4-4aaf-ab1b-5451cc387264`
   - Web: `5e3ce6c0-2b1f-4285-8d4b-75ee78787346`
5. Add Microsoft Graph **delegated** permissions `User.Read`,
   `Calendars.Read`, `Mail.Read`, and `Presence.Read`, and grant tenant admin
   consent. Do not add application permissions or write permissions.
   OIDC also requests `openid`, `profile`, `email`, and `offline_access`.
6. Users need a Microsoft 365 mailbox/calendar and access to Teams presence.
7. Add a **Single-page application** redirect URI for the portal origin
   (`https://portal.example.com`, matching the SDK's redirect URI), as well as
   `brk-multihub://portal.example.com` for nested app authentication in Teams.
   Retain the confidential web application's OIDC callback and secret for Django;
   never expose that secret to the frontend. Enable the SPA authorization-code
   flow with PKCE, not implicit token issuance.

Teams returns a token for the portal API, **not Graph**. The backend validates
its RS256 signature using the tenant's Microsoft signing keys, v2 issuer, tenant,
audience, expiry, delegated scope, and authorized Teams client. With the
`organizations` issuer, the tenant UUID from the signed token selects the issuer,
keys, and on-behalf-of token endpoint; the UUID is validated before use. Only
then does the backend request `https://graph.microsoft.com/.default`. A token
for another app or unauthorized client cannot sign in.

## OIDC configuration

Set `OIDC_PROVIDERS` to a JSON array, stored privately in deployment settings.
The following values are placeholders, not real credentials:

```json
[
  {
    "id": "work",
    "name": "entraid-work",
    "server_url": "https://login.microsoftonline.com/organizations/v2.0",
    "client_id": "11111111-1111-1111-1111-111111111111",
    "client_secret": "<secret-from-private-deployment-settings>",
    "teams": {
      "resource": "api://portal.example.com/11111111-1111-1111-1111-111111111111"
    }
  }
]
```

`teams` is optional: omit it for browser-only Graph integration. Its fields:

| Setting | Meaning |
| --- | --- |
| `tenant_id` | Defaults to the OIDC issuer tenant; use `organizations` for multitenant sign-in |
| `resource` | Required Application ID URI; must end in `/<client_id>` |
| `audience` | Defaults to `client_id`; v2 API tokens must use that exact audience |
| `authorized_client_ids` | Optional nonempty UUID array; defaults to the two Teams clients above |

An optional provider-level `scope` array can add OIDC/Graph scopes, but cannot
remove the required read permissions or `offline_access` from an `entraid`
provider. No tokens or client secrets are exposed by the public Teams
configuration endpoint.

**Identity linking:** sign in once in a normal browser using the same Entra
provider before using Teams SSO. For multitenant sign-in, use the `organizations`
issuer. This creates the OIDC social-account association with the provider's
verified tenant/object identity. Administrators must still provision the user's
Agent and company associations for CRM access. Teams SSO uses that explicit
association; it does not link accounts by email or trust the Teams context's user
ID. Existing associations without tenant/object claims need a fresh browser OIDC
sign-in. Linking a different provider does not enable Graph for an existing local
session; sign in through the Entra provider.

## Create the Teams app package

Use the latest **Microsoft 365 Agents Toolkit** in VS Code (or Teams Developer
Portal) to scaffold a Teams tab app/package and its required color/outline PNG
icons. The existing Django/Preact portal is the tab implementation; do not deploy
the scaffold's separate sample tab server. Keep the generated supported manifest
schema/version and developer, description, icon, privacy, and terms metadata.

Set these fields in the generated manifest, substituting your real HTTPS domain
and Entra client ID:

```json
{
  "staticTabs": [
    {
      "entityId": "contacts-portal",
      "name": "Contacts",
      "contentUrl": "https://portal.example.com/teams/tab/?provider=work",
      "websiteUrl": "https://portal.example.com/",
      "scopes": ["personal"]
    }
  ],
  "configurableTabs": [
    {
      "configurationUrl": "https://portal.example.com/teams/config/?provider=work",
      "canUpdateConfiguration": true,
      "scopes": ["team", "groupchat"]
    }
  ],
  "validDomains": ["portal.example.com"],
  "webApplicationInfo": {
    "id": "11111111-1111-1111-1111-111111111111",
    "resource": "api://portal.example.com/11111111-1111-1111-1111-111111111111"
  }
}
```

These are **manifest fields**, not a complete standalone manifest.
`webApplicationInfo.id` is the Entra registration ID, not necessarily the Teams
package's own `id`. The resource must exactly match **Expose an API** and the
portal provider's `teams.resource`.

Validate and zip the manifest/icons using Toolkit/Developer Portal, enable
custom app upload in your Teams tenant, and upload the package. Add the personal
app to test the static tab; use **Add a tab** in a channel/chat to test the
configuration page and save its tab. Tabs still display the current user's
workspace, not a shared channel mailbox or another user's calendar.

## HTTPS, framing, and cookies

Deploy behind HTTPS with `DJANGO_DEBUG=0`, `DJANGO_SECURE_COOKIES=1`, the correct
`DJANGO_ALLOWED_HOSTS`, and the portal's HTTPS origin in
`DJANGO_CSRF_TRUSTED_ORIGINS`. Only enable `DJANGO_TRUST_PROXY=1` behind a proxy
that overwrites forwarded headers. Compose's localhost defaults are insufficient
for Teams; rebuild the image after changing frontend dependencies/code.

With Teams enabled, secure session/CSRF cookies use `SameSite=None`, necessary
for an embedded tab. CSRF protection remains enabled for SSO and all mutations.
Teams tab pages (including the dashboard rendered at `/teams/tab/#/dashboard`)
allow framing only by the
portal itself and Microsoft's Teams/Microsoft 365 hosts through CSP
`frame-ancestors`. Ordinary portal, admin, and account pages retain clickjacking protection.
Configure any reverse proxy to preserve these headers and not add conflicting
`X-Frame-Options: DENY` to tab content.

Some browsers block third-party cookies even with `SameSite=None`. Allow the
portal's embedded cookies in the Teams client/browser, or open the portal in a
normal browser. An HTTPS tunnel can be used for development, but its domain must
match the API resource, app package, and deployment settings.

## Errors and verification

SSO is attempted automatically on the tab content page; configuration pages
stay open for Teams' Save flow. Outside Teams or when SSO/consent fails, the page
provides an explicit error/retry rather than silently signing in as a different
existing browser user. Use browser OIDC sign-in to establish the account or
renew consent, then return to Teams.

The client SDK uses nested app authentication on supported Teams hosts. If silent
Graph consent is unavailable, an interactive MSAL popup establishes the account;
allow sign-in popups and retry if the browser blocks one. Consent or identity
failure does not silently fall back to another user's Graph session.

Graph failures are surfaced rather than silently substituting local inbox or
calendar data. Read-only views follow Graph pagination. Email bodies are plain
text, never rendered as HTML. Pagination destinations are restricted to Graph,
and backend HTTP calls use timeouts without forwarding credentials on redirects.
No Graph data is imported into the CRM database.

Verify with a real tenant:

1. Browser Entra sign-in: confirm Graph name/email/photo, default calendar and
   Inbox; check local profile edits and inbox flags are unavailable.
2. Personal Teams tab: confirm automatic SSO and the correct user's dashboard.
3. Channel/chat tab: save configuration and reopen the saved tab.
4. Change availability in Teams; confirm the sidebar badge updates without
   clicking anything, including after returning to the tab.
5. Confirm local and non-`entraid` OIDC accounts retain local behavior.
6. Test revoked consent, expired tokens, missing photo/mailbox, multiple
   providers, multitenant users from distinct directories, and blocked
   third-party cookies. An unprovisioned user must not gain CRM directory or
   activity access.

Automated validation uses the existing Django test/check commands and frontend
build described in README.md. Graph/SSO tests mock Microsoft endpoints; real
Teams client/tenant testing is required before production rollout.

References:
- [Teams tab SSO](https://learn.microsoft.com/en-us/microsoftteams/platform/tabs/how-to/authentication/tab-sso-overview)
- [Teams SSO and Graph OBO](https://learn.microsoft.com/en-us/microsoftteams/platform/tabs/how-to/authentication/tab-sso-graph-api)
- [Microsoft 365 Agents Toolkit](https://learn.microsoft.com/en-us/microsoftteams/platform/toolkit/agents-toolkit-fundamentals)
- [Teams JavaScript SDK](https://learn.microsoft.com/en-us/microsoftteams/platform/tabs/how-to/using-teams-client-library)
- [Entra token claims](https://learn.microsoft.com/en-us/entra/identity-platform/access-token-claims-reference):
  v2 access-token `aud` is the API's client ID, not its Application ID URI.
