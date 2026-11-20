#!/usr/bin/env bash
# backfill_pace_monthly.sh — HỒI TỐ % + nhịp độ cho MỘT tháng (as-of), ghi LỊCH SỬ (--hist-only).
# Mỗi tháng 1 bundle riêng (tin ≤ hết tháng đó) → không bị cắt về các tháng gần như retro 1-bundle.
#   bash backfill_pace_monthly.sh 2026-03
set -euo pipefail
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")"

M="${1:?Cần tháng YYYY-MM, vd 2026-03}"
BATCH=25
echo "==== $(date '+%F %T') · HỒI TỐ tháng $M ===="

python3 prepare_pace_bundles.py --asof "$M" --months 11 --maxn 12 --out pace_bundles.json
N=$(python3 -c 'import json;print(len(json.load(open("pace_bundles.json"))))' 2>/dev/null || echo 0)
if [ "$N" = "0" ]; then echo "Không có dự án có tin tới $M — bỏ qua."; exit 0; fi

rm -rf pace_batches && mkdir -p pace_batches
python3 - "$BATCH" <<'PY'
import json, sys
B = int(sys.argv[1]); b = json.load(open('pace_bundles.json', encoding='utf-8')); t = list(b)
for n, i in enumerate(range(0, len(t), B)):
    json.dump({k: b[k] for k in t[i:i+B]},
              open(f'pace_batches/batch_{n}.json', 'w', encoding='utf-8'), ensure_ascii=False)
print("lô:", -(-len(t)//B))
PY

PROMPT="AS-OF: Dữ liệu CHỈ tới hết tháng $M. Chấm theo trạng thái dự án TẠI thời điểm đó, KHÔNG dùng thông tin sau $M.
$(cat pace_agent_prompt.txt)"

for f in pace_batches/batch_*.json; do
  i=$(basename "$f" | tr -dc '0-9')
  echo "  → chấm lô $i (tháng $M) ..."
  cat "$f" | claude -p "$PROMPT" --output-format text > "pace_batches/raw_${i}.txt" || { echo "  ! lô $i lỗi"; continue; }
  python3 - "$i" <<'PY'
import re, sys
i = sys.argv[1]; t = open(f'pace_batches/raw_{i}.txt', encoding='utf-8').read()
m = re.search(r'\{.*\}', t, re.S)
open(f'pace_batches/verdicts_{i}.json', 'w', encoding='utf-8').write(m.group(0) if m else '{}')
PY
done

python3 merge_pace_verdicts.py
python3 apply_pace_llm.py --month "$M" --hist-only     # CHỈ ghi lịch sử, không đè verdict hiện tại
echo "==== $(date '+%F %T') XONG tháng $M ===="
