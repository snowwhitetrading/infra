#!/usr/bin/env bash
# run_pace_monthly.sh — Cập nhật tiến độ HÀNG THÁNG bằng Claude Code (Pro/Max, headless) trên
# GitHub Actions (thay server Viettel đã hết hạn). Dùng OAuth token Pro/Max — KHÔNG tính phí API token.
#   prepare as-of tháng vừa kết thúc → chia lô → claude -p chấm → gộp → apply --month.
# Env cần: IRIS_MONGO_URI (Mongo), CLAUDE_CODE_OAUTH_TOKEN (gói Pro/Max, từ `claude setup-token`).
set -euo pipefail
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")"

BATCH=10    # bundle giờ lớn hơn (tin trải nhiều tháng + quỹ đạo) → lô nhỏ cho chắc
# Tháng mục tiêu = tháng dương lịch LIỀN TRƯỚC hôm nay (chạy 1/12 → chấm tháng 11)
M="$(python3 -c "import datetime as d;t=d.date.today().replace(day=1)-d.timedelta(days=1);print(t.strftime('%Y-%m'))")"
echo "==== $(date '+%F %T') · cập nhật tiến độ tháng $M ===="

# 1) Gom tin as-of tháng M → pace_bundles.json (pymongo)
# --history 6: mỗi tháng ƯỚC LƯỢNG LẠI quỹ đạo % của 6 tháng liền trước (tin đến muộn sẽ sửa được đường cũ)
python3 prepare_pace_bundles.py --asof "$M" --months 11 --maxn 30 --history 6
N=$(python3 -c 'import json;print(len(json.load(open("pace_bundles.json"))))' 2>/dev/null || echo 0)
if [ "$N" = "0" ]; then echo "Không có dự án có tin tới $M — xong."; exit 0; fi

# 2) Chia lô
rm -rf pace_batches && mkdir -p pace_batches
python3 - "$BATCH" <<'PY'
import json, sys
B = int(sys.argv[1]); b = json.load(open('pace_bundles.json', encoding='utf-8')); tids = list(b)
for n, i in enumerate(range(0, len(tids), B)):
    json.dump({t: b[t] for t in tids[i:i+B]},
              open(f'pace_batches/batch_{n}.json', 'w', encoding='utf-8'), ensure_ascii=False)
print("lô:", -(-len(tids)//B))
PY

# 3) Nhắc as-of cho agent (không dùng thông tin sau tháng M) + rubric
PROMPT="AS-OF: Dữ liệu CHỈ tới hết tháng $M. Chấm theo trạng thái dự án TẠI thời điểm đó, KHÔNG dùng thông tin sau $M.
$(cat pace_agent_prompt.txt)"

# 4) Mỗi lô: claude -p (Pro/Max) → JSON verdict
for f in pace_batches/batch_*.json; do
  i=$(basename "$f" | tr -dc '0-9')
  echo "  → chấm lô $i ..."
  cat "$f" | claude -p "$PROMPT" --output-format text > "pace_batches/raw_${i}.txt" || { echo "  ! lô $i lỗi claude"; continue; }
  python3 - "$i" <<'PY'
import re, sys
i = sys.argv[1]; t = open(f'pace_batches/raw_{i}.txt', encoding='utf-8').read()
m = re.search(r'\{.*\}', t, re.S)
open(f'pace_batches/verdicts_{i}.json', 'w', encoding='utf-8').write(m.group(0) if m else '{}')
PY
done

# 5) Gộp → ghi verdict + điểm/% tháng M vào Mongo (tracker + Infra_Pace_History)
python3 merge_pace_verdicts.py
python3 apply_pace_llm.py --month "$M"
python3 enforce_progress_monotonic.py     # % hoàn thành chỉ tăng (ép đơn điệu sau khi ghi)

echo "==== $(date '+%F %T') XONG (tháng $M) ===="
