#!/usr/bin/env bash
# PrismaticaAPI 生产更新脚本：安全拉取代码、备份数据库、执行迁移并重启后端。

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ -d "${SCRIPT_DIR}/../.git" ]]; then
    DEFAULT_REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
else
    DEFAULT_REPO_DIR="/opt/prismatica/PrismaticaAPI"
fi

REPO_URL="${REPO_URL:-https://github.com/HexaLuminaStudio/Prismatica-API.git}"
REPO_DIR="${REPO_DIR:-${DEFAULT_REPO_DIR}}"
REMOTE_NAME="${REMOTE_NAME:-origin}"
BRANCH="${BRANCH:-main}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://127.0.0.1:8000}"
BACKUP_DIR="${BACKUP_DIR:-${HOME}/backups/prismatica-api}"
STATE_DIR="${STATE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/prismatica-api}"
MYSQL_BACKUP_IMAGE="${MYSQL_BACKUP_IMAGE:-mysql:8.4}"
SKIP_DB_BACKUP="${SKIP_DB_BACKUP:-0}"
SKIP_PUBLIC_PROBES="${SKIP_PUBLIC_PROBES:-0}"
REQUIRE_PROD_ENV="${REQUIRE_PROD_ENV:-1}"
PULL_BASE_IMAGES="${PULL_BASE_IMAGES:-0}"

DEPLOY_TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
PREVIOUS_COMMIT="unknown"
TARGET_COMMIT="unknown"
BACKUP_PATH="none"

log() {
    printf '[%s] %s\n' "$(date '+%F %T')" "$*"
}

usage() {
    cat <<'EOF'
用法：./deploy/update_and_restart.sh

常用环境变量：
  REPO_DIR=/opt/prismatica/PrismaticaAPI
  BRANCH=main
  PUBLIC_BASE_URL=http://127.0.0.1:8000
  BACKUP_DIR=/安全备份目录/prismatica-api
  SKIP_DB_BACKUP=1        # 仅首次空库部署
  SKIP_PUBLIC_PROBES=1    # 仅无 Nginx 的测试服务器
  PULL_BASE_IMAGES=1      # 同时刷新 Python/MySQL 基础镜像
EOF
}

fail() {
    log "失败：$*"
    exit 1
}

requireCommand() {
    command -v "$1" >/dev/null 2>&1 || fail "缺少命令：$1"
}

envValue() {
    local key="$1"
    local envFile="${REPO_DIR}/.env"
    awk -F= -v target="${key}" '
        $0 !~ /^[[:space:]]*#/ && $1 == target {
            sub(/^[^=]*=/, "")
            value = $0
        }
        END { print value }
    ' "${envFile}"
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
    log "部署在第 ${lineNumber} 行中断，退出码 ${exitCode}。"
    log "切换前提交：${PREVIOUS_COMMIT}；目标提交：${TARGET_COMMIT}；数据库备份：${BACKUP_PATH}"
    showDiagnostics
    exit "${exitCode}"
}

trap 'onError "$?" "$LINENO"' ERR

validateInputs() {
    [[ "${BRANCH}" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "BRANCH 包含不允许的字符"
    [[ "${REMOTE_NAME}" =~ ^[A-Za-z0-9._-]+$ ]] || fail "REMOTE_NAME 包含不允许的字符"
    [[ "${PUBLIC_BASE_URL}" =~ ^https?:// ]] || fail "PUBLIC_BASE_URL 必须以 http:// 或 https:// 开头"
}

ensureRepository() {
    if [[ ! -d "${REPO_DIR}/.git" ]]; then
        log "仓库不存在，开始首次克隆：${REPO_DIR}"
        mkdir -p -- "$(dirname -- "${REPO_DIR}")"
        git clone --branch "${BRANCH}" --single-branch -- "${REPO_URL}" "${REPO_DIR}"
    fi
    cd -- "${REPO_DIR}"

    [[ -f docker-compose.yml ]] || fail "${REPO_DIR} 不是 PrismaticaAPI 部署目录"
    [[ -f .env ]] || fail "缺少 ${REPO_DIR}/.env；请先根据 .env.example 配置生产环境"

    local currentBranch
    currentBranch="$(git branch --show-current)"
    [[ "${currentBranch}" == "${BRANCH}" ]] || fail "当前分支为 ${currentBranch:-detached}，期望 ${BRANCH}"
    git diff --quiet || fail "工作区存在未提交的已跟踪文件修改，请先处理后再部署"
    git diff --cached --quiet || fail "暂存区存在未提交修改，请先处理后再部署"
}

validateProductionEnv() {
    local envMode
    envMode="$(envValue ENV)"
    if [[ "${envMode}" != "prod" ]]; then
        if [[ "${REQUIRE_PROD_ENV}" == "1" ]]; then
            fail ".env 中 ENV=${envMode:-未设置}；生产部署必须设为 ENV=prod"
        fi
        log "警告：当前 ENV=${envMode:-未设置}，已按 REQUIRE_PROD_ENV=0 继续"
    fi

    local envModeBits
    envModeBits="$(stat -c '%a' .env 2>/dev/null || true)"
    if [[ -n "${envModeBits}" && "${envModeBits}" != "600" && "${envModeBits}" != "640" ]]; then
        log "警告：.env 权限为 ${envModeBits}，建议执行 chmod 600 .env"
    fi
}

fetchAndVerify() {
    PREVIOUS_COMMIT="$(git rev-parse HEAD)"
    log "只读拉取远端引用：${REMOTE_NAME}/${BRANCH}"
    git fetch --prune -- "${REMOTE_NAME}" "${BRANCH}"
    TARGET_COMMIT="$(git rev-parse "${REMOTE_NAME}/${BRANCH}")"
    git merge-base --is-ancestor "${PREVIOUS_COMMIT}" "${TARGET_COMMIT}" \
        || fail "本地提交无法快进到远端；脚本不会自动 reset 或覆盖服务器文件"
    log "准备更新：${PREVIOUS_COMMIT:0:12} -> ${TARGET_COMMIT:0:12}"
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

validateCurrentRuntime() {
    docker compose version >/dev/null
    docker compose config --quiet

    local nginxPath
    nginxPath="$(findNginx || true)"
    if [[ -n "${nginxPath}" ]]; then
        log "检查当前 Nginx 配置"
        "${nginxPath}" -t
    else
        log "警告：未找到 Nginx，可通过 NGINX_BIN 指定；将继续检查后端内部端口"
    fi
}

backupDatabase() {
    if [[ "${SKIP_DB_BACKUP}" == "1" ]]; then
        log "已按 SKIP_DB_BACKUP=1 跳过数据库备份"
        return
    fi

    requireCommand gzip
    mkdir -p -- "${BACKUP_DIR}"
    chmod 700 -- "${BACKUP_DIR}" 2>/dev/null || true
    local tempBackup="${BACKUP_DIR}/.prismatica-${DEPLOY_TIMESTAMP}.sql.gz.tmp"
    BACKUP_PATH="${BACKUP_DIR}/prismatica-${DEPLOY_TIMESTAMP}-${PREVIOUS_COMMIT:0:12}.sql.gz"

    log "创建迁移前一致性数据库备份：${BACKUP_PATH}"
    docker run --rm \
        --add-host host.docker.internal:host-gateway \
        --env-file "${REPO_DIR}/.env" \
        "${MYSQL_BACKUP_IMAGE}" \
        sh -ec '
            export MYSQL_PWD="${DB_PASSWORD:?缺少 DB_PASSWORD}"
            exec mysqldump \
                --host="${DOCKER_DB_HOST:-host.docker.internal}" \
                --port="${DB_PORT:-3306}" \
                --user="${DB_USER:?缺少 DB_USER}" \
                --single-transaction \
                --quick \
                --triggers \
                --hex-blob \
                --no-tablespaces \
                --set-gtid-purged=OFF \
                "${DB_NAME:?缺少 DB_NAME}"
        ' | gzip -9 >"${tempBackup}"
    [[ -s "${tempBackup}" ]] || fail "数据库备份为空"
    chmod 600 -- "${tempBackup}"
    mv -- "${tempBackup}" "${BACKUP_PATH}"
}

updateRepository() {
    if [[ "${PREVIOUS_COMMIT}" == "${TARGET_COMMIT}" ]]; then
        log "代码已经是远端最新版本；仍会重建并重启后端"
        return
    fi
    log "快进更新工作区"
    git merge --ff-only "${REMOTE_NAME}/${BRANCH}"
    [[ "$(git rev-parse HEAD)" == "${TARGET_COMMIT}" ]] || fail "更新后提交与目标不一致"
}

validateNewCompose() {
    export BUILD_ID="${DEPLOY_BUILD_ID:-deploy-${DEPLOY_TIMESTAMP}}"
    export GIT_COMMIT="${TARGET_COMMIT}"
    docker compose config --quiet

    local services
    services="$(docker compose config --services)"
    local requiredService
    for requiredService in api ai resources maintenance redis; do
        grep -Fxq "${requiredService}" <<<"${services}" || fail "Compose 缺少服务：${requiredService}"
    done
}

buildImages() {
    local buildArgs=(build api ai resources maintenance)
    if [[ "${PULL_BASE_IMAGES}" == "1" ]]; then
        buildArgs=(build --pull api ai resources maintenance)
    fi
    log "构建四个后端服务镜像"
    docker compose "${buildArgs[@]}"
}

runMigrations() {
    local migrations=(
        scripts.migrate_account_billing
        scripts.migrate_auth_version
        scripts.migrate_dynamic_pricing
        scripts.migrate_corpus_download_pricing
        scripts.migrate_affordable_ai_pricing
    )
    local migrationModule
    for migrationModule in "${migrations[@]}"; do
        log "执行幂等迁移：${migrationModule}"
        docker compose run --rm --no-deps api python -m "${migrationModule}"
    done
    log "执行数据库结构预检"
    docker compose run --rm --no-deps api python -m scripts.db_preflight
}

containerState() {
    docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null
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

restartBackend() {
    log "确保 Redis 正常运行（不会强制重启）"
    docker compose up -d redis
    waitForHealthy prismatica-redis

    log "重建并切换 API、AI、资源和维护服务"
    docker compose up -d --no-deps --force-recreate --remove-orphans api ai resources maintenance
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
    probeUpstreamHeader GET /v1/resources/download/__deployment_probe__ resources
}

recordSuccess() {
    mkdir -p -- "${STATE_DIR}"
    chmod 700 -- "${STATE_DIR}" 2>/dev/null || true
    printf '%s\n' "${PREVIOUS_COMMIT}" >"${STATE_DIR}/previous-commit"
    printf '%s\n' "${TARGET_COMMIT}" >"${STATE_DIR}/last-successful-commit"
    printf '%s\n' "${BACKUP_PATH}" >"${STATE_DIR}/last-backup"
    chmod 600 -- "${STATE_DIR}"/* 2>/dev/null || true
}

main() {
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
        usage
        return
    fi
    [[ $# -eq 0 ]] || fail "不接受位置参数；请使用 --help 查看环境变量"
    validateInputs
    requireCommand git
    requireCommand docker
    requireCommand curl
    requireCommand awk
    requireCommand grep
    requireCommand flock

    mkdir -p -- "${STATE_DIR}"
    exec 9>"${STATE_DIR}/deploy.lock"
    flock -n 9 || fail "已有另一个部署任务正在执行"

    ensureRepository
    validateProductionEnv
    fetchAndVerify
    validateCurrentRuntime
    backupDatabase
    updateRepository
    validateNewCompose
    buildImages
    runMigrations
    restartBackend
    probeInternalRoutes
    probePublicRoutes
    recordSuccess

    trap - ERR
    log "部署完成：${TARGET_COMMIT:0:12}；数据库备份：${BACKUP_PATH}"
    log "请继续使用真实账号检查登录、余额、流水、Admin、AI 流式响应和资源下载。"
}

main "$@"
