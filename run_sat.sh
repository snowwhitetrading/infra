#!/usr/bin/env bash
# run_sat.sh — Đánh giá tiến độ qua ẢNH VỆ TINH bằng Claude Code (thị giác, Pro/Max headless).
#   prepare_sat_bundles → mỗi dự án: claude -p ĐỌC ảnh (Read) → JSON → gộp → apply_sat.
# Chạy HÀNG THÁNG (ảnh Sentinel-2 cập nhật theo tháng). Cron gợi ý: 0 12 1 * *
set -euo pipefail
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")"
LOG="run_sat.log"
echo "==== $(date '+%F %T') SAT bắt đầu ====" | tee -a "$LOG"

python3 prepare_sat_bundles.py --maximg 3 | tee -a "$LOG"
PROMPT="$(cat sat_agent_prompt.txt)"
rm -rf sat_out && mkdir -p sat_out

for tid in $(python3 -c "import json; print(' '.join(json.load(open('sat_bundles.json',encoding='utf-8'))))"); do
  imgs=$(python3 -c "import json; b=json.load(open('sat_bundles.json',encoding='utf-8'))['$tid']; print(chr(10).join('  - '+i['month']+' (mây '+str(i['cloud'])+'%): '+i['file'] for i in b['imgs']))")
  name=$(python3 -c "import json; print(json.load(open('sat_bundles.json',encoding='utf-8'))['$tid']['name'])")
  echo "  → vệ tinh dự án $tid ($name) ..." | tee -a "$LOG"
  claude -p "$PROMPT

DỰ ÁN: $name
CÁC ẢNH (theo thời gian — hãy Read từng file):
$imgs" --allowedTools Read --output-format text > "sat_out/${tid}.txt" 2>>"$LOG" || { echo "  ! $tid lỗi" | tee -a "$LOG"; continue; }
done

# Gộp các output → sat_verdicts.json (trích JSON object từ mỗi file)
python3 - <<'PY' | tee -a "$LOG"
import json, os, re, glob
out = {}
for f in glob.glob('sat_out/*.txt'):
    tid = os.path.splitext(os.path.basename(f))[0]
    m = re.search(r'\{.*\}', open(f, encoding='utf-8').read(), re.S)
    if m:
        try: out[tid] = json.loads(m.group(0))
        except Exception: pass
json.dump(out, open('sat_verdicts.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
from collections import Counter
print("Sat verdicts:", len(out), "· trend:", dict(Counter(v.get('satTrend','?') for v in out.values())))
PY

python3 apply_sat.py | tee -a "$LOG"
echo "==== $(date '+%F %T') SAT XONG ====" | tee -a "$LOG"
