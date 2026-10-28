#!/usr/bin/env bash
# backfill_pace_history.sh — HỒI TỐ lịch sử nhịp độ: Claude đọc lại tin THEO TỪNG THÁNG
# (từ START đến tháng hiện tại), chấm pace + điểm -5..+5 "as-of" tháng đó → Infra_Pace_History.
# CHỈ ghi lịch sử (--hist-only), KHÔNG đè verdict hiện tại. Tốn quota → chạy 1 lần, nên để qua đêm.
#   ./backfill_pace_history.sh [START=2026-01]
set -euo pipefail
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")"

START="${1:-2026-01}"
BATCH=30
LOG="backfill_pace.log"
NOW="$(date +%Y-%m)"
echo "==== $(date '+%F %T') HỒI TỐ từ $START đến $NOW ====" | tee -a "$LOG"

# danh sách tháng START..NOW
months=$(python3 - "$START" "$NOW" <<'PY'
import sys
def ym(s): y,m=s.split("-"); return int(y)*12+int(m)-1
a,b=ym(sys.argv[1]),ym(sys.argv[2])
print(" ".join(f"{t//12:04d}-{t%12+1:02d}" for t in range(a,b+1)))
PY
)

for M in $months; do
  echo "---- Tháng $M ----" | tee -a "$LOG"
  # 1) Bundle as-of M (chỉ tin & giải ngân ≤ hết tháng M)
  python3 prepare_pace_bundles.py --months 8 --maxn 14 --asof "$M" | tee -a "$LOG"
  N=$(python3 -c 'import json;print(len(json.load(open("pace_bundles.json"))))' 2>/dev/null || echo 0)
  if [ "$N" = "0" ]; then echo "  (không có dự án có tin tới $M — bỏ qua)" | tee -a "$LOG"; continue; fi

  # 2) Nhắc "as-of" cho agent (không dùng kiến thức sau tháng M)
  PROMPT="HỒI TỐ: Dữ liệu CHỈ tới hết tháng $M. Chấm pace + score theo trạng thái dự án TẠI THỜI ĐIỂM đó, KHÔNG dùng thông tin sau $M.
$(cat pace_agent_prompt.txt)"

  # 3) Chia lô → claude -p → trích JSON
  rm -rf pace_batches && mkdir -p pace_batches
  python3 - "$BATCH" <<'PY'
import json,sys
B=int(sys.argv[1]); b=json.load(open('pace_bundles.json',encoding='utf-8')); t=list(b)
for n,i in enumerate(range(0,len(t),B)):
    json.dump({k:b[k] for k in t[i:i+B]},open(f'pace_batches/batch_{n}.json','w',encoding='utf-8'),ensure_ascii=False)
PY
  for f in pace_batches/batch_*.json; do
    i=$(basename "$f" | tr -dc '0-9')
    cat "$f" | claude -p "$PROMPT" --output-format text > "pace_batches/raw_${i}.txt" 2>>"$LOG" || { echo "  ! lô $i lỗi" | tee -a "$LOG"; continue; }
    python3 - "$i" <<'PY'
import re,sys
i=sys.argv[1]; t=open(f'pace_batches/raw_{i}.txt',encoding='utf-8').read()
m=re.search(r'\{.*\}',t,re.S)
open(f'pace_batches/verdicts_{i}.json','w',encoding='utf-8').write(m.group(0) if m else '{}')
PY
  done

  # 4) Gộp → ghi LỊCH SỬ tháng M (chỉ history, không đè verdict hiện tại)
  python3 merge_pace_verdicts.py | tee -a "$LOG"
  python3 apply_pace_llm.py --month "$M" --hist-only | tee -a "$LOG"
done

echo "==== $(date '+%F %T') HỒI TỐ XONG ====" | tee -a "$LOG"
echo "Site tự cập nhật sparkline ở lần build kế tiếp (GitHub Actions)."
