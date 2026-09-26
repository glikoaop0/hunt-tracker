# Hunt Tracker

**Track threat hunts through their whole lifecycle: from hypothesis, to a recurring cadence of logged runs, to a retirement that records *why*.**

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Local only](https://img.shields.io/badge/runs-local%20only-informational)

![Hunt Tracker board](screenshots/board.png)

---

## Why it exists

Most hunt programs quietly die in a spreadsheet or a notes app. A hunt gets run once, never re-run, and when it's dropped nobody records whether it became a detection, failed for lack of telemetry, or was simply forgotten.

Hunt Tracker treats each hunt as a living ticket built around a **hypothesis** and a **query**. Every execution is logged as a run with an explicit outcome and a snapshot of the query used. Hunts can run on a cadence, so you see what's overdue. And when a hunt is retired, the reason is kept, so over time you learn what your hunting actually produces.

It's small, local and single-user by design: no accounts, no cloud, no integrations. Just your hunts, on your machine.

## Key features

- **Lifecycle board**: Idea → Scoped → Active → Retired, with drag-and-drop, filters, and overdue warnings.
- **Guided next step**: each hunt page shows where it stands in the lifecycle and offers the natural next action.
- **Run history with query snapshots**: every run records its outcome, search window, result count and notes, plus the exact query used at that time. Editing a query never rewrites the past.
- **Cadence scheduling**: set a hunt to repeat every N days; the next run date advances as you log runs.
- **Retirement with a reason**: converted to detection, missing telemetry, hypothesis rejected, and more. Reactivating keeps the previous retirement as history.
- **Exclusions**: track known-benign values per hunt, with a reason. Deactivated, never deleted.
- **Campaigns**: group related hunts around one investigation objective, with scope, activity and a written conclusion.
- **Insights**: lifecycle distribution, run outcomes, retirement reasons, and a 12-month activity heatmap.
- **Export**: full JSON export, SQLite backup download, and per-hunt Markdown (ready to paste into Obsidian).

## Quick start

Requirements: **Python 3.11+** and **git**. No Node, no build step, no Docker, no external service.

### Windows (PowerShell)

```powershell
git clone https://github.com/glikoaop0/hunt-tracker.git
cd hunt-tracker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

> If `Activate.ps1` fails with an execution policy error, run this once, then retry:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

**Shortcut:** after the first setup, just double-click `run.bat`.

### macOS / Linux

```bash
git clone https://github.com/glikoaop0/hunt-tracker.git
cd hunt-tracker
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Then open **http://127.0.0.1:8000**. The app creates its database on first start, and the board starts empty.

Next time, you only need to activate the virtual environment and run the last command.

## Demo mode

Want to take screenshots or record a demo without exposing your real hunts? Demo mode uses a separate database and shows a **DEMO DATA** badge in the top bar.

```powershell
# Windows (PowerShell)
$env:HUNT_TRACKER_DB = "demo"; python -m uvicorn app.main:app --reload --port 8001
```

```bash
# macOS / Linux
HUNT_TRACKER_DB=demo python -m uvicorn app.main:app --reload --port 8001
```

On Windows you can also double-click `run-demo.bat`. Demo mode opens on **http://127.0.0.1:8001** and never touches your real data.

> In PowerShell the variable stays set for that window. Open a new window (or run `Remove-Item Env:HUNT_TRACKER_DB`) before starting the app on your real data. Any value other than `demo` makes the app refuse to start, so it can never silently fall back to the wrong database.

## Data and backups

All data lives in a single SQLite file in the project root: `hunts.db` (or `demo.db` in demo mode). Database files and the `backups/` folder are git-ignored.

- **Backup anytime** from the Export page (`/export`).
- **After updating** to a version with schema changes, run:

  ```
  python -m app.migrate
  ```

  It backs up your database to `backups/` first, then adds any missing tables and columns. Safe to run repeatedly.

## How it works

**Core objects**

- **Hunt**: hypothesis, query, status, priority (1–5), data sources, ATT&CK technique IDs, Markdown notes, optional cadence.
- **Run**: one execution of a hunt, with an outcome (*No findings, Findings, Inconclusive, Detection opportunity*), optional duration, search window and result count, notes, and a query snapshot.
- **Exclusion**: a known-benign value and why it's excluded.
- **Campaign**: a group of hunts around one objective. A hunt can belong to several campaigns.

**Rules the app enforces**

- Retiring always requires a reason. Other status moves are free.
- Logging a run advances the next run date by the cadence.
- Past runs keep their own query snapshot, even if the hunt's query changes.
- Closing a campaign never retires its hunts; unlinking never deletes them.
- Deleting a hunt is permanent and requires typing `DELETE` on a confirmation page that lists exactly what will be removed.

## Security

Hunt Tracker is a **single-user app meant to run on your own machine**, bound to `127.0.0.1`. It has **no authentication**: anyone who can reach the port can read and change everything.

**Do not** expose it on a network (e.g. `--host 0.0.0.0`), put it behind a public reverse proxy, or run it on a shared server.

Built-in protections include CSRF protection on every change, sanitized Markdown rendering, and strict security headers. Your database, backups and exports contain your hunts, queries and exclusions in plain text, so treat them like any other investigation data.

See [SECURITY.md](SECURITY.md) for the threat model, known limitations, and how to report a vulnerability.

## Non-goals

Hunt Tracker is deliberately small. It does not include, and doesn't plan to include:

- Authentication or multi-user accounts
- SIEM, EDR or API integrations: queries are stored and copied, never executed
- File uploads or attachments
- A rich-text editor (notes are Markdown)
- Background jobs or deployment configuration

## Roadmap

Ideas being considered for future versions:

- Realistic demo dataset based on public ATT&CK techniques
- ATT&CK Navigator layer export (coverage by status and last run)
- Telemetry gap report built from "missing telemetry" retirements
- Sigma rule stub when a hunt is retired as "converted to detection"
- Query versioning with diffs

Suggestions are welcome: open an issue.

## Running the tests

With the virtual environment active:

```
python -m pytest
```

Tests run against an in-memory database and never touch your real data.

## Tech stack

- **Backend**: Python, FastAPI, Uvicorn
- **Database**: SQLite via SQLModel, one local file
- **Frontend**: Jinja2 templates + HTMX (vendored), plain CSS, self-hosted fonts
- **Markdown**: Python-Markdown, sanitized with nh3
- No Node, no bundler, no CDN. All dependencies pinned in `requirements.txt`.



## Contributing

Issues and pull requests are welcome. For anything bigger than a small fix, please open an issue first to discuss the idea. Run the test suite before submitting.

## Credits

Built and maintained by [glikoaop0](https://github.com/glikoaop0).

Vendored components:

- [HTMX](https://htmx.org) 2.0.10 (0BSD)
- Archivo font (SIL Open Font License 1.1)
- IBM Plex Mono font (SIL Open Font License 1.1)


## License

No license has been chosen yet. All rights are reserved by the author.

