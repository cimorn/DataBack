# DataBack

[简体中文](README.md) | **English**

Track backup times, locations, and notes for files and folders. Supports batch entry, backup history, private folders, and data recovery.

## Deploy with Docker

Runs on Linux servers, computers, and NAS devices. Images are available for `amd64` and `arm64`.

```sh
git clone https://github.com/cimorn/DataBack.git
cd DataBack
cp .env.example .env
```

Set your login password, privacy password, and session secret in `.env`. Use separate random values: at least 16 characters for the login password and 32 for the session secret.

```sh
docker compose up -d
```

Open <http://127.0.0.1:8767>. The `databack_ledger` Docker volume stores the database; `databack_snapshots` stores automatic backups.

For domain access, configure an HTTPS reverse proxy and update `.env`:

```dotenv
DATABACK_PUBLIC_ORIGIN=https://backup.example.com
DATABACK_ALLOWED_HOSTS=backup.example.com,127.0.0.1:8767,localhost:8767
```

If the proxy runs on another device, set `DATABACK_BIND_IP` to the Docker host's LAN address. Remote access requires HTTPS; local access can use HTTP.

To connect an existing MongoDB instance, see [Database configuration](docs/docker.en.md).

## Use

- **Add items:** Create a folder or file entry, then set its name, type, and parent folder.
- **Record a backup:** Mark a leaf file or folder as a backup target → click its name → add a record with the backup time, location, and notes.
- **Batch entry:** Enter batch mode and select items, or paste a table. Each batch supports up to 20 records.
- **Find and edit records:** Open backup history → filter by item, location, or date → edit or delete records.
- **Private folders:** Click DataBack in the upper-left corner and enter your privacy password. Click it again to hide private folders.
- **Undo deletion:** Use Undo in the deletion notification, or restore items from the recycle bin in data management within 30 days.

## Update

```sh
docker compose pull app
docker compose up -d app
```

Updates preserve data volumes and `.env`. Check status with `docker compose ps` and logs with `docker compose logs --tail=100 app`.

## Develop and publish

Requires Python 3.10+, Node.js 22.15+, and pnpm 10.28.2.

```sh
cd web
python -m pip install -r requirements.txt
pnpm install --frozen-lockfile
pnpm build
python app.py
```

Open <http://127.0.0.1:8767>. For frontend development, run `pnpm dev` from `web/` in another terminal. Keep the Python server running for API requests.

```sh
pnpm test
pnpm build
python -m unittest discover -s tests -p 'test_*.py'
```

MongoDB integration tests require a separate replica set. Set `DATABACK_TEST_MONGO_URI` to its connection string. CI creates an isolated test database automatically.

Edit `web/src/` for the interface and `web/backend/` for the API. Push to `main` to run tests and publish `ghcr.io/cimorn/databack:latest` through GitHub Actions. Each build also publishes a `sha-<full-commit-hash>` tag for pinning or rolling back an image.

To build locally, run `docker build -t databack .` from the repository root.

## Desktop launcher

On Windows, build `web/`, then double-click `desktop/启动.cmd` to start and `desktop/关闭.cmd` to stop. The launcher opens the local web app in your browser. Data is stored in `data/` at the repository root.

Set the `DATABACK_PRIVACY_PASSWORD` environment variable before using private folders locally.

## Back up and restore data

- **Export:** Unlock private folders → open data management → export JSON to save the complete ledger.
- **Automatic backups:** Open automatic backups in data management. DataBack keeps one backup per day and retains the latest 30. You can download a backup or create one immediately.
- **Restore:** Select a backup → review its contents → confirm. DataBack saves the current ledger before replacing it.
- **Full local backup:** Stop the app, then copy `data/`.
- **Full Docker backup:** Stop the service and back up its data volumes and `.env`. See [Full backup](docs/docker.en.md#full-backup) for commands.

Ledger JSON contains folders and backup records. Save actual files, recycle-bin contents, and deployment credentials separately.

## Layout

```text
web/               Web interface, API, and tests
desktop/           Windows start and stop scripts
deploy/mongodb/    Optional MongoDB replica-set configuration
Dockerfile         Image build
compose.yaml       Docker deployment
```

[MIT License](LICENSE)
