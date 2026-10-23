# DataBack

**简体中文** | [English](README.en.md)

记录文件和文件夹的备份时间、位置与说明，支持批量登记、历史查询、隐私目录和数据恢复。

## Docker 部署

支持 Linux 服务器、电脑和 NAS，镜像提供 `amd64`、`arm64` 两种架构。

```sh
git clone https://github.com/cimorn/DataBack.git
cd DataBack
cp .env.example .env
```

修改 `.env` 中的登录密码、隐私密码和会话密钥。登录密码至少 16 字符，会话密钥至少 32 字符；使用独立随机值。

```sh
docker compose up -d
```

打开 <http://127.0.0.1:8767>。数据保存在 Docker 卷 `databack_ledger`，自动备份保存在 `databack_snapshots`。

通过域名访问时配置 HTTPS 反向代理，并在 `.env` 修改：

```dotenv
DATABACK_PUBLIC_ORIGIN=https://backup.example.com
DATABACK_ALLOWED_HOSTS=backup.example.com,127.0.0.1:8767,localhost:8767
```

代理在其他设备上时，设置 `DATABACK_BIND_IP` 为 Docker 主机的内网地址。外部访问使用 HTTPS，本机访问可使用 HTTP。

已有 MongoDB 可按 [数据库配置](docs/docker.md) 接入。

## 使用

- **添加资料：** 新建文件夹／添加文件 → 填写名称、类型和所属目录。
- **登记备份：** 将末级文件或文件夹设为备份对象 → 点击名称 → 新增记录 → 填写时间、位置、说明。
- **批量登记：** 点击批量登记后勾选项目，或粘贴表格，每批最多 20 条。
- **查询与修改：** 历史记录 → 筛选资料、位置、日期 → 编辑或删除记录。
- **隐私目录：** 点击左上角 DataBack → 输入隐私密码；再次点击收起。
- **撤销删除：** 删除提示中点击撤销，或到数据管理的回收站恢复，保留 30 天。

## 更新

```sh
docker compose pull app
docker compose up -d app
```

镜像更新保留数据卷和 `.env`。查看运行状态：`docker compose ps`；查看日志：`docker compose logs --tail=100 app`。

## 开发与发布

需要 Python 3.10+、Node.js 22.15+、pnpm 10.28.2。

```sh
cd web
python -m pip install -r requirements.txt
pnpm install --frozen-lockfile
pnpm build
python app.py
```

打开 <http://127.0.0.1:8767>。开发界面时另开终端，在 `web/` 运行 `pnpm dev`，API 由 Python 服务提供。

```sh
pnpm test
pnpm build
python -m unittest discover -s tests -p 'test_*.py'
```

MongoDB 集成测试使用独立副本集，通过 `DATABACK_TEST_MONGO_URI` 指定测试连接。CI 自动创建测试数据库。

修改 `web/src/` 更新界面，修改 `web/backend/` 更新接口。提交并推送到 `main` 后，GitHub Actions 自动测试并发布 `ghcr.io/cimorn/databack:latest`。也会发布 `sha-完整提交编号` 标签，可用于固定镜像或回退。

本机构建镜像：`docker build -t databack .`（在项目根目录执行）。

## 桌面启动

Windows 构建 `web/` 后，双击 `desktop/启动.cmd`；关闭时双击 `desktop/关闭.cmd`。启动器使用浏览器打开本机网页，数据保存在项目根目录的 `data/`。

使用本机隐私目录前，设置 `DATABACK_PRIVACY_PASSWORD` 环境变量。

## 数据备份与恢复

- **导出：** 解锁隐私目录 → 数据管理 → 导出 JSON，保存完整台账。
- **自动备份：** 数据管理 → 自动备份，每天一份、保留最新 30 份，可下载或立即备份。
- **恢复：** 选择备份 → 核对内容 → 确认恢复；替换前自动保存当前台账。
- **本机完整备份：** 关闭程序后复制 `data/`。
- **Docker 完整备份：** 停止服务后备份数据卷和 `.env`，命令见 [数据备份](docs/docker.md#完整备份)。

台账 JSON 包含目录和备份记录，实际文件、回收站内容和部署密码另行保存。

## 目录

```text
web/               网页界面、API、测试
desktop/           Windows 启动与关闭脚本
deploy/mongodb/    可选 MongoDB 副本集配置
Dockerfile         镜像构建
compose.yaml       Docker 部署
```

[MIT License](LICENSE)
