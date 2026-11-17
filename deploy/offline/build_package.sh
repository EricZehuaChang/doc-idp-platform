#!/bin/bash
# Assemble the offline deploy package on a build machine that has Docker
# buildx and internet access: platform image + static Docker engine + the
# on-site scripts, as one tar.gz the customer host can install without any
# network (BOC POC 2026-11; customer host = Kylin V10 SP2 on ARM).
#
#   deploy/offline/build_package.sh arm64|amd64 <dir holding the two .onnx weights>
#
# Output: deploy/offline/out/idp-offline-<arch>-<version>.tar.gz and .sha256
set -euo pipefail

ARCH="${1:?第一个参数：arm64 或 amd64}"
MODELS="${2:?第二个参数：检测模型目录（含 PP-DocLayoutV3.onnx 和 yolos-signature.onnx）}"
DOCKER_VERSION="${DOCKER_VERSION:-29.10.0}"   # supports cgroup v1 (Kylin V10) until May 2029
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
case "$ARCH" in arm64) DARCH=aarch64 ;; amd64) DARCH=x86_64 ;; *) echo "ARCH 只能是 arm64 或 amd64" >&2; exit 2 ;; esac
for f in PP-DocLayoutV3.onnx yolos-signature.onnx; do
    [ -f "$MODELS/$f" ] || { echo "缺少模型文件 $MODELS/$f" >&2; exit 2; }
done

commit="$(git -C "$REPO" rev-parse --short HEAD)"
git -C "$REPO" diff --quiet HEAD -- app alembic configs deploy || commit="$commit-dirty"
VERSION="$(date +%Y%m%d)-$commit"
IMAGE="idp-platform:$VERSION-$ARCH"
OUT="$REPO/deploy/offline/out"
PKG="$OUT/idp"
rm -rf "$PKG"
mkdir -p "$PKG/images" "$PKG/docker"

echo "==> 构建镜像 $IMAGE（$ARCH）"
docker buildx build --platform "linux/$ARCH" --build-context models="$MODELS" \
    -f "$REPO/deploy/docker/Dockerfile" -t "$IMAGE" --load "$REPO"

echo "==> 导出镜像"
docker save "$IMAGE" | gzip > "$PKG/images/idp-image.tar.gz"

echo "==> 下载 Docker $DOCKER_VERSION 静态包（$DARCH）"
curl -fL --retry 3 -o "$PKG/docker/docker-$DOCKER_VERSION.tgz" \
    "https://download.docker.com/linux/static/stable/$DARCH/docker-$DOCKER_VERSION.tgz"

echo "==> 组装"
cp "$REPO/deploy/offline/install.sh" "$REPO/deploy/offline/idp.sh" \
   "$REPO/deploy/offline/idp.env.example" "$PKG/"
cp "$REPO/deploy/offline/docker/daemon.json" "$REPO/deploy/offline/docker/docker.service" "$PKG/docker/"
printf 'VERSION=%s\nARCH=%s\nIMAGE=%s\nDOCKER_VERSION=%s\n' \
    "$VERSION" "$ARCH" "$IMAGE" "$DOCKER_VERSION" > "$PKG/VERSION"
# whatever the checkout's line endings, the host gets LF
sed -i 's/\r$//' "$PKG"/*.sh "$PKG/idp.env.example" "$PKG/VERSION" "$PKG"/docker/daemon.json "$PKG"/docker/docker.service
chmod 755 "$PKG"/*.sh
(cd "$PKG" && sha256sum images/* docker/* > SHA256SUMS)

tarball="$OUT/idp-offline-$ARCH-$VERSION.tar.gz"
tar --owner=0 --group=0 -czf "$tarball" -C "$OUT" idp
(cd "$OUT" && sha256sum "$(basename "$tarball")" > "$(basename "$tarball").sha256")
echo "==> 完成：$tarball（$(du -h "$tarball" | cut -f1)）"
