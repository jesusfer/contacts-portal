# Teams sign-in and Microsoft Graph

Teams SSO verifies a signed token for the portal API. The token is not a Microsoft
Graph token. Django checks its signature, issuer, audience, tenant, time claims,
delegated `access_as_user` scope, and authorized Teams client.

The identity endpoint at `/api/teams/identity/` returns the verified Entra object
ID and tenant ID. It does not create a Django session or call Graph.

When a user opens the full dashboard, `/api/teams/sso/` matches the verified
object and tenant IDs to one active account linked through OIDC. Django exchanges
the Teams token for delegated Graph credentials with MSAL and the server-side
client secret. It checks the Graph object ID, then creates the Django session.
CRM access also requires a provisioned Agent.

MSAL acquires and renews Graph credentials. Django stores the access token and
serialized MSAL token cache in the server-side session. The cache is bound to the
user, OIDC provider, tenant, and client ID. Existing OIDC refresh credentials are
imported into MSAL's cache. Django reuses an access token while it has more than
60 seconds before expiry; otherwise, MSAL renews it. If a Teams object ID is
stored, Django checks it again after renewal. Django does not return tokens or the
client secret to the browser. Graph credentials require server-side Django
sessions.

Teams tabs acquire their own Graph tokens through the Teams SDK. These tokens use
an in-memory cache and are separate from the server-side token cache.

## Graph requests

Django uses the Microsoft Graph SDK with delegated credentials. Its transport
allows only read-only `GET` requests to `/me`, the profile photo, the default
calendar, Inbox messages, and presence. It uses the portal's logged HTTP
transport, a 10-second timeout, and does not follow redirects. The portal checks
Graph response data and validates signed pagination cursors.

The Bot Framework endpoint uses Bot Framework channel authentication. Teams API
and card models handle messaging-extension results and cards; they do not replace
the bot's channel authentication or CRM access checks.

## Entra app registration

Use a confidential web app in the Microsoft public cloud. The supported issuer
is either a tenant UUID or `organizations` on
`https://login.microsoftonline.com`. `common`, consumer accounts, and sovereign
clouds are not supported.

1. Register the browser OIDC callback:
   `https://portal.example.com/accounts/oidc/work/login/callback/`.
   Replace `work` with the provider ID. Use a tenant UUID issuer or the
   `organizations` issuer for users from multiple Entra tenants.
2. Create a client secret. Keep it in private deployment settings.
3. Under **Expose an API**, set an Application ID URI and expose the delegated
   `access_as_user` scope. Configure v2 access tokens.
4. Authorize these Teams clients for the scope:
   - Desktop and mobile: `1fec8e78-bce4-4aaf-ab1b-5451cc387264`
   - Web: `5e3ce6c0-2b1f-4285-8d4b-75ee78787346`
5. Add Microsoft Graph delegated permissions `User.Read`, `Calendars.Read`,
   `Mail.Read`, and `Presence.Read`. Grant tenant admin consent.
6. Add a single-page application redirect URI for the portal origin and
   `brk-multihub://portal.example.com`. Use authorization code with PKCE.
   Keep the web callback and secret for Django. Do not expose the secret to the
   frontend.

Users need a Microsoft 365 mailbox and calendar, and access to Teams presence.

## OIDC settings

Set `OIDC_PROVIDERS` to a JSON array in private deployment settings. A provider
used for Teams must have an ID that starts with `entraid` and a Microsoft issuer.
Add its `teams` object with the API resource:

```json
{
  "id": "entraid-work",
  "name": "Work identity",
  "server_url": "https://login.microsoftonline.com/organizations/v2.0",
  "client_id": "<entra-client-id>",
  "client_secret": "<private-client-secret>",
  "teams": {
    "resource": "api://portal.example.com/<entra-client-id>"
  }
}
```

`teams.resource` must match the Application ID URI. `tenant_id` defaults to the
issuer tenant. Set it to `organizations` for a multitenant issuer. `audience`
defaults to `client_id`. `authorized_client_ids` defaults to the Teams web and
desktop/mobile client IDs listed above.

The Application ID URI has the form
`api://portal.example.com/<entra-client-id>` or
`api://portal.example.com/botid-<entra-client-id>`. Use the exact URI in the
Teams app manifest and OIDC provider settings.

For the full dashboard, each user must first sign in through this OIDC provider.
This links the account to the verified Entra tenant and object ID. Email address
does not link accounts. An administrator must provision an Agent for CRM access.
The Graph-only personal tab does not require a linked CRM account or Agent.
