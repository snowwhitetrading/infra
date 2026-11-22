#!/usr/bin/env bash
# run_disb_auto.sh — FLOW TỰ ĐỘNG giải ngân: tải báo cáo mới nhất từ mof.gov.vn → render →
# Claude đọc ảnh trích → ghi THẲNG Mongo Infra_Disbursement (append series). Không cần CSV/browser.
# Env: IRIS_MONGO_URI (Mongo) + CLAUDE_CODE_OAUTH_TOKEN (Pro/Max). Dùng trên GitHub Actions hằng tháng.
set -euo pipefail
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")"
echo "==== $(date '+%F %T') · GIẢI NGÂN TỰ ĐỘNG ===="

# 1) Tìm + tải báo cáo kỳ MỚI HƠN kỳ đã nạp, từ NHIỀU NGUỒN (vanban.chinhphu.vn → mof.gov.vn).
#    Không có kỳ mới → dừng ngay (không tốn lượt Claude).
FETCH="$(python3 fetch_disb_report.py --out disbursement_data || true)"
PDF="$(printf '%s' "$FETCH" | cut -f1)"
KY_HINT="$(printf '%s' "$FETCH" | cut -f2)"
TITLE="$(printf '%s' "$FETCH" | cut -f3)"
SRC_URL="$(printf '%s' "$FETCH" | cut -f4)"
SRC_NAME="$(printf '%s' "$FETCH" | cut -f5)"
if [ -z "$PDF" ] || [ ! -f "$PDF" ]; then echo "Không có báo cáo kỳ mới (hoặc không tải được) — dừng."; exit 0; fi
echo "Báo cáo: $TITLE  ($KY_HINT, nguồn $SRC_NAME) → $PDF"

# 2) Render trang (dpi thấp cho bước QUÉT để nhanh/rẻ)
python3 prepare_disb_pages.py "$PDF" --out _disb_pages --dpi 100
rm -rf disb_out && mkdir -p disb_out

# 2b) QUÉT NHANH: lọc ra các trang là BẢNG GTVT theo dự án (bỏ công văn + phụ lục khác) → đỡ đọc thừa
SCAN="$(cat disb_scan_prompt.txt)"
python3 - <<'PY'
import json
m = json.load(open('_disb_pages/manifest.json', encoding='utf-8')); pg = m['pages']; B = 10
for n, i in enumerate(range(0, len(pg), B)):
    json.dump(pg[i:i+B], open(f'disb_out/scan_{n}.json', 'w', encoding='utf-8'))
print("lô quét:", -(-len(pg)//B))
PY
: > disb_out/keep.txt
for f in disb_out/scan_*.json; do
  imgs=$(python3 -c "import json;print(chr(10).join('  - '+p for p in json.load(open('$f'))))")
  claude -p "$SCAN

CÁC ẢNH TRANG (hãy Read TỪNG file):
$imgs" --allowedTools Read --output-format text 2>/dev/null \
    | python3 -c "import sys,re,json;t=sys.stdin.read();m=re.search(r'\{.*\}',t,re.S);print('\n'.join(str(x) for x in (json.loads(m.group(0)).get('keep',[]) if m else [])))" >> disb_out/keep.txt || true
done
KEEP="$(sort -n -u disb_out/keep.txt | tr '\n' ' ')"
echo "Trang BẢNG dự án GTVT: ${KEEP:-(không có)}"
if [ -z "${KEEP// }" ]; then echo "Không tìm thấy bảng Phụ lục GTVT — dừng."; exit 0; fi

# 2c) Render LẠI chỉ các trang cần ở dpi cao (đọc số chính xác)
python3 - "$PDF" "$KEEP" <<'PY'
import pymupdf, sys, os, json
pdf, keep = sys.argv[1], [int(x) for x in sys.argv[2].split()]
d = pymupdf.open(pdf); os.makedirs('_disb_hi', exist_ok=True); pages=[]
for n in keep:
    if 1 <= n <= d.page_count:
        p = f'_disb_hi/page_{n:02d}.png'; d[n-1].get_pixmap(dpi=170).save(p); pages.append(p)
json.dump({'pages':pages}, open('_disb_hi/manifest.json','w',encoding='utf-8'))
print('render hi:', len(pages), 'trang')
PY
python3 - <<'PY'
import json
pg = json.load(open('_disb_hi/manifest.json', encoding='utf-8'))['pages']; B = 4
for n, i in enumerate(range(0, len(pg), B)):
    json.dump(pg[i:i+B], open(f'disb_out/batch_{n}.json', 'w', encoding='utf-8'))
print("lô đọc:", -(-len(pg)//B))
PY

# 3) Mỗi lô: claude -p (Pro/Max) đọc ảnh → JSON. Kèm DANH SÁCH DỰ ÁN ĐANG THEO DÕI để agent tự gán tid
#    (đọc-hiểu tên, thay cho khớp mờ theo token vốn hay gán nhầm).
python3 - <<'PY'
from pymongo import MongoClient
from lib_db import mongo_uri
db = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)["dc_commodity"]
act = {d["tid"] for d in db["Infra_Projects_Registry"].find({"active": True}, {"tid": 1}) if d.get("tid")}
ps = sorted((p["id"], p.get("name", "")) for p in db["Infra_Project_Tracker"].find({"_key": "project"}, {"id": 1, "name": 1})
            if p["id"] in act)
open('disb_out/tracker_list.txt', 'w', encoding='utf-8').write("
".join(f"{i}	{n}" for i, n in ps))
print("danh sách tracker:", len(ps), "dự án")
PY
PROMPT="$(cat disb_agent_prompt.txt)

DANH SÁCH DỰ ÁN ĐANG THEO DÕI (tid<TAB>tên):
$(cat disb_out/tracker_list.txt)"
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
rows, as_of, tong = [], "", None
for f in sorted(glob.glob('disb_out/verdicts_*.json')):
    try: d = json.load(open(f, encoding='utf-8'))
    except Exception: continue
    as_of = as_of or (d.get('as_of') or '')
    tong = tong or d.get('tong')                      # dòng TỔNG SỐ chỉ có ở lô chứa trang đầu phụ lục
    rows += d.get('rows', [])
json.dump({'as_of': as_of, 'tong': tong, 'rows': rows}, open('disb_verdicts.json', 'w', encoding='utf-8'), ensure_ascii=False)
print("gộp:", len(rows), "dòng · as_of", as_of)
PY

# 5) Ghi thẳng Mongo (ky suy từ as_of; dedup theo kỳ)
#    --expect-ky: kỳ đọc trên bảng phải khớp kỳ ghi ở tiêu đề báo cáo, lệch thì DỪNG không ghi.
python3 apply_disb_mongo.py --verdicts disb_verdicts.json --expect-ky "$KY_HINT"   --source "$SRC_NAME" --source-url "$SRC_URL" --source-title "$TITLE"
echo "==== $(date '+%F %T') XONG ===="
