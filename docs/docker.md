# Docker 数据配置

**简体中文** | [English](docker.en.md)

## SQLite

根目录 `compose.yaml` 默认使用 SQLite，不需要单独的数据库容器。`/data` 保存数据库，`/backups` 保存每日台账快照。

使用主机文件夹时，将卷改为 `./data:/data` 和 `./backups:/backups`，并授予容器用户 `1000:10` 写权限。已有数据应先复制到指定位置，再调整挂载。

## MongoDB

MongoDB 需要支持事务的副本集。在应用的 Compose `environment` 中添加：

```yaml
MONGODB_URI: ${MONGODB_URI:?required}
MONGODB_DATABASE: databack
```

在主机 `.env` 设置自己的连接串。配置 MongoDB 后，应用直接使用该数据库，连接失败时启动失败。

新建专用 MongoDB 可使用 `deploy/mongodb/compose.yaml`。在该目录复制 `.env.example` 为 `.env`，填写两个不同的用户名和两个不同的随机密码（至少 32 字符），然后执行：

```sh
mkdir -p data
sudo chown 1000:10 data
docker compose up -d
```

在应用 Compose 中添加连接配置，并在应用自己的 `.env` 填入相同的应用数据库用户名和密码：

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

这是在原应用配置上增加的字段。使用独立连接串时，改用前述 `MONGODB_URI` 配置。

沿用已有部署时，保留原数据库目录、用户、密码、副本集和挂载配置，仅替换应用镜像。原 Compose 的 `deploy.nas.app_health` 健康检查入口仍可使用。

将已有 schema-3 台账 JSON 迁移到 MongoDB 时，使用 `web/scripts/migrate_to_mongo.py`，运行 `--help` 查看参数。

## 完整备份

SQLite 默认卷部署，在项目根目录执行：

```sh
mkdir -p backups
docker compose stop app
docker run --rm -v databack_ledger:/source:ro -v "$PWD/backups:/backup" alpine tar czf /backup/ledger.tar.gz -C /source .
docker run --rm -v databack_snapshots:/source:ro -v "$PWD/backups:/backup" alpine tar czf /backup/snapshots.tar.gz -C /source .
docker compose start app
```

另行私下保存 `.env`、`compose.yaml`。自定义项目名称时，将卷名 `databack_ledger`、`databack_snapshots` 替换为实际名称。

恢复到已创建的空数据卷：

```sh
docker compose stop app
docker run --rm -v databack_ledger:/target -v "$PWD/backups:/backup:ro" alpine tar xzf /backup/ledger.tar.gz -C /target
docker run --rm -v databack_snapshots:/target -v "$PWD/backups:/backup:ro" alpine tar xzf /backup/snapshots.tar.gz -C /target
docker compose start app
```

MongoDB 使用自己的备份工具，或停止应用与该数据库后复制完整数据目录，再恢复服务。复制运行中的数据库文件不能保证一致性。
