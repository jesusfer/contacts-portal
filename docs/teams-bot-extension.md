# Customer search and call-summary extension

The Bot Framework app provides two Teams compose commands:

- **Search customers** searches customer company names. A result preview shows
  the customer name, ID, contact count, and contacts. Selecting the preview
  shows a customer card with a bounded contact list.
- **Log call summary** opens a dialog to choose a customer, one of its contacts,
  and one of the current Agent's calls with that contact. The user can edit and
  save the call summary. Text from the selected Teams message is added to the
  editable summary when available.

The search uses the shared company and contact directory. The call-summary
action only offers the Agent's linked customers, their contacts, and that
Agent's calls. The server checks these relationships again when the user submits
each step.

The endpoint is `POST /api/teams/messages/`. It accepts Bot Framework channel
requests only; browser session cookies do not authorize extension actions. Each
invoke needs a valid channel bearer token and a linked, active Entra account in
the configured tenant. The account must have a provisioned CRM Agent.

Ordinary bot messages receive the reply “This bot is not enabled for
conversation.” The bot does not process their content. The message still needs
valid Bot Framework channel authentication.

## Configure the bot

1. Register a single-tenant Azure Bot, enable the Microsoft Teams channel, and
   set its HTTPS messaging endpoint to
   `https://portal.example.com/api/teams/messages/`.
2. Set these private environment variables:
   - `TEAMS_BOT_APP_ID`: the bot application ID.
   - `TEAMS_BOT_APP_PASSWORD`: the bot client-secret value.
   - `TEAMS_BOT_TENANT_ID`: the allowed Entra tenant UUID.
   - `TEAMS_BOT_PROVIDER_ID`: an `entraid` OIDC provider ID for that tenant.
3. Each user must sign in through that OIDC provider and have a provisioned
   Agent.
4. Configure and package the tab-and-bot manifest. See
   [App setup and deployment](teams-deployment.md) and the
   [manifest packaging guide](../teams/README.md).

The extension returns HTTP 503 when any bot setting is missing. It does not use
browser authentication or Graph permissions to authorize its actions.

Cards inserted into a Teams conversation are visible to its participants.
Share customer information only with authorized recipients.
