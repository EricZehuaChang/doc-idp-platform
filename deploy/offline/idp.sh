#!/bin/bash
# Day-to-day operations for the offline IDP deployment. Runs as the ops user
# (member of the docker group) — never needs root.
#
#   ./idp.sh start            start (or re-create after editing idp.env)
#   ./idp.sh stop             stop the service
#   ./idp.sh restart          same as start
#   ./idp.sh status           container, health and license state
#   ./idp.sh logs             follow the service log (Ctrl+C to quit)
#   ./idp.sh check            test the connection to the model services
#   ./idp.sh license <file>   install a license file (opens the service)
#   ./idp.sh suspend          lock the service immediately (needs a new license to reopen)
#   ./idp.sh load <file>      load an updated image package, then run start
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NAME=idp
ENV_FILE="$HERE/idp.env"
SRV="$HERE/srv"                       # mounted at /srv/idp: data + rendered configs
# shellcheck source=/dev/null
. "$HERE/VERSION"                     # VERSION, ARCH, IMAGE, DOCKER_VERSION

die() { echo "[失败] $*" >&2; exit 1; }

need_docker() {
    docker info >/dev/null 2>&1 || die "连不上 Docker。确认已用 root 执行过 install.sh，并且 $(whoami) 退出后重新登录过（docker 组才生效）"
}

load_env() {
    [ -f "$ENV_FILE" ] || die "缺少 $ENV_FILE：执行 cp idp.env.example idp.env 后按手册填写"
    sed -i 's/\r$//' "$ENV_FILE"       # a file edited on Windows carries CR line ends
    PORT="$(sed -n 's/^IDP_PORT=//p' "$ENV_FILE" | tail -1)"
    PORT="${PORT:-8080}"
}

running() { [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || echo false)" = true ]; }

# Run a python module in the platform: inside the live container when it is
# up, otherwise in a throwaway container on the same data (so a license can be
# installed before the first start).
in_app() {
    if running; then
        docker exec "$NAME" python "$@"
    else
        docker run --rm --user "$(id -u):$(id -g)" --env-file "$ENV_FILE" \
            -v "$SRV:/srv/idp" --entrypoint python "$IMAGE" "$@"
    fi
}

probe() { docker exec "$NAME" python -c \
    "import urllib.request;urllib.request.urlopen('http://127.0.0.1:$PORT/healthz',timeout=3)" \
    >/dev/null 2>&1; }

cmd_start() {
    need_docker; load_env
    grep -q '^IDP_ADMIN_PASSWORD=.\+' "$ENV_FILE" || die "idp.env 里 IDP_ADMIN_PASSWORD 不能为空"
    docker image inspect "$IMAGE" >/dev/null 2>&1 || docker load -i "$HERE/images/idp-image.tar.gz"
    mkdir -p "$SRV"
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker run -d --name "$NAME" --restart unless-stopped --network host \
        --user "$(id -u):$(id -g)" --env-file "$ENV_FILE" -v "$SRV:/srv/idp" \
        --log-opt max-size=50m --log-opt max-file=5 "$IMAGE" >/dev/null
    echo "启动中，等待服务就绪（首次启动约 30 秒）..."
    for _ in $(seq 1 90); do
        if ! running; then
            docker logs --tail 40 "$NAME" 2>&1 || true
            die "服务启动后退出了，以上是最后的日志"
        fi
        if probe; then
            echo "服务已启动：http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT"
            in_app -m app.license status || true
            return 0
        fi
        sleep 2
    done
    die "3 分钟内没有就绪，执行 ./idp.sh logs 查看原因"
}

cmd_stop() {
    need_docker
    docker stop -t 30 "$NAME" >/dev/null 2>&1 && echo "服务已停止" || echo "服务本来就没有运行"
}

cmd_status() {
    need_docker; load_env
    echo "版本：$VERSION（$ARCH）  镜像：$IMAGE"
    if running; then
        if probe; then echo "服务：运行中，健康"; else echo "服务：运行中，但健康检查未通过"; fi
    else
        echo "服务：未运行"
    fi
    in_app -m app.license status || true
}

cmd_logs() { need_docker; docker logs --tail 200 -f "$NAME"; }

cmd_check() {
    need_docker; load_env
    running || die "服务没有运行，先执行 ./idp.sh start"
    docker exec "$NAME" python /app/deploy/check_models.py
}

cmd_license() {
    need_docker; load_env
    [ -n "${1:-}" ] && [ -f "$1" ] || die "用法：./idp.sh license 授权文件路径"
    mkdir -p "$SRV"
    cp "$1" "$SRV/.incoming-license.json"
    status=0
    in_app -m app.license install /srv/idp/.incoming-license.json || status=$?
    rm -f "$SRV/.incoming-license.json"
    return $status
}

cmd_suspend() {
    need_docker; load_env
    read -r -p "暂停后所有用户立即无法访问，只有装入新授权文件才能恢复。确认暂停请输入 yes：" answer
    [ "$answer" = yes ] || { echo "已取消"; return 0; }
    in_app -m app.license suspend
}

cmd_load() {
    need_docker
    [ -n "${1:-}" ] && [ -f "$1" ] || die "用法：./idp.sh load 镜像包路径（.tar.gz）"
    docker load -i "$1" | tail -1
    echo "镜像已导入。如镜像名有变化，请把新包的 VERSION 文件覆盖到 $HERE 后再执行 ./idp.sh start"
}

case "${1:-}" in
    start|restart) cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    logs)    cmd_logs ;;
    check)   cmd_check ;;
    license) cmd_license "${2:-}" ;;
    suspend) cmd_suspend ;;
    load)    cmd_load "${2:-}" ;;
    *) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
