# Security Policy

As a U.S. Government agency, the General Services Administration (GSA) takes seriously our responsibility to protect the public's information, including financial and personal information, from unwarranted disclosure.

Software developed by the U.S. General Services Administration (GSA) is subject to the GSA Vulnerability Disclosure Policy.

Please consult our policy for:

How to submit a report if you believe you have discovered a vulnerability.
GSA's coordinated disclosure policy.
Information on how you may conduct security research on GSA developed software and systems.
Important legal and policy guidelines.

## Supported Versions

Use this section to tell people about which versions of your project are
currently being supported with security updates.

| Version (Branch) | Supported          |
| ------- | ------------------ |
| main   | :white_check_mark: |
| other  | :x:

## Security Researchers

Security researchers shall:

- Make every effort to avoid privacy violations, degradation of user experience, disruption to production systems, and destruction or manipulation of data.
- Only use exploits to the extent necessary to confirm a vulnerability. Do not use an exploit to compromise or exfiltrate data, establish command line access and/or persistence, or use the exploit to "pivot" to other systems. Once you've established that a vulnerability exists, or encountered any of the sensitive data outlined above, you must stop your test and notify us immediately.
- Keep confidential any information about discovered vulnerabilities for up to 90 calendar days after you have notified GSA. For details, please review Coordinated Disclosure.

## Hackathon note

Over the HTTP transport the server **fails closed**: it refuses to start unless `AUTH_MODE=jwt` is configured (JWKS or shared-secret JWT validation with required issuer and audience), or `ALLOW_UNAUTHENTICATED_HTTP=true` is set because a platform in front of it already authenticates every request (as Databricks Apps do). Additional controls: request-body limit (413), per-process rate limiting, response-size limit, PDF download size cap and fetch concurrency limit, outbound requests restricted to `https://www.irs.gov/` on every redirect hop, optional Host/Origin validation (`ALLOWED_HOSTS`), and a structured audit line per tool call. `/health` and `/ready` are unauthenticated and return operational status only. TLS termination, per-client quotas, and dependency/container scanning are the deploying platform's responsibility; review your agency's ATO requirements. Never commit secrets — keep JWT secrets and API keys in environment variables / `.env` (git-ignored), not in source.

The starter HTTP helper uses a fixed API origin, rejects absolute or escaping paths,
disables redirects and environment proxies, and sanitizes upstream failures. Do not
replace its relative path with a tool-supplied URL. If a tool must fetch arbitrary
URLs, require exact destination allowlists, connection-time validation of every
resolved IPv4/IPv6 address, validation at every redirect hop, credential stripping
across origins, and network-level egress controls. A DNS lookup followed by a normal
hostname request is vulnerable to DNS rebinding.

Treat all tool arguments, retrieved content, and agent-to-agent messages as
untrusted. Do not include upstream response bodies, headers, full URLs, query
strings, stack traces, credentials, names, SSNs, dates of birth, addresses, or other
sensitive values in MCP errors or logs. Use structured logs with explicitly
allowlisted fields and define retention and access controls before processing PII.
