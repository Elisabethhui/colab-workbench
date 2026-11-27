#!/usr/bin/env bash
# 在远端 Colab session 上执行一个 local notebook，把输出收进 runs/，并抽出 traceback。
#
# 用法:
#   scripts/colab_run_nb.sh <notebook.ipynb> [session] [--gpu T4] [--keep-output]
#
# 退出码与 colab exec 一致（0 成功 / 1 未捕获异常 / N sys.exit(N)）。
set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION=""
GPU=""
KEEP=0
NOTEBOOK=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gpu) GPU="$2"; shift 2 ;;
    --keep-output) KEEP=1; shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) if [[ -z "$NOTEBOOK" ]]; then NOTEBOOK="$1"; else SESSION="$1"; fi; shift ;;
  esac
done

[[ -z "$NOTEBOOK" ]] && { echo "用法: $0 <notebook.ipynb> [session] [--gpu T4]" >&2; exit 2; }
[[ -f "$NOTEBOOK" ]] || { echo "找不到文件: $NOTEBOOK" >&2; exit 2; }

NAME="$(basename "$NOTEBOOK" .ipynb)"
PROJECT="$(basename "$(dirname "$NOTEBOOK")")"
[[ -z "$SESSION" ]] && SESSION="$(echo "$PROJECT" | tr -c 'a-zA-Z0-9_-' '-' | cut -c1-20)"

command -v colab >/dev/null || { echo "colab CLI 未安装: uv tool install google-colab-cli" >&2; exit 2; }

# 1. 确保 session 存在（幂等）
if ! colab status -s "$SESSION" >/dev/null 2>&1; then
  echo "[run-nb] session '$SESSION' 不存在，创建中${GPU:+（--gpu $GPU）}..."
  # shellcheck disable=SC2086
  colab new -s "$SESSION" ${GPU:+--gpu "$GPU"} >/dev/null || { echo "[run-nb] 创建 session 失败" >&2; exit 1; }
fi

# 2. 执行（colab exec 把结果写在 notebook 同目录的 <name>_output.ipynb）
echo "[run-nb] 执行 $NOTEBOOK  @ $SESSION"
colab exec -s "$SESSION" -f "$NOTEBOOK"
RC=$?

# 3. 把输出收进 runs/，避免污染项目目录
NB_DIR="$(cd "$(dirname "$NOTEBOOK")" && pwd)"
OUT="$NB_DIR/${NAME}_output.ipynb"
TS="$(date +%Y%m%d-%H%M%S)"
DEST="$BASE/runs/${PROJECT}-${NAME}-${TS}.ipynb"

if [[ -f "$OUT" ]]; then
  mkdir -p "$BASE/runs"
  mv "$OUT" "$DEST"
  echo
  echo "[run-nb] 输出已收进: runs/$(basename "$DEST")"
  echo "[run-nb] ---- 错误摘要 ----"
  python3 "$BASE/scripts/nb_errors.py" "$DEST"
  ERR_RC=$?
  [[ $RC -eq 0 && $ERR_RC -ne 0 ]] && RC=$ERR_RC
else
  echo "[run-nb] 警告: 没有生成输出 notebook（可能 session 在远端就被中断了）" >&2
  echo "[run-nb] 排查: colab status -s $SESSION ; colab log -s $SESSION -n 40" >&2
fi

echo
echo "[run-nb] 退出码: $RC"
[[ $KEEP -eq 0 ]] && echo "[run-nb] 记得收工: colab stop -s $SESSION"
exit $RC
