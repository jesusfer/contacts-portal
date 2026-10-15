# Teams tabs

Contacts Portal provides a personal tab and a configurable customer tab.

## Personal tab

The personal tab opens at `/teams/tabs/personal/`. By default, it opens a Microsoft 365
workspace without signing in to the CRM. Teams SSO verifies the user's Entra
object ID and tenant ID. The Teams SDK then reads the user's profile, photo,
calendar, Inbox, and presence from Microsoft Graph. It requests delegated
`User.Read`, `Calendars.Read`, `Mail.Read`, and `Presence.Read` permissions.

The SDK checks that the Graph user and tenant match the verified Teams identity.
Graph tokens stay in the SDK's in-memory cache. Calendar and Inbox views are
read-only. Presence refreshes every 30 seconds and when the tab becomes visible.

**Open full dashboard** starts the CRM sign-in flow. The user must have a linked
active OIDC account and a provisioned CRM Agent for CRM data. The dashboard uses
the Django session for CRM requests.

Set `TEAMS_GRAPH_ONLY=0` to start in the full dashboard instead. The default is
`1`. This setting chooses how the personal tab starts; the full dashboard action
is available in either mode.

## Configurable customer tab

The configuration page opens at `/teams/tabs/shared/config/`. The user signs in with Teams
SSO, chooses one of the CRM customers linked to their Agent, then saves the tab.
Teams stores the selected customer in the content URL at
`/teams/tabs/shared/customer/`.

Each viewer signs in and sees contacts for the selected customer and their own
calls. The tab does not show another Agent's calls. The customer list and customer
details require an active Teams CRM session for the same OIDC provider.

## Microsoft Graph requests

Teams tabs use the SDK to read Microsoft Graph directly. The full dashboard also
uses the server-side Graph client when Django builds its session and profile
responses. Ordinary browser sign-in through an Entra OIDC provider uses the
server-side Graph client for profile, photo, calendar, Inbox, and presence data.

Graph-backed profile names and photos are read-only in the portal. The portal
does not import Graph data into the CRM database.
