# Deploying the IRS Research MCP Server

Local development runs the server over **stdio** (how Claude Desktop / Claude
Code launch it). To share it with an agent platform, deploy it to a host that
serves it over **streamable-HTTP**, then register it.

Deployment kits are provided for two targets:

| Target | Kits | Transport | When to use |
|---|---|---|---|
| **IBM watsonx Orchestrate** | [`ibm/`](ibm/) — 3 kits | stdio (local) **or** streamable-HTTP (Code Engine) | You're evaluating/agent-building in watsonx Orchestrate |
| **Databricks** | [`databricks/`](databricks/) | streamable-HTTP (Databricks Apps) | You're deploying alongside a Databricks workspace |

## The server code doesn't change

Every kit runs the **same** server in `src/irs_research_server/`, with the
prebuilt catalog from `data/catalog/irs_catalog.json`. The entry point
`app.py` auto-selects its transport:

- a platform port (`DATABRICKS_APP_PORT` or `PORT`) → **HTTP** on that port;
- `MCP_TRANSPORT=streamable-http` → HTTP on `MCP_HOST:MCP_PORT`;
- otherwise → **stdio**.

So IBM Code Engine and Databricks Apps both "just work" — they inject a port and
the server serves HTTP with `/mcp` + `/health`.

## Authentication and limits (required for remote HTTP)

The HTTP transport **refuses to start without authentication**. Configure one of:

- **JWT bearer tokens (recommended):** `AUTH_MODE=jwt`, `JWT_ISSUER`, `JWT_AUDIENCE`, and either
  `JWT_JWKS_URI` (your identity provider's key set) or `JWT_PUBLIC_KEY` (+ `JWT_ALGORITHM`, e.g.
  `HS256` with a shared secret). Optional `JWT_REQUIRED_SCOPES` (comma-separated).
- **Platform-enforced auth:** `ALLOW_UNAUTHENTICATED_HTTP=true` only when the platform in front of
  the server already authenticates every request (set by default in the Databricks kit).

Also set `ALLOWED_HOSTS` to the public hostname(s) to enable Host/Origin (DNS-rebinding) checks.
Terminate TLS at the platform or a reverse proxy. Built-in limits (all env-configurable):
`MAX_REQUEST_BYTES` (256 KB), `RATE_LIMIT_PER_SECOND`/`RATE_LIMIT_BURST` (5/s, burst 20, per
process), `MAX_RESPONSE_BYTES` (1 MB), `MAX_PDF_BYTES` (25 MB), `MAX_CONCURRENT_FETCHES` (4).
Outbound requests, including redirects, are restricted to `https://www.irs.gov/`. Each tool call
writes one JSON line to the `irs_research_server.audit` logger.

Probes: `/health` (liveness; 503 if the catalog failed to load) and `/ready` (catalog, freshness,
cache DB writable, PDF parser; `?check_irs=true` also probes IRS.gov). Both are unauthenticated and
return status only.

## IBM (three options, lowest → highest overhead)

See [`ibm/README.md`](ibm/README.md) for the full chooser. In short:

1. **[`ibm/local-mcp-toolkit/`](ibm/local-mcp-toolkit/)** — ship the code; run it
   over stdio **inside** Orchestrate. No HTTP, no container, no registry.
   **Lowest overhead — recommended for first-timers.**
2. **[`ibm/code-engine-git-build/`](ibm/code-engine-git-build/)** — Code Engine
   builds from your public Git repo and pushes to your Container Registry.
   Needs ICR authority.
3. **[`ibm/prebuilt-image/`](ibm/prebuilt-image/)** — deploy a prebuilt public
   image to Code Engine (or build from Git). Lowest cloud authority.

## Databricks

See [`databricks/README.md`](databricks/README.md). Deploy as a Databricks App:
the kit syncs your repo to the workspace and runs `databricks apps deploy`. The
app is gated by workspace auth, so clients present a bearer token.

## cloud.gov (bonus)

Not a full kit, but the repo root includes a `manifest.yaml` for cloud.gov
(Cloud Foundry). Deploy with `cf push` after `cf login`/`cf target`. See the
comments in `manifest.yaml`.

---

## Security reminder

All kits expose the MCP endpoint with **no authentication** by default (except
Databricks, which is gated by workspace SSO). That matches a public-data
hackathon posture. For anything beyond a hackathon, front the endpoint with auth
and review your agency's ATO requirements. Never commit secrets — each kit's
`.env` is git-ignored and holds coordinates only.
