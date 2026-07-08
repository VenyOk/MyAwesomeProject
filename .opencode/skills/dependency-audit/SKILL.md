---
name: dependency-audit
description: Audits third-party dependencies for known vulnerabilities and risky versions. Use when adding, updating, or pinning packages, touching lockfiles (package-lock.json, yarn.lock, pnpm-lock.yaml, poetry.lock, requirements.txt, go.sum), or when the user asks about CVEs, advisories, SBOM, licenses, or vulnerable/outdated dependencies.
---

# Dependency Audit

Every open-source dependency inherits the security posture of all its
transitive packages. A scanner reads your lockfile, resolves direct +
transitive versions, and matches them against advisory databases. Always pin
versions and keep a committed lockfile so scans are reproducible. (OWASP A06)

## What to run (by ecosystem)
- **Python** (`requirements.txt` / `pyproject.toml` / `poetry.lock`) →
  `pip-audit` (PyPI Advisory DB + OSV).
- **Node** (`package-lock.json` / `yarn.lock` / `pnpm-lock.yaml`) → `npm audit`
  / `yarn audit` / `pnpm audit` (npm advisory DB).
- **Cross-ecosystem / SBOM / second opinion** → `osv-scanner` (OSV.dev; many
  languages; scans lockfiles and SBOMs).
- **GitHub repo** → enable Dependabot alerts + security updates (free, opens
  fix PRs).

## Python — commands
```bash
pip install pip-audit
pip-audit                       # scan current environment
pip-audit -r requirements.txt   # scan a requirements file
pip-audit --format json -o vulns.json     # machine-readable output
pip-audit --fix                 # auto-upgrade — REVIEW the diff first

# second advisory source (catches CVEs pip-audit may miss)
osv-scanner -r requirements.txt
osv-scanner -L poetry.lock
```

## Node — commands
```bash
npm audit                       # reads package-lock.json
npm audit --audit-level=high    # exit non-zero only at high+
npm audit --json                # machine-readable output
npm audit fix                   # install safe updates
npm audit fix --force           # BREAKING upgrades — REVIEW before running

osv-scanner -L package-lock.json      # second advisory source
yarn audit                            # yarn
pnpm audit                            # pnpm
```

## Pin first (do this before auditing)
Unpinned or caret/tilde ranges let a `^` bump pull in a compromised or buggy
release from the registry at install time.
- **Python:** pin in `requirements.txt` (`requests==2.31.0`, not `requests`).
  Use `pip-tools` (`pip-compile`) to produce a fully pinned, hashed lockfile.
- **Node:** commit `package-lock.json` / `yarn.lock`; run `npm ci` in CI (it
  fails if the lockfile is out of sync with `package.json`).
- **Go:** commit `go.sum`.

## Severity & triage
Advisories are graded Critical / High / Medium / Low (usually CVSS).
- Critical/High in **reachable** code → fix or replace now.
- Low/informational in unreachable code → track and schedule; don't block.
- Prefer fixing over suppressing. If you must ignore an advisory, document why
  and set an expiry, e.g. `# ignoring GHSA-xxxx — affected API unused, revisit 2026-Q3`.

## SBOM (Software Bill of Materials)
An SBOM is a machine-readable inventory of every component (direct +
transitive), versions, and licenses — useful for compliance and continuous
monitoring. Generate with CycloneDX or SPDX:
```bash
# Node
npx @cyclonedx/cyclonedx-npm --output-file sbom.json
# Python
pip install cyclonedx-bom
cyclonedx-py requirements requirements.txt -o sbom.xml
# scan the SBOM for vulns
osv-scanner --sbom sbom.json
grype sbom:./sbom.json
```

## License considerations
Free CVE scanners (pip-audit, npm audit, osv-scanner) do NOT check licenses.
A transitive GPL/AGPL dependency can create legal risk for proprietary code.
- Inspect licenses: `pip-licenses` (Python), `license-checker` (Node).
- Flag copyleft (GPL, AGPL) and unusual licenses for legal review before
  shipping proprietary products.

## Quick checklist
- [ ] Versions pinned; lockfile committed
- [ ] `pip-audit` / `npm audit` / `osv-scanner` run, results reviewed
- [ ] Critical/High advisories in reachable code are fixed or replaced
- [ ] Dependabot (or equivalent) enabled
- [ ] SBOM generated for compliance-sensitive releases
- [ ] Licenses reviewed for proprietary distribution
