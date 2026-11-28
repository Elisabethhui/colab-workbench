#!/usr/bin/env bash
# 上传 + 在远端跑一个 .py / .ipynb，**任务一结束就返回**（不再 `sleep 300` 罚站）。
#
# 用法:
#   scripts/colab_run.sh -s <session> -f <本地文件> [--remote 远端路径] [--max-wait 1800]
#                        [--exec-timeout 0] [--tag 名字] [--keep-log]
#
# 例:
#   scripts/colab_run.sh -s cpurun -f /tmp/verify3.py
#   scripts/colab_run.sh -s gpu1 -f projects/rppg/exp_stream.ipynb --max-wait 3600
#
# 行为:
#   1) `colab upload` 把文件推上去（约 1s，实测）
#   2) 后台起 `colab exec`，日志写 /tmp/colabrun-<tag>.log
#   3) 交给 scripts/wait_log.sh 轮询：终端一断开/脚本一结束立刻返回并打印 traceback
#
# 退出码: 0 = 跑完（含远端报错，看日志）；1 = 超时未结束；2 = 用法/上传失败
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SESSION=""
FILE=""
REMOTE=""
MAX_WAIT=1800
EXEC_TIMEOUT=0
TAG=""
KEEP_LOG=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--session) SESSION="$2"; shift 2 ;;
    -f|--file) FILE="$2"; shift 2 ;;
    --remote) REMOTE="$2"; shift 2 ;;
    --max-wait) MAX_WAIT="$2"; shift 2 ;;
    --exec-timeout) EXEC_TIMEOUT="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --no-keep-log) KEEP_LOG=0; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

[[ -z "$SESSION" || -z "$FILE" ]] && { echo "用法: $0 -s <session> -f <file> [--max-wait 1800]" >&2; exit 2; }
[[ -f "$FILE" ]] || { echo "文件不存在: $FILE" >&2; exit 2; }
[[ -n "$REMOTE" ]] || REMOTE="$FILE"
[[ -n "$TAG" ]] || TAG="$(basename "${FILE%.*}")-$(date +%H%M%S)"
LOG="/tmp/colabrun-${TAG}.log"

# 本地代理对 colab.dev 长连接有害，CLI 自己会读取。保持和人工操作一致：
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY

echo "[run] 上传 $(basename "$FILE") → $SESSION:$REMOTE"
if ! timeout 120 colab upload "$FILE" "$REMOTE" -s "$SESSION" 2>&1 | tail -1; then
  echo "[run] 上传失败。先确认 session 活着: scripts/sessions.py list" >&2
  exit 2
fi

echo "[run] 远端执行（日志 $LOG）..."
nohup colab exec -s "$SESSION" --timeout "$EXEC_TIMEOUT" -f "$REMOTE" > "$LOG" 2>&1 &
PID=$!

# 远端脚本秒失败时进程很快退出，wait_log 会立刻返回
"$HERE/wait_log.sh" "$LOG" --pid "$PID" --max-wait "$MAX_WAIT" --tail 20
RC=$?

if [[ $RC -eq 1 ]]; then
  echo "[run] 超过 ${MAX_WAIT}s 仍未结束。远端可能还在跑："
  echo "      后续查看: scripts/wait_log.sh $LOG --max-wait 600"
  echo "      或杀掉:   kill $PID && colab stop -s $SESSION"
  exit 1
fi

# 顺手抽一下错误，方便一步看到结论
if tr -d '\r' < "$LOG" | grep -qaE 'Traceback \(most recent call last\)'; then
  echo "[run] 远端报错。"
  exit 0
fi
echo "[run] 远端执行结束。"
[[ $KEEP_LOG -eq 1 ]] && echo "[run] 完整日志: $LOG"
exit 0
