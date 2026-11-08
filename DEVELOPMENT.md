# 网页开发

**简体中文** | [English](DEVELOPMENT.en.md) · [使用与部署](README.md)

## 环境

Python 3.10+、Node.js 22.15+、pnpm 10.28.2。以下 Windows 命令在 PowerShell 中执行。

## 首次运行

在项目根目录安装依赖。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r web/requirements.txt
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
cd web
..\.venv\Scripts\python.exe app.py
```

打开 <http://127.0.0.1:8767>，按 Ctrl+C 停止。默认使用本机 SQLite，首次运行会在项目根目录创建 `data/ledger.sqlite3`。

Linux / macOS 使用以下命令。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r web/requirements.txt
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
cd web
../.venv/bin/python app.py
```

## 修改代码

| 路径 | 内容 |
| --- | --- |
| `web/src/` | React 页面、样式和交互 |
| `web/public/` | 网站图标和静态文件 |
| `web/backend/` | HTTP 接口、校验和台账存储 |
| `web/runtime/` | 容器启动与健康检查 |
| `web/tests/` | 前端、后端和数据库测试 |
| `deploy/` | Docker 运行与数据库部署配置 |
| `.github/workflows/publish.yml` | 自动测试和镜像发布 |

修改前端时，保留 Python 服务，在另一个终端进入 `web/` 执行。

```sh
pnpm exec vite build --watch
```

保存代码后会重新构建，刷新 <http://127.0.0.1:8767> 查看。修改后端后，重启 Python 服务。

## 本机配置

`python app.py` 读取进程环境变量。`.env` 由 Docker Compose 加载，直接运行 Python 时需在终端设置变量。

在启动服务的 PowerShell 终端中，可设置固定的本机隐私密码。

```powershell
$env:DATABACK_PRIVACY_PASSWORD = "替换为你自己的隐私密码"
```

| 配置 | 用途 |
| --- | --- |
| `DATABACK_PORT` | Python 服务端口，默认 `8767` |
| `DATABACK_DATA_DIR` | SQLite 数据目录，默认项目根目录的 `data/` |
| `DATABACK_SNAPSHOT_DIR` | 快照目录，默认与 SQLite 文件同级的 `snapshots/` |
| `MONGODB_URI`、`MONGODB_DATABASE` | 开发用 MongoDB 副本集连接和数据库名 |

在 `web/` 沿用首次运行的虚拟环境 Python 路径，添加 `--port 8768 --db /path/to/ledger.sqlite3` 可指定端口和独立测试台账。测试数据与日常台账使用不同路径。

## 检查

在 `web/` 执行。

```sh
pnpm test
pnpm build
```

Windows 后端测试使用项目虚拟环境。

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
```

Linux / macOS 将 Python 路径换为 `../.venv/bin/python`。完整 MongoDB 测试需用 `DATABACK_TEST_MONGO_URI` 指向独立副本集，本机未配置时对应测试会跳过。GitHub Actions 会创建测试数据库并执行完整检查。

## 提交与发布

1. 从最新 `main` 创建分支，修改并完成检查。
2. 提交、推送分支，创建 PR。修复提交引用对应 issue，完整解决使用 `Fixes #编号`。
3. PR 检查通过后合并到 `main`。
4. 等待 GitHub Actions 的 `test` 和 `publish` 成功。
5. Docker 主机拉取镜像并更新应用。

```sh
docker compose pull app
docker compose up -d --no-deps app
docker compose ps
```

构建发布 `ghcr.io/cimorn/databack:latest` 和 `sha-完整提交编号`，支持 `amd64`、`arm64`。NAS 图形界面可在 DataBack 项目中重新部署并勾选“拉取最新镜像”。

## 数据与清理

- 本机完整备份时，先停止 Python 服务，再复制 `data/`。恢复时停止服务，放回对应数据目录后启动。
- 服务端每天生成台账快照，保留最新 30 份。清空历史快照后，服务会继续生成新快照。
- Docker 的数据库、快照和配置备份按部署文档操作。 [数据库备份与恢复](docs/docker.md#完整备份)
- 提交前运行 `git status --short`，只提交源码、锁文件和文档。凭据、数据库、快照、依赖、构建产物和测试输出保存在本机或部署主机。
- 开发结束后可删除 `web/dist/`、`web/node_modules/`、`.venv/` 和 Python 缓存，再次开发时重新安装依赖并构建。
