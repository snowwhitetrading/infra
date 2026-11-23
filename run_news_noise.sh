#!/usr/bin/env bash
# run_news_noise.sh — Soát TIN NHIỄU bằng Claude Code (Pro/Max, headless): bài chỉ nhắc địa danh / dự án khác trùng tên
# bị gắn nhầm vào dự án → gỡ, và ghi nhớ (not_projects) để các lượt quét sau không gắn lại.
#   prepare (pymongo) → mỗi lô: claude -p đọc tiêu đề, phán → apply (pymongo).
# Dùng:  ./run_news_noise.sh [SINCE_ISO]     (mặc định: bài quét trong 2 ngày gần đây, nguồn gnews)
set -uo pipefail
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")"
PY=python3; $PY -c "" 2>/dev/null || PY=python     # Windows: 'python3' có thể chỉ là lối tắt Store

SINCE="${1:-$($PY -c 'import datetime as d;print((d.datetime.now(d.timezone.utc)-d.timedelta(days=2)).isoformat())')}"
DIR="noise_batches"
MAXB="${MAX_BATCHES:-25}"          # trần số lô mỗi lượt (≈ 350 cặp/lô) — phần dư để lượt sau

$PY clean_news_noise.py --prepare --since "$SINCE" --out "$DIR" || exit 0
PROMPT="$(cat news_noise_prompt.txt)"
n=0
for f in "$DIR"/batch_*.json; do
  [ -e "$f" ] || break
  [ "$n" -ge "$MAXB" ] && { echo "đạt trần $MAXB lô — phần còn lại để lượt sau"; break; }
  i=$(basename "$f" .json | tr -dc '0-9')
  echo "  → soát lô $i ..."
  cat "$f" | claude -p "$PROMPT" --output-format text > "$DIR/raw_${i}.txt" 2>>"$DIR/err.txt" \
    || { echo "  ! lô $i lỗi claude"; rm -f "$DIR/raw_${i}.txt"; }
  n=$((n+1))
done
$PY clean_news_noise.py --apply "$DIR"
