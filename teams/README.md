The manifests must be separated because each manifest can have only one messaging extension.

## Package the manifests

Requires Python 3, with no additional dependencies.

1. Copy `teams.env.example` to `teams.env` in this folder.
2. Fill in all six values. Use plain `KEY=value` entries (optionally enclosing
   values in matching single or double quotes). Blank lines and full-line `#`
   comments are supported; values are literal, not shell expressions.
3. Run `python /absolute/path/to/contacts-portal/teams/package_manifests.py`.
   The script finds `teams.env` relative to itself, regardless of your working
   directory.

The script replaces `<PLACEHOLDER>` values in every packaged JSON file, including
the OpenAPI specification, without modifying the source templates. Missing or
empty required values stop packaging before any archives are written.

The output is `dist/tabs-bot-extension.zip` and `dist/api-extension.zip` inside
this folder. Each archive contains `manifest.json` at its root (renaming
`api-manifest.json` for the API package), plus the package's JSON resources and
PNG icons. Adaptive Card `${...}` bindings are preserved. Running the script
again overwrites the archives. Upload the appropriate ZIP to Teams.

Both `teams.env` and `dist/` are ignored by Git.