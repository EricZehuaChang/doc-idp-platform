#!/bin/sh
# Private-deploy container entrypoint: render model configs from the
# environment (idp.env), then run the single process that serves API, web UI
# and the in-process task runner. One worker on purpose: the lite tier's task
# queue lives inside this process.
set -eu
mkdir -p /srv/idp/data /srv/idp/configs
python /app/deploy/render_configs.py
exec python -m uvicorn app.main:create_app --factory \
    --host 0.0.0.0 --port "${IDP_PORT:-8080}" --workers 1
