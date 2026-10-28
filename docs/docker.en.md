# Docker data configuration

[简体中文](docker.md) | **English**

## SQLite

The root `compose.yaml` uses SQLite by default and needs no separate database container. `/data` stores the database; `/backups` stores daily ledger snapshots.

To use host directories, change the volume mounts to `./data:/data` and `./backups:/backups`, and grant container user `1000:10` write access. Copy existing data to the selected location before changing mounts.

## MongoDB

MongoDB requires a replica set with transaction support. Add these variables to the application's Compose `environment`:

```yaml
MONGODB_URI: ${MONGODB_URI:?required}
MONGODB_DATABASE: databack
```

Set your connection string in the host's `.env`. Once configured, the app uses MongoDB and fails to start if it cannot connect.

For a dedicated MongoDB instance, use `deploy/mongodb/compose.yaml`. In that directory, copy `.env.example` to `.env`. Set two different usernames and two different random passwords, each at least 32 characters long. Then run:

```sh
mkdir -p data
sudo chown 1000:10 data
docker compose up -d
```

Add the following fields to the application's Compose configuration. Set the same application database username and password in the application's own `.env`:

```yaml
services:
  app:
    environment:
      MONGODB_USERNAME: ${MONGODB_USERNAME:?required}
      MONGODB_PASSWORD: ${MONGODB_PASSWORD:?required}
    networks: [default, database]
networks:
  database:
    external: true
    name: databack_private
```

Merge these fields into the existing application configuration. For a separate connection string, use the `MONGODB_URI` configuration above instead.

For existing deployments, keep the database directory, users, passwords, replica set, and volume mounts. Replace only the application image. The original `deploy.nas.app_health` Compose health-check entry point remains supported.

To migrate an existing schema-3 ledger JSON file to MongoDB, use `web/scripts/migrate_to_mongo.py`; run it with `--help` for options.

## Full backup

For the default SQLite volume deployment, run from the repository root:

```sh
mkdir -p backups
docker compose stop app
docker run --rm -v databack_ledger:/source:ro -v "$PWD/backups:/backup" alpine tar czf /backup/ledger.tar.gz -C /source .
docker run --rm -v databack_snapshots:/source:ro -v "$PWD/backups:/backup" alpine tar czf /backup/snapshots.tar.gz -C /source .
docker compose start app
```

Keep a private copy of `.env` and `compose.yaml`. If you changed the Compose project name, replace `databack_ledger` and `databack_snapshots` with the actual volume names.

To restore into empty data volumes that have already been created:

```sh
docker compose stop app
docker run --rm -v databack_ledger:/target -v "$PWD/backups:/backup:ro" alpine tar xzf /backup/ledger.tar.gz -C /target
docker run --rm -v databack_snapshots:/target -v "$PWD/backups:/backup:ro" alpine tar xzf /backup/snapshots.tar.gz -C /target
docker compose start app
```

For MongoDB, use its backup tools, or stop the application and its database before copying the complete database directory. Restart both services afterward. Copying database files while MongoDB is running cannot guarantee a consistent backup.
