# Teams app setup and deployment

The `teams/` directory contains two app manifests:

- `tabs-bot-extension/manifest.json` defines the personal tab, configurable
  customer tabs, customer search, and call-summary action.
- `api-extension/api-manifest.json` defines the separate Contact Cards API
  extension.

Each manifest can define only one messaging extension, so the two extensions
use separate Teams apps. Both packages use the portal's public HTTPS hostname.
The `webApplicationInfo` resource and ID must match the Entra app registration
and the selected OIDC provider.

The tab-and-bot app uses `TEAMS_BOT_APP_ID` for the bot and the OIDC client ID
for Teams SSO. The Contact Cards app uses the same OIDC client ID for SSO and a
separate stable Teams app ID.

Use `python /absolute/path/to/contacts-portal/teams/package_manifests.py` to
package the manifests. Configure the six values in `teams/teams.env.example`.
The script writes `tabs-bot-extension.zip` and `api-extension.zip` under
`teams/dist/`. See the [packaging guide](../teams/README.md) for details.

## Production settings

Deploy the portal over HTTPS. Set `DJANGO_DEBUG=0`,
`DJANGO_SECURE_COOKIES=1`, `DJANGO_ALLOWED_HOSTS`, and
`DJANGO_CSRF_TRUSTED_ORIGINS`. Set `DJANGO_TRUST_PROXY=1` only behind a trusted
proxy that overwrites forwarded headers.

When Teams is enabled, session and CSRF cookies use `SameSite=None` and secure
cookies. CSRF protection remains enabled. Teams tab pages can be framed by the
portal and Microsoft Teams or Microsoft 365 hosts. Keep the portal's response
security headers when configuring a reverse proxy.

Some browsers block third-party cookies in embedded tabs. Allow the portal's
cookies in the Teams client or browser. The hostname in the Entra registration,
Teams manifest, and portal settings must match.

## Check the installation

1. Open the personal tab in Teams and confirm that SSO verifies the current user.
2. Check the profile, calendar, Inbox, and presence views.
3. Open **Open full dashboard** and check that a linked, provisioned user can
   access CRM data.
4. Configure a customer tab in a team or group chat. Confirm it shows the
   selected customer's contacts and the current Agent's calls.
5. Test each messaging extension with an account that has an Agent. Confirm
   unlinked or unprovisioned users cannot access CRM data.
