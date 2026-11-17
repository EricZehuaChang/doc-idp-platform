# syntax=docker/dockerfile:1.7
# Private-deploy image (offline customer sites, first used for the BOC POC
# 2026-11). One container runs API + web UI + the in-process task runner on
# SQLite — the lite shape production has run since August. The same file
# builds linux/arm64 (BOC: Kylin V10 SP2 on ARM VMs) and linux/amd64.
#
# Build from the repo root (deploy/offline/build_package.sh does this):
#   docker buildx build --platform linux/arm64 \
#     --build-context models=<dir holding the two .onnx weights> \
#     -f deploy/docker/Dockerfile -t idp-platform:<tag> --load .
FROM python:3.12-slim-bookworm

# - openjdk-17-jre-headless: opendataloader-pdf (electronic-PDF first pass)
#   needs Java 11+; without it PDFs silently fall back to pdfplumber. The man
#   dir must exist or the JRE postinst fails on slim images.
# - fonts-noto-cjk: PDFium falls back to system fonts for non-embedded CJK
#   glyphs when pages are rasterized for detection / multimodal reading.
# - tzdata: license expiry and output naming are Asia/Shanghai dates.
RUN mkdir -p /usr/share/man/man1 \
    && apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless fonts-noto-cjk tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so code edits do not re-resolve wheels. The list comes
# from pyproject (core + parsers/output/vision extras); constraints pin every
# package to the version production runs.
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG TARGETARCH
COPY pyproject.toml /tmp/pyproject.toml
COPY deploy/docker/constraints.txt /tmp/constraints.txt
# arm64 pins onnxruntime 1.27.0: 1.28.0 and later segfault on `import` under
# qemu arm64 emulation (bisected 2026-11-18: 1.20-1.27 load, 1.28-1.32 crash),
# so 1.27.0 is the newest version the arm64 image could be tested end to end
# with before shipping. It also backs markitdown (via magika) — a crash there
# would take down Office parsing, not just seal/signature detection.
RUN if [ "$TARGETARCH" = arm64 ]; then \
        sed -i 's/^onnxruntime==.*/onnxruntime==1.27.0/' /tmp/constraints.txt; fi
RUN python - <<'PY' > /tmp/requirements.txt
import tomllib
p = tomllib.load(open("/tmp/pyproject.toml", "rb"))["project"]
reqs = list(p["dependencies"])
for extra in ("parsers", "output", "vision"):
    reqs += p["optional-dependencies"][extra]
print("\n".join(reqs))
PY
RUN pip install --no-cache-dir --retries 10 --timeout 120 --index-url "$PIP_INDEX_URL" \
        -r /tmp/requirements.txt -c /tmp/constraints.txt

# Detection weights are not in git (612 MB); they come in as a named build
# context and live outside the data volume so upgrades replace them.
COPY --from=models PP-DocLayoutV3.onnx yolos-signature.onnx /opt/idp/models/

COPY alembic.ini ./
COPY alembic/ alembic/
COPY app/ app/
# Shipped configs are only defaults: the entrypoint renders the live
# configs/*.yaml into the data volume from idp.env (render_configs.py).
COPY configs/ configs.default/
COPY deploy/docker/entrypoint.sh deploy/docker/render_configs.py deploy/docker/check_models.py /app/deploy/
RUN ln -s /srv/idp/configs /app/configs \
    && chmod 755 /app/deploy/entrypoint.sh \
    && python -m compileall -q app

# The container runs as the host's ops user (idp.sh passes --user), so
# nothing below may assume root: HOME and temp go to /tmp, data to /srv/idp.
ENV PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    TZ=Asia/Shanghai \
    IDP_PORT=8080 \
    IDP_DATA_DIR=/srv/idp/data \
    IDP_DATABASE_URL=sqlite+aiosqlite:////srv/idp/data/idp.db \
    IDP_DEPLOY_TIER=lite \
    IDP_AUTH_MODE=on \
    IDP_LICENSE_REQUIRED=true \
    IDP_SEAL_MODEL_PATH=/opt/idp/models/PP-DocLayoutV3.onnx \
    IDP_SIGNATURE_MODEL_PATH=/opt/idp/models/yolos-signature.onnx

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('IDP_PORT','8080'),timeout=4)"
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
