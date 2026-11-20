#!/usr/bin/env bash
# run_disb.sh — Cập nhật GIẢI NGÂN từ 1 PDF Phụ lục III (Bộ Tài chính) bằng Claude Code (Pro/Max).
#   render trang (PyMuPDF) → claude -p đọc ảnh trích số → merge → apply_disb (CSV + Mongo).
#   bash run_disb.sh disbursement_data/8M2026-pages.pdf
set -euo pipefail
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")"

PDF="${1:?Cần đường dẫn PDF, vd disbursement_data/8M2026-pages.pdf}"
OUT="_disb_pages"
echo "==== $(date '+%F %T') · GIẢI NGÂN từ $(basename "$PDF") ===="

python3 prepare_disb_pages.py "$PDF" --out "$OUT"

rm -rf disb_out && mkdir -p disb_out
python3 - <<'PY'
import json, os
m = json.load(open('_disb_pages/manifest.json', encoding='utf-8'))
pg = m['pages']; B = 4                               # 4 trang / lô
for n, i in enumerate(range(0, len(pg), B)):
    json.dump(pg[i:i+B], open(f'disb_out/batch_{n}.json', 'w', encoding='utf-8'))
print("số lô:", -(-len(pg)//B))
PY

PROMPT="$(cat disb_agent_prompt.txt)"
for f in disb_out/batch_*.json; do
  i=$(basename "$f" | tr -dc '0-9')
  imgs=$(python3 -c "import json,sys;print(chr(10).join('  - '+p for p in json.load(open('$f'))))")
  echo "  → đọc lô $i ..."
  claude -p "$PROMPT

CÁC ẢNH TRANG (hãy Read TỪNG file):
$imgs" --allowedTools Read --output-format text > "disb_out/raw_${i}.txt" || { echo "  ! lô $i lỗi"; continue; }
  python3 - "$i" <<'PY'
import re, sys
i = sys.argv[1]; t = open(f'disb_out/raw_{i}.txt', encoding='utf-8').read()
m = re.search(r'\{.*\}', t, re.S)
open(f'disb_out/verdicts_{i}.json', 'w', encoding='utf-8').write(m.group(0) if m else '{}')
PY
done

# gộp các lô: union rows + lấy as_of đầu tiên có
python3 - <<'PY'
import glob, json
rows, as_of = [], ""
for f in sorted(glob.glob('disb_out/verdicts_*.json')):
    try:
        d = json.load(open(f, encoding='utf-8'))
    except Exception:
        continue
    as_of = as_of or (d.get('as_of') or '')
    rows += d.get('rows', [])
json.dump({'as_of': as_of, 'rows': rows}, open('disb_verdicts.json', 'w', encoding='utf-8'), ensure_ascii=False)
print("gộp:", len(rows), "dòng · as_of", as_of)
PY

python3 apply_disb.py --verdicts disb_verdicts.json --manifest "$OUT/manifest.json"
echo "==== $(date '+%F %T') XONG $(basename "$PDF") ===="
