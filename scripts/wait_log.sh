#!/usr/bin/env bash
# 轮询一个日志文件，**任务一结束就返回**，不傻等固定时长。
#
# 背景：以前的写法是 `nohup colab exec ... & sleep 300; grep 日志`，
# 远端其实 1~5 秒就结束了（常常还是秒失败），却每次罚站 5 分钟。
#
# 用法:
#   scripts/wait_log.sh <logfile> [--pid PID] [--max-wait 600] [--interval 3] [--tail 12]
#
#   --pid PID        后台任务 pid。给了就以「进程退出」为结束条件（最准）
#   --max-wait SEC   兜底上限，默认 1800
#   --interval SEC   轮询间隔，默认 3
#   --tail N         返回时打印最后 N 行，默认 12
#   --quiet          只打印一行结论
#
# 结束条件（任一）:
#   1) --pid 指定的进程已退出         → 立刻返回
#   2) 日志出现结束标志（Traceback / Error: / 完成 / DONE / OOM 等）且 3 个间隔内不再增长
#   3) 超过 --max-wait
#
# 退出码: 0 = 正常结束或命中结束标志；1 = 超时；2 = 用法错误
set -uo pipefail

LOG=""
PID=""
MAX_WAIT=1800
INTERVAL=3
TAIL=12
QUIET=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pid) PID="$2"; shift 2 ;;
    --max-wait) MAX_WAIT="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --tail) TAIL="$2"; shift 2 ;;
    --quiet) QUIET=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    -*) echo "未知参数: $1" >&2; exit 2 ;;
    *) LOG="$1"; shift ;;
  esac
done

[[ -z "$LOG" ]] && { echo "用法: $0 <logfile> [--pid PID] [--max-wait 600]" >&2; exit 2; }

START=$SECONDS
prev_size=-1
stable=0
reason=""

END_RE='Traceback \(most recent call last\)|^[[:space:]]*(Error|Exception|RuntimeError|ValueError|NameError|AttributeError|ModuleNotFoundError|ImportError|KeyboardInterrupt|OutOfMemoryError)[: ]|^\['"'"'[0-9]+'"'"'\]|完成|DONE|OK$|OOM|Killed'

while :; do
  elapsed=$((SECONDS - START))

  if [[ -n "$PID" ]] && ! kill -0 "$PID" 2>/dev/null; then
    reason="进程退出"; break
  fi
  [[ $elapsed -ge $MAX_WAIT ]] && { reason="达到上限 ${MAX_WAIT}s"; break; }

  if [[ -f "$LOG" ]]; then size=$(wc -c < "$LOG"); else size=0; fi
  if [[ "$size" == "$prev_size" ]]; then
    stable=$((stable + 1))
  else
    stable=0
    prev_size="$size"
  fi

  # 无 pid 模式：日志停止增长 + 命中结束标志 → 认为结束
  if [[ -z "$PID" ]] && [[ $stable -ge 3 ]] && grep -qaE "$END_RE" "$LOG" 2>/dev/null; then
    reason="日志稳定且命中结束标志"; break
  fi
  # 有 pid 模式也留一条快路：命中 traceback 且日志稳定，说明脚本已崩但进程还没退
  sleep "$INTERVAL"
done

elapsed=$((SECONDS - START))
echo "[wait_log] ${elapsed}s 结束（${reason}）: $LOG"
[[ $QUIET -eq 1 ]] && exit 0

CLEAN=$(tr -d '\r' < "$LOG" 2>/dev/null | tr '\r' '\n' | sed -e 's/\x1b\[[0-9;]*m//g')
if grep -qaE 'Traceback \(most recent call last\)' <<<"$CLEAN"; then
  echo "[wait_log] 检出异常，末尾 ${TAIL} 行："
  tail -n "$TAIL" <<<"$CLEAN"
  exit 0
fi
echo "[wait_log] 末尾 ${TAIL} 行："
tail -n "$TAIL" <<<"$CLEAN"
exit 0
