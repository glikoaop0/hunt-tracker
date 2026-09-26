# Security policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

## Intended deployment and threat model

Hunt Tracker is a single-user application. It is meant to run on your own machine, bound to 127.0.0.1, which is Uvicorn's default.

It has **no authentication and no authorization**. Anyone who can reach the port can read and change all data.

Do not:

- Expose it on a network, for example with `--host 0.0.0.0`.
- Put it behind a public reverse proxy.
- Run it on a shared server or a machine other people can log in to.

The protections described below assume this local, single-user setup. They are not a substitute for authentication.

## Reporting a vulnerability

Please **do not open a public issue** for a security vulnerability.

Report it privately through GitHub:

1. Go to the **Security** tab of this repository.
2. Click **Report a vulnerability**.
3. Fill in the form.

Direct link: [Report a vulnerability]()

Only the maintainer can see your report. If an advisory is published, you are credited in it.

In the form, please include:

- **Summary:** the problem and its impact, in a few lines.
- **Details:** the affected page, endpoint or file (for example `app/routes.py`).
- **PoC:** steps to reproduce, including the version (for example 0.1.0), your OS and Python version, and how you started the app.
- **Impact:** what an attacker could read or change, and what they need first (local access, a malicious link, etc.).

Under **Affected products**, you can leave the ecosystem empty and put the version you tested in **Affected versions**.

For the moment, Hunt Tracker is maintained by one person in their spare time. Reports are handled on a best-effort basis.

Before reporting, check the known limitations below. They are documented and already accepted for this release. Issues that only exist when the app is exposed to a network, against the guidance above, are covered by those limitations.

## Built-in protections

### CSRF

- Every state-changing request (POST, and any future PUT, PATCH or DELETE) is checked centrally by middleware before any route runs.
- A random per-session token is kept in an HttpOnly, SameSite=Strict session cookie. HTML forms carry the token in a hidden field. HTMX sends it in an `X-CSRF-Token` header.
- As defense in depth, requests whose `Origin` (or `Referer`) is not the app's own origin are rejected.
- No state-changing action is reachable with GET.

### XSS and Markdown sanitization

- All templates auto-escape.
- User-written Markdown (hunt notes, run notes, retirement notes, campaign scope and notes) is rendered, then sanitized with nh3 using a strict allowlist of the tags Markdown produces.
- Links and images only allow the `http`, `https` and `mailto` schemes.
- Raw HTML, scripts, event handlers, iframes and `javascript:` URLs are removed.

### HTMX hardening

- HTMX runs with `allowScriptTags: false` and `allowEval: false`.

### No outbound traffic

- No telemetry, no analytics, no update checks, no CDN.
- Fonts and HTMX are served locally.
- Stored queries are never executed against any system.

### Destructive actions

- Deleting a hunt needs two explicit confirmations plus typing DELETE. This is checked on the server.

### Dependencies

- Python dependencies are pinned to exact versions in `requirements.txt`.
- HTMX and the fonts are vendored in the repository.

## Known limitations

- No authentication or authorization.
- No HTTPS. The app serves plain HTTP on localhost only.
- No encryption at rest.
- No rate limiting.
- No Content-Security-Policy or other security response headers yet.
- Not designed or reviewed for multi-user or networked use.

## Protecting your data

Hunt queries, notes, exclusions (often real hostnames and domains) and findings can be sensitive. All of it is stored in plain text in:

- `hunts.db` (and `demo.db`) in the project root.
- Everything in `backups/`, including the copies made by `python -m app.migrate`.
- Every file you download from the Export page: JSON exports, SQLite backups and per-hunt Markdown exports.

There is no encryption at rest. Protect these files like any other investigation data:

- Do not commit them. `*.db` files and `backups/` are git-ignored; keep it that way, and keep export files out of the repository too.
- Do not keep them in shared or synced folders that other people can access.
- Use demo mode (`HUNT_TRACKER_DB=demo`) for screenshots, recordings and demos, so real hunts never appear on screen.