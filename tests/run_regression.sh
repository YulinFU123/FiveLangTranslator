#!/usr/bin/env bash
# 回归测试门禁 —— 供 scripts/release.sh 调用
# 用法: ./tests/run_regression.sh [quick|full]
#   quick : 核心功能快速门禁（pytest 套件）
#   full  : 全量回归（pytest 完整套件 + 端到端性能基准）
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT" || exit 1

MODE="${1:-quick}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"

case "$MODE" in
  quick)
    echo "[regression] 快速门禁: pytest 核心套件"
    python -m pytest -q
    ;;
  full)
    echo "[regression] 全量回归: pytest 完整套件"
    python -m pytest -q
    echo "[regression] 全量回归: 端到端性能基准"
    python -m tests.e2e_latency --no-pixel --render-timeout 5000
    ;;
  *)
    echo "[regression] 未知模式: $MODE （支持 quick|full）" >&2
    exit 2
    ;;
esac

status=$?
if [[ $status -eq 0 ]]; then
  echo "[regression] 通过 ($MODE)"
else
  echo "[regression] 失败 ($MODE)，退出码 $status" >&2
fi
exit $status
