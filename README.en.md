# DataBack

[简体中文](README.md) | **English**

Track backup times, locations, and notes for files and folders. Supports batch entry, backup history, and private folders.

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
- **Undo deletion:** Click Undo in the deletion notification.

## Update

```sh
docker compose pull app
docker compose up -d app
```

Updates preserve data volumes and `.env`. Check status with `docker compose ps` and logs with `docker compose logs --tail=100 app`.

## Develop and publish

See [Web development](DEVELOPMENT.en.md) for setup, local previews, code changes, and test commands.

Push a branch and open a PR → merge into `main` after checks pass → wait for GitHub Actions to publish the image → pull the update on the Docker host. Images use `latest` and `sha-<full-commit-hash>` tags.

To build locally, run `docker build -t databack .` from the repository root.

## Back up and restore data

- **Automatic backups:** The server saves one ledger snapshot per day and retains the latest 30. Docker stores these in the `databack_snapshots` volume by default.
- **Full local backup:** Stop the app, then copy `data/`.
- **Full Docker backup:** Stop the service and back up its data volumes and `.env`. See [Full backup](docs/docker.en.md#full-backup) for commands.

The ledger records folders and backup information. Save actual files and deployment credentials separately.

## Layout

```text
web/               Web interface, API, and tests
DEVELOPMENT.en.md  Web development guide
deploy/mongodb/    Optional MongoDB replica-set configuration
Dockerfile         Image build
compose.yaml       Docker deployment
```

[MIT License](LICENSE)
