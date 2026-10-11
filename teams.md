# Microsoft Teams and Microsoft Graph integration

## What changed

- The frontend uses the current Microsoft Teams JavaScript SDK
  (`@microsoft/teams-js` 2.58.0), not the deprecated TeamsFx SDK.
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
- New server endpoints live under `/api/teams/` and `/api/graph/`. Access/refresh
  tokens stay in server-side Django sessions, never browser storage or JSON
  responses. Expired delegated tokens are refreshed server-side; if consent or
  reauthentication is required, sign in again.

Protect the session database and its backups as credentials: they contain
delegated tokens. Keep Django's server-side session backend; do not switch to
signed-cookie sessions. Use Django's existing `clearsessions` command in your
operational maintenance to remove expired session rows.

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
