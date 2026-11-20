#!/usr/bin/env bash
# run_disb_auto.sh — FLOW TỰ ĐỘNG giải ngân: tải báo cáo mới nhất từ mof.gov.vn → render →
# Claude đọc ảnh trích → ghi THẲNG Mongo Infra_Disbursement (append series). Không cần CSV/browser.
# Env: IRIS_MONGO_URI (Mongo) + CLAUDE_CODE_OAUTH_TOKEN (Pro/Max). Dùng trên GitHub Actions hằng tháng.
set -euo pipefail
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")"
echo "==== $(date '+%F %T') · GIẢI NGÂN TỰ ĐỘNG ===="

# 1) Tải báo cáo chi tiết GTVT mới nhất (API mof.gov.vn)
FETCH="$(python3 fetch_disb_report.py --out disbursement_data || true)"
PDF="$(printf '%s' "$FETCH" | cut -f1)"
KY_HINT="$(printf '%s' "$FETCH" | cut -f2)"
TITLE="$(printf '%s' "$FETCH" | cut -f3)"
if [ -z "$PDF" ] || [ ! -f "$PDF" ]; then echo "Không tải được báo cáo mới — dừng."; exit 0; fi
echo "Báo cáo: $TITLE  ($KY_HINT) → $PDF"

# 2) Render trang
python3 prepare_disb_pages.py "$PDF" --out _disb_pages
rm -rf disb_out && mkdir -p disb_out
python3 - <<'PY'
import json
m = json.load(open('_disb_pages/manifest.json', encoding='utf-8')); pg = m['pages']; B = 4
for n, i in enumerate(range(0, len(pg), B)):
    json.dump(pg[i:i+B], open(f'disb_out/batch_{n}.json', 'w', encoding='utf-8'))
print("số lô:", -(-len(pg)//B))
PY

# 3) Mỗi lô: claude -p (Pro/Max) đọc ảnh → JSON
PROMPT="$(cat disb_agent_prompt.txt)"
for f in disb_out/batch_*.json; do
  i=$(basename "$f" | tr -dc '0-9')
  imgs=$(python3 -c "import json;print(chr(10).join('  - '+p for p in json.load(open('$f'))))")
  echo "  → đọc lô $i ..."
  cat /dev/null | claude -p "$PROMPT

CÁC ẢNH TRANG (hãy Read TỪNG file):
$imgs" --allowedTools Read --output-format text > "disb_out/raw_${i}.txt" || { echo "  ! lô $i lỗi"; continue; }
  python3 - "$i" <<'PY'
import re, sys
i = sys.argv[1]; t = open(f'disb_out/raw_{i}.txt', encoding='utf-8').read()
m = re.search(r'\{.*\}', t, re.S)
open(f'disb_out/verdicts_{i}.json', 'w', encoding='utf-8').write(m.group(0) if m else '{}')
PY
done

# 4) Gộp → disb_verdicts.json
python3 - <<'PY'
import glob, json
rows, as_of = [], ""
for f in sorted(glob.glob('disb_out/verdicts_*.json')):
    try: d = json.load(open(f, encoding='utf-8'))
    except Exception: continue
    as_of = as_of or (d.get('as_of') or '')
    rows += d.get('rows', [])
json.dump({'as_of': as_of, 'rows': rows}, open('disb_verdicts.json', 'w', encoding='utf-8'), ensure_ascii=False)
print("gộp:", len(rows), "dòng · as_of", as_of)
PY

# 5) Ghi thẳng Mongo (ky suy từ as_of; dedup theo kỳ)
python3 apply_disb_mongo.py --verdicts disb_verdicts.json
echo "==== $(date '+%F %T') XONG ===="
