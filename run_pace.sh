#!/usr/bin/env bash
# run_pace.sh — Đánh giá NHỊP ĐỘ dự án bằng Claude Code (Pro/Max, headless) trên server.
#   prepare (pymongo) → chia lô → claude -p đọc-hiểu từng lô → gộp → apply (pymongo).
# Chạy tay: ./run_pace.sh   ·   Cron: 0 11 * * 5  (18h thứ 6 VN = 11h UTC)
set -euo pipefail
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"   # cron thiếu PATH → khai báo rõ
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")"

BATCH=20
LOG="run_pace.log"
echo "==== $(date '+%F %T') bắt đầu ====" | tee -a "$LOG"

# 1) Gom tin gần đây → pace_bundles.json (pymongo, đọc DB)
python3 prepare_pace_bundles.py --months 10 --maxn 30 | tee -a "$LOG"

# 2) Chia lô
rm -rf pace_batches && mkdir -p pace_batches
python3 - "$BATCH" <<'PY'
import json, os, sys
B = int(sys.argv[1])
b = json.load(open('pace_bundles.json', encoding='utf-8'))
tids = list(b)
for n, i in enumerate(range(0, len(tids), B)):
    json.dump({t: b[t] for t in tids[i:i+B]},
              open(f'pace_batches/batch_{n}.json', 'w', encoding='utf-8'), ensure_ascii=False)
print("lô:", -(-len(tids)//B))
PY

# 3) Mỗi lô: claude -p đọc-hiểu → JSON verdict (dùng Pro/Max, không API phí)
for f in pace_batches/batch_*.json; do
  i=$(basename "$f" | tr -dc '0-9')
  echo "  → đánh giá lô $i ..." | tee -a "$LOG"
  cat "$f" | claude -p "$(cat pace_agent_prompt.txt)" --output-format text > "pace_batches/raw_${i}.txt" 2>>"$LOG" || { echo "  ! lô $i lỗi claude" | tee -a "$LOG"; continue; }
  # trích object JSON từ output
  python3 - "$i" <<'PY'
import re, sys
i = sys.argv[1]
t = open(f'pace_batches/raw_{i}.txt', encoding='utf-8').read()
m = re.search(r'\{.*\}', t, re.S)
open(f'pace_batches/verdicts_{i}.json', 'w', encoding='utf-8').write(m.group(0) if m else '{}')
PY
done

# 4) Gộp + ghi paceLLM vào DB (pymongo)
python3 merge_pace_verdicts.py | tee -a "$LOG"
python3 apply_pace_llm.py       | tee -a "$LOG"

echo "==== $(date '+%F %T') XONG ====" | tee -a "$LOG"
# Site tự cập nhật ở lần build hàng giờ của GitHub Actions (step5 ưu tiên paceLLM).
