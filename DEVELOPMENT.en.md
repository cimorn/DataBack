# Web development

[简体中文](DEVELOPMENT.md) | **English** · [Use and deployment](README.en.md)

## Requirements

Python 3.10+, Node.js 22.15+, and pnpm 10.28.2. Run the Windows commands in PowerShell.

## First run

Install dependencies from the repository root.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r web/requirements.txt
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
cd web
..\.venv\Scripts\python.exe app.py
```

Open <http://127.0.0.1:8767> and press Ctrl+C to stop the server. The default local SQLite database is created at `data/ledger.sqlite3` in the repository root.

On Linux / macOS, use these commands.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r web/requirements.txt
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
cd web
../.venv/bin/python app.py
```

## Edit the application

| Path | Contents |
| --- | --- |
| `web/src/` | React pages, styles, and interactions |
| `web/public/` | Website icons and static files |
| `web/backend/` | HTTP API, validation, and ledger storage |
| `web/runtime/` | Container startup and health checks |
| `web/tests/` | Frontend, backend, and database tests |
| `deploy/` | Docker runtime and database deployment configuration |
| `.github/workflows/publish.yml` | Automated checks and image publishing |

For frontend changes, keep the Python server running. Open another terminal in `web/` and run the build watcher.

```sh
pnpm exec vite build --watch
```

Saving a change rebuilds the frontend. Refresh <http://127.0.0.1:8767> to view it. Restart the Python server after backend changes.

## Local configuration

`python app.py` reads process environment variables. Docker Compose loads `.env`. When running Python directly, set variables in the terminal.

For a fixed local privacy password, set this in the PowerShell terminal that starts the server.

```powershell
$env:DATABACK_PRIVACY_PASSWORD = "replace-with-your-own-privacy-password"
```

| Setting | Purpose |
| --- | --- |
| `DATABACK_PORT` | Python server port, default `8767` |
| `DATABACK_DATA_DIR` | SQLite directory, default `data/` in the repository root |
| `DATABACK_SNAPSHOT_DIR` | Snapshot directory, default `snapshots/` beside the SQLite file |
| `MONGODB_URI`, `MONGODB_DATABASE` | Development MongoDB replica-set connection and database name |

From `web/`, use the same virtual-environment Python path as in the first-run commands and add `--port 8768 --db /path/to/ledger.sqlite3` to select a port and a separate test ledger. Keep test data in a different location from your daily ledger.

## Checks

Run from `web/`.

```sh
pnpm test
pnpm build
```

On Windows, run backend tests with the project virtual environment.

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
```

On Linux / macOS, use `../.venv/bin/python`. Full MongoDB tests require `DATABACK_TEST_MONGO_URI` pointing to an isolated replica set. Those tests are skipped locally when it is unset. GitHub Actions creates a test database and runs the full checks.

## Commit and publish

1. Create a branch from the latest `main`, make changes, and run the checks.
2. Commit, push the branch, and open a PR. Reference the relevant issue in fix commits and use `Fixes #number` when the issue is fully resolved.
3. Merge into `main` after PR checks pass.
4. Wait for the GitHub Actions `test` and `publish` jobs to succeed.
5. Pull the image and update the application on the Docker host.

```sh
docker compose pull app
docker compose up -d --no-deps app
docker compose ps
```

Builds publish `ghcr.io/cimorn/databack:latest` and `sha-<full-commit-hash>` for `amd64` and `arm64`. In a NAS interface, redeploy the DataBack project with the option to pull the latest image selected.

## Data and cleanup

- For a full local backup, stop the Python server and copy `data/`. To restore, stop the server, restore the data directory, and restart.
- The server creates daily ledger snapshots and retains the latest 30. New snapshots continue to be created after old ones are cleared.
- Follow the deployment guide for Docker database, snapshot, and configuration backups. [Database backup and restore](docs/docker.en.md#full-backup)
- Run `git status --short` before committing. Commit source, lockfiles, and documentation. Keep credentials, databases, snapshots, dependencies, builds, and test output on the local or deployment host.
- After development, you can remove `web/dist/`, `web/node_modules/`, `.venv/`, and Python caches. Reinstall dependencies and rebuild when you return.
