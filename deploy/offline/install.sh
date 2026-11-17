#!/bin/bash
# One-time host preparation for the offline IDP package — the ONLY step that
# needs root. Customer rule (BOC 2026-10-30): root is for deployment only,
# daily operations must run as the ops user without root, and /etc/sudoers
# must not be modified. So this script:
#   1. installs the bundled static Docker engine (skipped if the host already
#      has a working one), with Docker data on the data disk and Docker kept
#      away from the host firewall (no iptables rules, no bridge: the
#      platform container uses host networking);
#   2. puts the ops user into the docker group (no sudoers change);
#   3. opens the platform port if firewalld is running;
#   4. loads the platform image and hands the package directory to the ops user.
#
# Usage (as root, from the unpacked package directory):  bash install.sh
# Options via env: APP_USER (default appadmin), DOCKER_ROOT (default /data/docker)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP_USER="${APP_USER:-appadmin}"
DOCKER_ROOT="${DOCKER_ROOT:-/data/docker}"
# shellcheck source=/dev/null
. "$HERE/VERSION"                     # VERSION, ARCH, IMAGE, DOCKER_VERSION

say()  { echo -e "\n==> $*"; }
warn() { echo "  [注意] $*"; }
die()  { echo "  [失败] $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "需要 root 权限，请执行：sudo bash install.sh"
id "$APP_USER" >/dev/null 2>&1 || die "找不到运维用户 $APP_USER（可用 APP_USER=用户名 bash install.sh 指定）"
case "$ARCH" in arm64) want=aarch64 ;; amd64) want=x86_64 ;; *) die "VERSION 里的 ARCH 无效：$ARCH" ;; esac
[ "$(uname -m)" = "$want" ] || die "这是 $ARCH 安装包，本机是 $(uname -m)，请换对应架构的安装包"

say "1/5 Docker 引擎"
if docker info >/dev/null 2>&1; then
    echo "  本机已有可用的 Docker（$(docker version -f '{{.Server.Version}}')），跳过安装"
elif systemctl cat docker.service >/dev/null 2>&1 && systemctl enable --now docker >/dev/null 2>&1 \
        && sleep 3 && docker info >/dev/null 2>&1; then
    echo "  已启动本机原有的 Docker（$(docker version -f '{{.Server.Version}}')）"
else
    tmp="$(mktemp -d)"
    tar xzf "$HERE"/docker/docker-*.tgz -C "$tmp"
    install -m 755 "$tmp"/docker/* /usr/local/bin/
    rm -rf "$tmp"
    mkdir -p /etc/docker "$DOCKER_ROOT"
    if [ -f /etc/docker/daemon.json ]; then
        warn "/etc/docker/daemon.json 已存在，保留原文件未覆盖"
    else
        sed "s#__DOCKER_ROOT__#$DOCKER_ROOT#" "$HERE/docker/daemon.json" > /etc/docker/daemon.json
    fi
    install -m 644 "$HERE/docker/docker.service" /etc/systemd/system/docker.service
    groupadd -f docker
    systemctl daemon-reload
    systemctl enable --now docker
    for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
    docker info >/dev/null 2>&1 || die "Docker 启动失败，查看原因：journalctl -u docker --no-pager | tail -50"
    echo "  已安装 Docker $DOCKER_VERSION，数据目录 $DOCKER_ROOT，开机自启"
fi

say "2/5 运维用户 $APP_USER 加入 docker 组（不修改 sudoers）"
groupadd -f docker
usermod -aG docker "$APP_USER"
echo "  完成。$APP_USER 需要退出后重新登录才生效"

say "3/5 目录与配置"
mkdir -p "$HERE/srv"
if [ ! -f "$HERE/idp.env" ]; then
    cp "$HERE/idp.env.example" "$HERE/idp.env"
    echo "  已生成 $HERE/idp.env（按手册填写模型地址和管理员密码）"
fi
chmod 600 "$HERE/idp.env"
chmod 755 "$HERE/idp.sh"
chown -R "$APP_USER": "$HERE"
echo "  安装目录 $HERE 已交给 $APP_USER"

say "4/5 防火墙端口"
PORT="$(sed -n 's/^IDP_PORT=//p' "$HERE/idp.env" | tr -d '\r' | tail -1)"; PORT="${PORT:-8080}"
if systemctl is-active firewalld >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null && firewall-cmd --reload >/dev/null
    echo "  firewalld 已放行 $PORT/tcp"
else
    echo "  firewalld 未运行，无需放行（网络层访问由行内网络组开通）"
fi

say "5/5 导入平台镜像（约 1～3 分钟）"
docker load -i "$HERE/images/idp-image.tar.gz" | tail -1

say "环境自检"
echo "  内核：$(uname -r)   架构：$(uname -m)"
echo "  Docker 存储驱动：$(docker info -f '{{.Driver}}')   数据目录：$(docker info -f '{{.DockerRootDir}}')"
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = "Enforcing" ]; then
    warn "SELinux 为 Enforcing；若平台启动后报目录无权限，见手册的常见问题一节"
fi
df -h "$DOCKER_ROOT" "$HERE" | sed 's/^/  /'

cat <<EOF

安装完成。接下来请用 $APP_USER 操作（不再需要 root）：
  1. 退出当前登录，重新以 $APP_USER 登录
  2. cd $HERE && vi idp.env      # 填写模型服务地址、管理员密码
  3. ./idp.sh start
EOF
