#!/usr/bin/env bash
# 幂等地确保一个 Colab session 存在。
#
# 用法:
#   scripts/colab_up.sh <session> [--gpu T4|L4|G4|H100|A100] [--tpu v5e1|v6e1] [--high-mem] [--restart]
set -uo pipefail

SESSION=""
GPU=""
TPU=""
HIGHMEM=0
RESTART=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gpu) GPU="$2"; shift 2 ;;
    --tpu) TPU="$2"; shift 2 ;;
    --high-mem) HIGHMEM=1; shift ;;
    --restart) RESTART=1; shift ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) SESSION="$1"; shift ;;
  esac
done

[[ -z "$SESSION" ]] && { echo "用法: $0 <session> [--gpu T4]" >&2; exit 2; }
command -v colab >/dev/null || { echo "colab CLI 未安装: uv tool install google-colab-cli" >&2; exit 2; }

# 注意: `colab status -s X` 在 session 不存在时**退出码仍是 0**（只打印 not found），
# 所以必须解析 `colab sessions` 的 `[name]` 前缀来判断。
session_exists() {
  colab sessions 2>/dev/null | awk -v want="[$SESSION]" '$1 == want { found = 1 } END { exit !found }'
}

if [[ $RESTART -eq 1 ]] && session_exists; then
  echo "[up] 重启 $SESSION 的内核..."
  colab restart-kernel -s "$SESSION"
fi

if session_exists; then
  echo "[up] session '$SESSION' 已存在:"
  colab status -s "$SESSION"
  exit 0
fi

ARGS=(-s "$SESSION")
[[ -n "$GPU" ]] && ARGS+=(--gpu "$GPU")
[[ -n "$TPU" ]] && ARGS+=(--tpu "$TPU")
[[ $HIGHMEM -eq 1 ]] && ARGS+=(--high-mem)

echo "[up] 创建 session '$SESSION'..."
if ! colab new "${ARGS[@]}"; then
  cat >&2 <<'EOF'
[up] 创建失败。常见原因：
  - GPU/TPU 免费额度用尽（HTTP 400）→ 去掉 --gpu 用 CPU，或等额度恢复
  - --gpu 值拼错（T4/L4/G4/H100/A100 全大写）→ 大小写错误会被静默当成 A100 然后失败
  - 并发 runtime 数已达上限 → colab sessions 看有没有遗留的，停掉不用的
EOF
  exit 1
fi

colab status -s "$SESSION"
