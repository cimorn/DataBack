FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS frontend
WORKDIR /build
RUN corepack enable && corepack prepare pnpm@10.28.2 --activate
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/index.html web/tsconfig.json web/vite.config.ts ./
COPY web/src ./src
COPY web/public ./public
RUN pnpm build

FROM python:3.12-slim-bookworm AS runtime
ARG SOURCE_URL
ARG REVISION
LABEL org.opencontainers.image.title="DataBack" \
      org.opencontainers.image.description="Backup history ledger" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source=$SOURCE_URL \
      org.opencontainers.image.revision=$REVISION
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 DATABACK_DATA_DIR=/data DATABACK_SNAPSHOT_DIR=/backups
WORKDIR /app
COPY web/requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY --chown=1000:10 web/backend ./backend
COPY --chown=1000:10 web/runtime ./runtime
COPY --chown=1000:10 deploy/nas/app_health.py deploy/nas/app_entrypoint.py ./deploy/nas/
COPY --chown=1000:10 --from=frontend /build/dist ./dist
RUN mkdir -p /data /backups && chown 1000:10 /data /backups && chmod -R u=rwX,go=rX /app
USER 1000:10
RUN python -c "import runtime.entrypoint, deploy.nas.app_entrypoint, deploy.nas.app_health; from pathlib import Path; Path('/app/dist/index.html').read_bytes()"
EXPOSE 8767
HEALTHCHECK --interval=30s --timeout=25s --start-period=30s CMD ["python", "-m", "runtime.health"]
ENTRYPOINT ["python", "-m", "runtime.entrypoint"]
