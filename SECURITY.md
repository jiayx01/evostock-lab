# Security Policy

## Private disclosure

Please report security issues through this repository's private GitHub Security Advisory feature. Do not open a public issue containing credentials, mailbox identifiers, broker records, holdings, or screenshots.

## Data boundary

EvoStock Lab expects all personal runtime state under `data/` or an external directory selected by `EVOSTOCK_DATA_DIR`. That directory is ignored by Git, but users remain responsible for filesystem permissions, backups, OAuth storage, and any external connector they configure.

The project does not need API keys in source files. Never commit OAuth tokens, cookies, private keys, real mailbox exports, broker account identifiers, or portfolio reports.
