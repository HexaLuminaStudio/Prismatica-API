#!/usr/bin/env bash
# PrismaticaAPI 纯重启脚本：不拉取、不构建、不迁移，只重启现有后端容器。

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_DIR="${REPO_DIR:-$(cd -- "${SCRIPT_DIR}/.." && pwd -P)}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://127.0.0.1:8000}"
STATE_DIR="${STATE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/prismatica-api}"
SKIP_PUBLIC_PROBES="${SKIP_PUBLIC_PROBES:-0}"
REQUIRE_PROD_ENV="${REQUIRE_PROD_ENV:-1}"

log() {
    printf '[%s] %s\n' "$(date '+%F %T')" "$*"
}

fail() {
    log "失败：$*"
    exit 1
}

usage() {
    cat <<'EOF'
用法：./deploy/restart_backend.sh

本脚本不会拉取代码、构建镜像、执行迁移或备份数据库，只重启现有的：
  api、ai、resources、maintenance

常用环境变量：
  REPO_DIR=/opt/prismatica/PrismaticaAPI
  PUBLIC_BASE_URL=http://127.0.0.1:8000
  SKIP_PUBLIC_PROBES=1    # 仅无 Nginx 的测试服务器
EOF
}

requireCommand() {
    command -v "$1" >/dev/null 2>&1 || fail "缺少命令：$1"
}

envValue() {
    local key="$1"
    awk -F= -v target="${key}" '
        $0 !~ /^[[:space:]]*#/ && $1 == target {
            sub(/^[^=]*=/, "")
            value = $0
        }
        END { print value }
    ' "${REPO_DIR}/.env"
}

showDiagnostics() {
    if [[ -f "${REPO_DIR}/docker-compose.yml" ]]; then
        (
            cd -- "${REPO_DIR}"
            docker compose ps || true
            docker compose logs --tail=80 api ai resources maintenance || true
        )
    fi
}

onError() {
    local exitCode="$1"
    local lineNumber="$2"
    log "重启在第 ${lineNumber} 行中断，退出码 ${exitCode}。"
    showDiagnostics
    exit "${exitCode}"
}

trap 'onError "$?" "$LINENO"' ERR

validateEnvironment() {
    [[ "${PUBLIC_BASE_URL}" =~ ^https?:// ]] || fail "PUBLIC_BASE_URL 必须以 http:// 或 https:// 开头"
    [[ -d "${REPO_DIR}" ]] || fail "后端目录不存在：${REPO_DIR}"
    cd -- "${REPO_DIR}"
    [[ -f docker-compose.yml ]] || fail "缺少 docker-compose.yml"
    [[ -f .env ]] || fail "缺少 .env"

    local envMode
    envMode="$(envValue ENV)"
    if [[ "${envMode}" != "prod" ]]; then
        if [[ "${REQUIRE_PROD_ENV}" == "1" ]]; then
            fail ".env 中 ENV=${envMode:-未设置}；生产重启要求 ENV=prod"
        fi
        log "警告：当前 ENV=${envMode:-未设置}，已按 REQUIRE_PROD_ENV=0 继续"
    fi

    docker compose version >/dev/null
    docker compose config --quiet
    local services
    services="$(docker compose config --services)"
    local requiredService
    for requiredService in api ai resources maintenance redis; do
        grep -Fxq "${requiredService}" <<<"${services}" || fail "Compose 缺少服务：${requiredService}"
    done
}

findNginx() {
    if [[ -n "${NGINX_BIN:-}" && -x "${NGINX_BIN}" ]]; then
        printf '%s\n' "${NGINX_BIN}"
    elif command -v nginx >/dev/null 2>&1; then
        command -v nginx
    elif [[ -x /www/server/nginx/sbin/nginx ]]; then
        printf '%s\n' /www/server/nginx/sbin/nginx
    fi
}

validateNginx() {
    local nginxPath
    nginxPath="$(findNginx || true)"
    if [[ -n "${nginxPath}" ]]; then
        log "检查当前 Nginx 配置"
        "${nginxPath}" -t
    else
        log "警告：未找到 Nginx，可通过 NGINX_BIN 指定"
    fi
}

containerState() {
    docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null
}

requireExistingContainers() {
    local containerName
    for containerName in \
        prismatica-api-fast \
        prismatica-api-ai \
        prismatica-api-resources \
        prismatica-api-maintenance; do
        docker inspect "${containerName}" >/dev/null 2>&1 \
            || fail "容器 ${containerName} 不存在；首次启动请使用 update_and_restart.sh"
    done
}

waitForHealthy() {
    local containerName="$1"
    local deadline=$((SECONDS + 120))
    local state
    while ((SECONDS < deadline)); do
        state="$(containerState "${containerName}" || true)"
        if [[ "${state}" == "running healthy" ]]; then
            log "容器已健康：${containerName}"
            return
        fi
        if [[ "${state}" == exited* || "${state}" == dead* ]]; then
            fail "容器异常退出：${containerName}（${state}）"
        fi
        sleep 3
    done
    fail "等待容器健康超时：${containerName}（${state:-不存在}）"
}

waitForRunning() {
    local containerName="$1"
    local deadline=$((SECONDS + 60))
    local state
    while ((SECONDS < deadline)); do
        state="$(containerState "${containerName}" || true)"
        if [[ "${state}" == running* ]]; then
            log "容器正在运行：${containerName}"
            return
        fi
        if [[ "${state}" == exited* || "${state}" == dead* ]]; then
            fail "容器异常退出：${containerName}（${state}）"
        fi
        sleep 2
    done
    fail "等待容器启动超时：${containerName}（${state:-不存在}）"
}

restartExistingBackend() {
    log "仅重启现有后端容器；Redis、Nginx 和 MySQL 不会重启"
    docker compose restart api ai resources maintenance
    waitForHealthy prismatica-api-fast
    waitForHealthy prismatica-api-ai
    waitForHealthy prismatica-api-resources
    waitForRunning prismatica-api-maintenance
}

probeInternalRoutes() {
    log "检查三个内部服务"
    curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8100/healthz >/dev/null
    curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8101/healthz >/dev/null
    curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8102/healthz >/dev/null
    curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8100/openapi.json >/dev/null
    curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8100/v1/pricing/catalog >/dev/null
}

probeUpstreamHeader() {
    local method="$1"
    local path="$2"
    local expected="$3"
    local headerFile
    headerFile="$(mktemp)"
    if [[ "${method}" == "POST" ]]; then
        curl --silent --show-error --max-time 15 \
            --request POST --header 'Content-Type: application/json' --data '{}' \
            --dump-header "${headerFile}" --output /dev/null \
            "${PUBLIC_BASE_URL}${path}"
    else
        curl --silent --show-error --max-time 15 \
            --request GET --dump-header "${headerFile}" --output /dev/null \
            "${PUBLIC_BASE_URL}${path}"
    fi
    if ! grep -Eiq "^X-Prismatica-Upstream:[[:space:]]*${expected}[[:space:]]*\r?$" "${headerFile}"; then
        rm -f -- "${headerFile}"
        fail "公网路由 ${path} 未命中预期上游 ${expected}"
    fi
    rm -f -- "${headerFile}"
}

probePublicRoutes() {
    if [[ "${SKIP_PUBLIC_PROBES}" == "1" ]]; then
        log "已按 SKIP_PUBLIC_PROBES=1 跳过公网入口检查"
        return
    fi
    log "检查公网入口与 Nginx 分流：${PUBLIC_BASE_URL}"
    curl --fail --silent --show-error --max-time 15 "${PUBLIC_BASE_URL}/healthz" >/dev/null
    curl --fail --silent --show-error --max-time 15 "${PUBLIC_BASE_URL}/v1/pricing/catalog" >/dev/null
    probeUpstreamHeader GET /healthz fast-api
    probeUpstreamHeader POST /v1/ai/chat ai
    probeUpstreamHeader GET /v1/resources/download/__restart_probe__ resources
}

main() {
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
        usage
        return
    fi
    [[ $# -eq 0 ]] || fail "不接受位置参数；请使用 --help 查看环境变量"
    requireCommand docker
    requireCommand curl
    requireCommand awk
    requireCommand grep
    requireCommand flock
    requireCommand mktemp

    mkdir -p -- "${STATE_DIR}"
    exec 9>"${STATE_DIR}/deploy.lock"
    flock -n 9 || fail "已有更新或重启任务正在执行"

    validateEnvironment
    validateNginx
    requireExistingContainers
    restartExistingBackend
    probeInternalRoutes
    probePublicRoutes

    trap - ERR
    log "纯重启完成。请继续使用真实账号检查登录、余额、流水、Admin、AI 和资源下载。"
    log "提示：本脚本不会应用本地源码或 Compose 改动；需要更新版本时请使用 update_and_restart.sh。"
}

main "$@"
