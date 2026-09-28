# Security policy

## Supported versions

MediaFleet is currently pre-release. Security fixes are applied to the latest
commit on the default branch.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or exposed secret.
Use the repository's private GitHub security advisory workflow. Include the
affected component, reproduction steps, impact, and any suggested mitigation.

## Secret handling

- Never commit populated `.env` files or credential-bearing URLs.
- Rotate a credential immediately if it may have entered Git history.
- Use deployment-platform secrets or a dedicated secret manager in production.
- Sensitive configuration values must not be returned by APIs after creation.
- Logs and errors must redact passwords, tokens, signatures, and URL user info.

See [configuration and secrets](docs/security/configuration-and-secrets.md).
