---
name: secure-coding
description: Defensive coding rules mapped to OWASP and CWE for high-risk code. Use when writing input handling and validation, file IO, network or HTTP calls, SQL queries, authentication or authorization logic, crypto, or deserialization, and when hardening code against injection, XSS, path traversal, SSRF, or weak crypto.
---

# Secure Coding

Defensive-coding quick reference. Treat all external input as hostile, fail
closed (deny by default), and pick the safe primitive over the clever one.
Rules are grouped by topic and mapped to OWASP Top 10 (2021) and CWE.

## Input validation & output encoding — A03, CWE-20/79/89
- Validate on a trusted boundary with an **allowlist** (expected format,
  length, range, charset). Reject-by-default, never filter-by-blacklist.
- **SQL:** always parameterized queries / prepared statements / the ORM query
  builder. Never `f"SELECT ... {var}"` or string concatenation. (CWE-89)
- **OS commands:** avoid `shell=True`, `exec`, `eval`, backticks. Pass an arg
  list (`subprocess.run([...], shell=False)`) and use built-in APIs. If
  unavoidable, validate against a strict allowlist. (CWE-78)
- **HTML output:** rely on the framework's contextual auto-escaping; encode on
  output, not input; set `Content-Type` correctly. (CWE-79)
- **Numbers:** parse to a typed int/float and range-check (avoids integer
  overflow / format-string issues). (CWE-190)

## Authentication & sessions — A07, CWE-287/384
- Hash passwords with a slow KDF: Argon2id (preferred), bcrypt, or scrypt with
  a per-user salt. Never MD5/SHA1/plain/reversible. (CWE-916/327)
- Enforce length + breach-list checks; rate-limit and lock out login.
- Regenerate the session ID after privilege change (login) to stop fixation.
  (CWE-384)
- Cookies: `Secure`, `HttpOnly`, `SameSite=Lax|Strict`; absolute + idle
  timeouts. Never put session tokens in URLs.
- JWT: pin the expected algorithm(s) server-side, reject `none`, verify
  `exp`/`iss`/`aud`, use strong keys. (CWE-347)

## Authorization — A01, CWE-862/863/639
- Enforce authz **server-side on every request**; never trust client-side
  flags or hidden fields. (CWE-602)
- Object-level (ownership) checks stop IDOR — derive the resource owner from
  the session, not from a request parameter. (CWE-639)
- Apply CSRF protection (synchronizer token / SameSite) to every
  state-changing request. (CWE-352)
- Least privilege for service accounts and tokens; scope them narrowly.

## Cryptography — A02, CWE-327/328
- Use vetted libraries (`cryptography`, libsodium, TLS). Never roll your own.
- Symmetric: AES-GCM or ChaCha20-Poly1305 (authenticated). No ECB, no reused
  nonce/IV, no constant IV. (CWE-329/1209)
- Randomness: use a CSPRNG (`secrets`, `os.urandom`, `crypto.randomBytes`) for
  tokens/keys — never `random`/`Math.random`. (CWE-338)
- TLS: verify certificates; do not set `verify=False` /
  `rejectUnauthorized: false` / `InsecureSkipVerify: true`.

## File IO & path traversal — A01/A03, CWE-22/434
- Resolve and canonicalize paths, then confirm they stay inside the allowed
  root (`Path.resolve()`; reject `..`, absolute paths, and symlinks that
  escape the root). (CWE-22)
- Validate uploaded file content/type and size; store outside the webroot with
  non-executable names/permissions; never serve uploads as HTML. (CWE-434)
- Never derive output paths from unsanitized user input.

## Network & SSRF — A10, CWE-918
- For server-side fetches of user-controlled URLs: use an allowlist of
  permitted hosts/schemes; block internal IPs (127.0.0.0/8, 10/8, 172.16/12,
  192.168/16, 169.254.169.254 link-local/metadata, ::1).
- Disable redirects, or re-validate the target after each redirect.
- Never pass raw user input straight into `requests.get(url)` / `urllib` /
  `fetch(url)`.

## Deserialization & integrity — A08, CWE-502
- Never deserialize untrusted data with `pickle`, unsafe `yaml.load` (use
  `yaml.safe_load`), `Marshal`, `shelve`, or native `readObject` /
  `ObjectInputStream`. (CWE-502)
- Prefer a data format + strict parser that cannot execute code (JSON with a
  schema). Sign and verify the integrity of any remotely loaded code/config.

## Errors & logging — A09, CWE-209/532/117
- Fail closed: on error, deny by default and reveal no internals to the user.
- Return generic errors to clients; log details server-side only. (CWE-209)
- Never log secrets/PII (passwords, tokens, card numbers, full PII). (CWE-532)
- Strip/sanitize newlines in data written to logs to stop log injection.
  (CWE-117)
- Log security events (authn success/failure, authz denials, sensitive
  actions) with user, timestamp, and outcome.

## Components — A06
- Pin versions, keep a committed lockfile, run dependency audits (see the
  `dependency-audit` skill). Prefer maintained packages with a published
  security policy.

## Golden rules
1. All input is hostile until validated.
2. Fail closed, never open.
3. Parameterize / encode / allowlist — don't concatenate or blacklist.
4. Authz is server-side, every request, object-scoped.
5. Vetted crypto only; keys/secrets come from env/vault, never literals.
