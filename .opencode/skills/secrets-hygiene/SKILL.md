---
name: secrets-hygiene
description: Prevents hardcoded credentials and secret leaks. Use when committing or writing code, handling API keys, tokens, passwords, private keys, or connection strings, editing .env / config / yaml / docker files, or when the user mentions credentials, secrets, or API keys.
---

# Secrets Hygiene

Stop secrets from entering version control. A secret committed to git persists
forever in history and is scraped automatically within minutes of a public
push. Treat any secret that touched a commit as compromised.

## Core rules
1. **Never hardcode secrets in source.** No API keys, tokens, passwords,
   private keys, or connection strings as literals in source files or in
   committed config. (CWE-798)
2. **Inject at runtime** from environment variables (dev) or a secrets manager
   / vault (prod): AWS Secrets Manager, GCP Secret Manager, Azure Key Vault,
   HashiCorp Vault, Doppler, cloud KMS. In CI/CD, fetch secrets via short-lived
   tokens or workload identity — not long-lived creds baked into pipeline
   config.
3. **`.env` files are local-only.** They MUST be in `.gitignore` and never
   committed. Ship a `.env.example` with placeholder values instead.
4. **Rotate immediately on leak.** A committed secret = a compromised secret.
   Revoke/rotate it at the issuer, update all consumers, then scrub history.
5. **Don't log secrets.** Redact before logging; never echo request bodies,
   headers, or env blocks that contain credentials. (CWE-532)

## `.gitignore` — verify these are present
```
.env
.env.*
!.env.example
*.pem
*.key
secrets.*
credentials.*
```

## Detecting secrets (known formats + high entropy)
Watch for:
- Cloud/provider keys: `AKIA...`, `aws_secret_access_key`, `ghp_...`/`gho_...`,
  `xox[baprs]-...` (Slack), `AIza...` (Google), `sk-...` (OpenAI/Stripe),
  `eyJ...` (JWTs).
- Private keys: `-----BEGIN ... PRIVATE KEY-----`.
- Connection strings: `protocol://user:password@host`.
- Long high-entropy base64/hex strings assigned to names like `key`, `token`,
  `secret`, `password`, `apiKey`, `passwd`, `auth`.
- Automate detection with `gitleaks`, `trufflehog`, `gitguardian`, or
  `detect-secrets` as a pre-commit hook.

## Examples

### BAD — hardcoded (now permanently in git history)
```python
API_KEY = "sk-REDACTED-EXAMPLE"                    # credential in source
db = connect("postgres://admin:hunter2@db.prod/internal")
```
```js
const STRIPE_SECRET = "sk_live_REDACTED_EXAMPLE"; // committed
```

### GOOD — runtime injection, one source of truth
```python
import os
api_key = os.environ["API_KEY"]      # fails loud if missing — good
db_url  = os.environ["DATABASE_URL"]
```
```js
const stripeKey = process.env.STRIPE_SECRET_KEY;
```

### GOOD — keep `.env` out of git, ship `.env.example`
```bash
# .env.example  (committed — placeholders only)
API_KEY=your_api_key_here
DATABASE_URL=postgres://user:pass@host:5432/db
```

## Incident checklist (a secret was just committed)
1. Rotate/revoke the secret at the issuer NOW — before touching history.
2. Update all consumers (apps, CI, on-call laptops) to the new value.
3. Review logs for misuse: unexpected IPs, resource creation, data exports.
4. Scrub history with `git filter-repo` (preferred) or BFG Repo-Cleaner;
   force-push and have collaborators re-clone (old refs re-leak otherwise).
5. Add a pre-commit secret scanner (`gitleaks`/`trufflehog`) to prevent
   recurrence.

## Quick checklist
- [ ] No secret literals in source or committed config
- [ ] Secrets read from env (dev) / vault (prod)
- [ ] `.env` in `.gitignore`; `.env.example` committed
- [ ] Pre-commit secret scanner installed
- [ ] No secrets in logs, error messages, or comments
- [ ] Rotation procedure documented for every secret in use
