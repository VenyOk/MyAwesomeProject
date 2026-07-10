# MCP Servers (local cache)

This folder preinstalls the **MCP (Model Context Protocol)** servers used by opencode
so they start instantly without fetching via `npx -y` on every launch.

## Installed servers

| Server | Type | Purpose | Needs key? |
|---|---|---|---|
| `playwright` | local | Browser automation & UI testing (drives Edge) | no |
| `context7` | local | Up-to-date library/framework docs, pinned to versions | optional (`CONTEXT7_API_KEY`) |
| `sequential-thinking` | local | Multi-step reasoning server | no |
| `fetch` | local | Fetch a URL → cleaned markdown/text | no |
| `tavily` | remote | Web search | yes (`TAVILY_API_KEY`) |
| `github` | remote | Repos, issues, PRs | yes (`GITHUB_PERSONAL_ACCESS_TOKEN`) |

Local servers run from `node_modules/` in this folder (see `package.json` and the
canonical mirror in `mcp.json`). The **active** config that opencode reads lives in
`../opencode.json`; this folder just provides the runtime + documentation.

## Managing servers

```bash
# add a new server
npm install <package>            # installs into ./node_modules
# then wire it into ../opencode.json with a "node .../path/to/entry.js" command

# update all servers
npm update

# reinstall after pulling (node_modules is gitignored)
npm install
```

> `npm install` here may hit transient `ECONNRESET` on flaky networks. If it does,
> retry with retries: `npm install --fetch-retries=5 --fetch-retry-mintimeout=20000`.

## Environment variables

Set these in your shell (or `.env`) before launching opencode. See `mcp.json` →

- `CONTEXT7_API_KEY` — optional, lifts rate limits
- `TAVILY_API_KEY` — required for the `tavily` remote server
- `GITHUB_PERSONAL_ACCESS_TOKEN` — required for the `github` remote server

`node_modules/` is gitignored and must be reinstalled after a fresh clone.
