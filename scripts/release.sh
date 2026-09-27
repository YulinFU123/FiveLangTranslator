#!/usr/bin/env bash
#
# P8 - 标准化版本发布流程（原子化，失败全链路回滚）
#
# 用法: ./scripts/release.sh <version> [options]
#
#   <version>     目标版本号，必填，如 v1.0.0 / v1.1.0-beta.1
#   --dry-run     试运行：仅输出流程预览与变更日志，不执行任何实际修改
#   --full-test   执行全量回归（默认仅执行 quick 快速门禁）
#   --push        发布成功后推送标签至远程 origin（默认仅本地发布）
#   --force       跳过工作区干净检查，强制发布（不推荐）
#   --bump        同步版本号到版本文件（默认不同步，避免与标签漂移）
#   -h, --help    显示帮助
#
set -Eeuo pipefail

# ============================== 配置区 ==============================
RELEASE_BRANCH="${RELEASE_BRANCH:-master}"          # 允许发布的分支
CHANGELOG_FILE="${CHANGELOG_FILE:-CHANGELOG.md}"    # 变更日志文件名
VERSION_FILE="${VERSION_FILE:-pyproject.toml}"      # 版本号元数据文件（--bump 时同步）
REGRESSION_SCRIPT="${REGRESSION_SCRIPT:-./tests/run_regression.sh}"
REMOTE_NAME="${REMOTE_NAME:-origin}"                # 推送远程仓库名
COMMIT_MSG_PREFIX="chore(release):"                 # 发布提交信息前缀
DEFAULT_PUSH=false                                  # 默认仅本地发布
# ====================================================================

# ------------------------------ 日志 ------------------------------
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_INFO=$'\033[36m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_OK=$'\033[32m'; C_RST=$'\033[0m'
else
  C_INFO=""; C_WARN=""; C_ERR=""; C_OK=""; C_RST=""
fi
log_info() { printf '%s[INFO]%s %s\n' "$C_INFO" "$C_RST" "$*"; }
log_warn() { printf '%s[WARN]%s %s\n' "$C_WARN" "$C_RST" "$*"; }
log_err()  { printf '%s[ERROR]%s %s\n' "$C_ERR" "$C_RST" "$*" >&2; }
log_ok()   { printf '%s[ OK ]%s %s\n' "$C_OK" "$C_RST" "$*"; }
stage()    { printf '\n%s==> %s%s\n' "$C_INFO" "$*" "$C_RST"; }

# 语义化版本 -> PEP 440（pyproject.toml 使用）
# v1.0.0        -> 1.0.0
# v1.0.0-alpha.2 -> 1.0.0a2
# v1.0.0-beta.1  -> 1.0.0b1
# v1.0.0-rc.1    -> 1.0.0rc1
pep440() {
  local ver="${1#v}"
  local core="${ver%%-*}"
  local pre=""
  if [[ "$ver" == *-* ]]; then
    local suffix="${ver#*-}"
    local label="${suffix%%.*}"
    local num="${suffix#*.}"
    [[ "$num" == "$suffix" ]] && num=""
    case "$label" in
      alpha|a) pre="a${num}" ;;
      beta|b)  pre="b${num}" ;;
      rc|c)    pre="rc${num}" ;;
      *)       pre="" ;;
    esac
  fi
  printf '%s%s' "$core" "$pre"
}

# --------------------------- 回滚栈机制 ---------------------------
# 每个可变更步骤注册对应回滚动作；异常时按注册逆序依次执行。
ROLLBACK_STACK=()
ROLLING_BACK=false
TMP_BLOCK=""
TMP_OUT=""
TMP_BACKUP=""

rollback_push() { ROLLBACK_STACK+=("$1"); }

rollback_run() {
  if ((${#ROLLBACK_STACK[@]} == 0)); then
    log_info "无需回滚：尚未产生任何变更"
    return 0
  fi
  log_warn "开始回滚，撤销已执行变更（逆序 ${#ROLLBACK_STACK[@]} 步）..."
  for ((i = ${#ROLLBACK_STACK[@]} - 1; i >= 0; i--)); do
    local action="${ROLLBACK_STACK[i]}"
    log_warn "  回滚 -> $action"
    eval "$action" || log_warn "  回滚步骤跳过（已撤销或无操作）：$action"
  done
  ROLLBACK_STACK=()
  log_warn "回滚执行完毕"
}

on_error() {
  local ec=$?
  if [[ "$ROLLING_BACK" == true ]]; then exit "$ec"; fi
  ROLLING_BACK=true
  log_err "发布流程失败（退出码 $ec），触发全链路回滚..."
  rollback_run
  log_err "已回滚：工作区、提交记录、标签均恢复至发布前状态。"
  exit "$ec"
}
trap on_error ERR

fail() {
  ROLLING_BACK=true
  log_err "$*"
  rollback_run
  log_err "发布终止：未产生任何有效变更。"
  exit 1
}

cleanup() {
  rm -f "${TMP_BLOCK:-}" "${TMP_OUT:-}" "${TMP_BACKUP:-}" "${TMP_PKG_BACKUP:-}" 2>/dev/null || true
}
trap cleanup EXIT

# ------------------------------ 帮助 ------------------------------
usage() {
  cat <<'USAGE'
用法: ./scripts/release.sh <version> [options]

参数:
  <version>     目标版本号（必填），语义化规范 vX.Y.Z，
                支持预发布后缀，如 v1.0.0 / v1.1.0-beta.1
  --dry-run     试运行：仅输出流程预览与变更日志，不执行任何实际修改
  --full-test   执行全量回归测试（默认仅执行 quick 快速门禁）
  --push        发布成功后推送标签至远程 origin（默认仅本地发布）
  --force       跳过工作区干净检查，强制发布（不推荐）
  --bump        同步版本号到 pyproject.toml（默认不同步；启用后发布提交
                将同时包含 CHANGELOG.md 与该版本文件）
  -h, --help    显示本帮助

示例:
  ./scripts/release.sh v1.0.0 --dry-run
  ./scripts/release.sh v1.0.0 --full-test
  ./scripts/release.sh v1.1.0-rc.1 --push
  ./scripts/release.sh v1.0.2 --bump
USAGE
}

# ---------------------------- 参数解析 ----------------------------
VERSION=""
DRY_RUN=false
FULL_TEST=false
DO_PUSH=$DEFAULT_PUSH
FORCE=false
BUMP_VERSION=false

while (($# > 0)); do
  case "$1" in
    --dry-run)   DRY_RUN=true ;;
    --full-test) FULL_TEST=true ;;
    --push)      DO_PUSH=true ;;
    --force)     FORCE=true ;;
    --bump)      BUMP_VERSION=true ;;
    -h|--help)   usage; exit 0 ;;
    -*)          log_err "未知参数: $1"; usage; exit 2 ;;
    *)
      if [[ -z "$VERSION" ]]; then VERSION="$1"
      else log_err "多余的位置参数: $1"; usage; exit 2; fi
      ;;
  esac
  shift
done

# 定位到仓库根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -d "$SCRIPT_DIR" ]] || SCRIPT_DIR="$(pwd)"

# ======================= 1/4 前置合规校验 =======================
stage "1/4 环境校验"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || { log_err "当前路径不在 Git 工作仓库内，请在仓库目录中执行。"; exit 1; }

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
log_info "仓库根目录: $ROOT"

current_branch="$(git rev-parse --abbrev-ref HEAD)"
[[ "$current_branch" == "$RELEASE_BRANCH" ]] \
  || fail "分支不合规：仅允许在 '$RELEASE_BRANCH' 分支发布，当前分支为 '$current_branch'。"
log_ok "发布分支校验通过: $current_branch"

if [[ -z "$VERSION" ]]; then
  log_err "缺少必填的目标版本号。"
  usage
  exit 2
fi

version_re='^v[0-9]+\.[0-9]+\.[0-9]+(-(alpha|beta|rc)(\.[0-9]+)?)?$'
[[ "$VERSION" =~ $version_re ]] \
  || fail "版本号格式非法: '$VERSION'。需严格匹配语义化规范 vX.Y.Z（可选 alpha/beta/rc 后缀），例如 v1.0.0 或 v1.1.0-beta.1。"
log_ok "版本号格式校验通过: $VERSION"

if git tag -l "$VERSION" | grep -Fxq "$VERSION"; then
  fail "标签 '$VERSION' 已存在于本地仓库，禁止重复发布（幂等拦截）。"
fi
log_ok "标签唯一性校验通过: $VERSION（未重复）"

dirty="$(git status --porcelain)"
if [[ "$FORCE" == true ]]; then
  log_warn "--force 已启用：跳过工作区干净检查（不推荐）"
elif [[ -n "$dirty" ]]; then
  if [[ "$DRY_RUN" == true ]]; then
    # 试运行不产生任何修改，仅提示，保证预览可用
    log_warn "工作区存在未提交变更；试运行仅提示，正式发布前需先提交或暂存："
    printf '%s\n' "$dirty"
  else
    log_err "工作区存在未提交的变更，禁止发布。变更如下："
    printf '%s\n' "$dirty" >&2
    fail "请先提交或暂存变更，或使用 --force 强制发布。"
  fi
else
  log_ok "工作区干净校验通过"
fi

# ======================= 2/4 变更日志生成 =======================
stage "2/4 变更日志生成"

PREV_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
if [[ -n "$PREV_TAG" ]]; then
  RANGE="$PREV_TAG..HEAD"
  log_info "上一个版本标签: $PREV_TAG"
else
  RANGE="HEAD"
  log_info "未找到历史标签，将生成全量提交日志"
fi

mapfile -t COMMITS < <(git log "$RANGE" --pretty=format:'%s' 2>/dev/null || true)
if ((${#COMMITS[@]} == 0)); then
  log_warn "目标范围内无提交记录，变更日志将为空"
fi

declare -A BUCKETS
for c in "${COMMITS[@]:-}"; do
  [[ -n "$c" ]] || continue
  prefix="${c%%:*}"        # 取冒号之前（ Conventional Commit 类型 ）
  prefix="${prefix%%(*}"   # 去掉作用域，如 feat(api)
  prefix="${prefix//[[:space:]]/}"
  prefix="${prefix,,}"     # 转小写
  case "$prefix" in
    feat|feature)  key=feat ;;
    fix|bugfix|hotfix) key=fix ;;
    docs|doc)      key=docs ;;
    refactor)      key=refactor ;;
    test|tests)    key=test ;;
    chore)         key=chore ;;
    *)             key=chore ;;
  esac
  if [[ -z "${BUCKETS[$key]:-}" ]]; then BUCKETS[$key]="$c"; else BUCKETS[$key]="${BUCKETS[$key]}
$c"; fi
done

RELEASE_DATE="$(date +%Y-%m-%d)"
BLOCK="## [$VERSION] - $RELEASE_DATE"$'\n'
for key in feat fix docs refactor test chore; do
  [[ -n "${BUCKETS[$key]:-}" ]] || continue
  case "$key" in
    feat)     title="### 新特性 (feat)" ;;
    fix)      title="### 修复 (fix)" ;;
    docs)     title="### 文档 (docs)" ;;
    refactor) title="### 重构 (refactor)" ;;
    test)     title="### 测试 (test)" ;;
    chore)    title="### 其他 (chore)" ;;
  esac
  BLOCK+=$'\n'"$title"$'\n'
  while IFS= read -r line; do
    [[ -n "$line" ]] && BLOCK+="- $line"$'\n'
  done <<< "${BUCKETS[$key]}"
done

log_info "变更日志预览："
printf '%s\n' "$BLOCK"

if [[ "$DRY_RUN" == true ]]; then
  if [[ "$BUMP_VERSION" == true && -f "$VERSION_FILE" ]]; then
    log_info "试运行：将同步 $VERSION_FILE 版本号为 $(pep440 "$VERSION")"
  fi
  log_warn "试运行模式（--dry-run）：流程预览完成，未执行任何实际修改。"
  log_info "后续将执行：3/4 回归门禁 -> 4/4 版本标签提交（--dry-run 下已跳过）"
  exit 0
fi

TMP_BLOCK="$(mktemp)"
printf '%s\n' "$BLOCK" > "$TMP_BLOCK"

if [[ -f "$CHANGELOG_FILE" ]]; then
  TMP_BACKUP="$(mktemp)"
  cp "$CHANGELOG_FILE" "$TMP_BACKUP"
  rollback_push "cp '$TMP_BACKUP' '$CHANGELOG_FILE' && rm -f '$TMP_BACKUP'; git update-index -q --refresh -- '$CHANGELOG_FILE' >/dev/null 2>&1 || true"
else
  rollback_push "rm -f '$CHANGELOG_FILE'; git update-index -q --refresh -- '$CHANGELOG_FILE' >/dev/null 2>&1 || true"
fi

TMP_OUT="$(mktemp)"
if [[ -f "$CHANGELOG_FILE" ]]; then
  # 保留既有标题行（以 '# ' 开头），新版本块插入其后
  first_line="$(head -n 1 "$CHANGELOG_FILE")"
  if [[ "$first_line" == "# "* ]]; then
    {
      printf '%s\n\n' "$first_line"
      cat "$TMP_BLOCK"
      tail -n +2 "$CHANGELOG_FILE"
    } > "$TMP_OUT"
  else
    cat "$TMP_BLOCK" "$CHANGELOG_FILE" > "$TMP_OUT"
  fi
else
  {
    printf '# 变更日志\n\n'
    cat "$TMP_BLOCK"
  } > "$TMP_OUT"
fi
mv "$TMP_OUT" "$CHANGELOG_FILE"
log_ok "变更日志已更新: $CHANGELOG_FILE"

# ---- 版本号同步（--bump，默认关闭）----
if [[ "$BUMP_VERSION" == true ]]; then
  if [[ ! -f "$VERSION_FILE" ]]; then
    fail "未找到版本文件: $VERSION_FILE（--bump 需要该文件存在）"
  fi
  new_version="$(pep440 "$VERSION")"
  old_version="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$VERSION_FILE" | head -n 1)"
  if [[ -z "$old_version" ]]; then
    fail "在 $VERSION_FILE 中未找到 'version = \"...\"' 字段，无法同步版本号。"
  elif [[ "$old_version" == "$new_version" ]]; then
    log_info "版本号已是 $new_version，无需同步"
  else
    TMP_PKG_BACKUP="$(mktemp)"
    cp "$VERSION_FILE" "$TMP_PKG_BACKUP"
    rollback_push "cp '$TMP_PKG_BACKUP' '$VERSION_FILE' && rm -f '$TMP_PKG_BACKUP'; git update-index -q --refresh -- '$VERSION_FILE' >/dev/null 2>&1 || true"
    sed -i "0,/^version = /s/^version = \".*\"/version = \"$new_version\"/" "$VERSION_FILE"
    log_ok "版本号已同步: $VERSION_FILE $old_version -> $new_version"
  fi
fi

# ======================= 3/4 回归测试门禁 =======================
stage "3/4 回归测试门禁"

TEST_MODE="quick"
[[ "$FULL_TEST" == true ]] && TEST_MODE="full"
log_info "回归模式: $TEST_MODE"

if [[ ! -f "$REGRESSION_SCRIPT" ]]; then
  fail "未找到回归脚本: $REGRESSION_SCRIPT（当前目录: $ROOT）"
fi

if bash "$REGRESSION_SCRIPT" "$TEST_MODE"; then
  log_ok "回归门禁通过（$TEST_MODE）"
else
  fail "回归门禁未通过（$TEST_MODE），终止发布并回滚变更日志。"
fi

# ======================= 4/4 版本标签提交 =======================
stage "4/4 版本标签提交"

RELEASE_PATHS=("$CHANGELOG_FILE")
if [[ "$BUMP_VERSION" == true && -f "$VERSION_FILE" ]]; then
  RELEASE_PATHS+=("$VERSION_FILE")
fi
git add "${RELEASE_PATHS[@]}"
git commit -m "$COMMIT_MSG_PREFIX $VERSION" >/dev/null
log_ok "发布提交已创建: $COMMIT_MSG_PREFIX $VERSION"
rollback_push "git reset --hard HEAD~1 >/dev/null 2>&1"

git tag -a "$VERSION" -m "Release $VERSION" -m "$BLOCK" >/dev/null
log_ok "附注标签已创建: $VERSION"
rollback_push "git tag -d '$VERSION' >/dev/null 2>&1"

# 校验结果正确性
git tag -l "$VERSION" | grep -Fxq "$VERSION" \
  || fail "标签创建校验失败：$VERSION 未出现在标签列表中。"
log_info "发布提交内容："
git show --stat --oneline HEAD | head -n 5

# ======================= 可选：远程推送 =======================
if [[ "$DO_PUSH" == true ]]; then
  log_info "远程推送（可选阶段）：推送标签至 $REMOTE_NAME"
  if git push "$REMOTE_NAME" "$VERSION"; then
    log_ok "标签已推送至 $REMOTE_NAME/$VERSION"
  else
    log_warn "推送失败：保留本地标签 $VERSION，不触发回滚。"
    log_warn "可手动执行：git push $REMOTE_NAME $VERSION"
  fi
else
  log_info "默认仅本地发布，未推送远程（如需推送请加 --push）"
fi

# ============================ 完成 ============================
printf '\n'
log_ok "发布成功：$VERSION"
log_info "  分支      : $current_branch"
log_info "  发布提交  : $(git rev-parse --short HEAD)"
log_info "  附注标签  : $VERSION"
log_info "  变更日志  : $CHANGELOG_FILE"
exit 0
