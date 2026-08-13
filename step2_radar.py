# -*- coding: utf-8 -*-
"""
step2_radar.py — RADAR dự án mới (deterministic, KHÔNG cần Claude).

Ý tưởng: đọc POOL tin CHƯA khớp dự án (dc_news.unmatched_raw, do step1 để lại khi scrape
16 báo) → trích "LOẠI + Tên Riêng" bằng regex → cái nào ngoài registry = ỨNG VIÊN dự án mới.
Gộp trùng, xếp theo độ nóng (số lần nhắc × số báo × tín hiệu 'tỷ USD/nghìn tỷ').
Ghi dc_commodity.Infra_Project_Candidates.

KHÔNG tự quét feed nữa — dùng chung dòng tin với step1 (chạy SAU step1 trong CI).
Deterministic, không LLM. Bạn liếc bảng candidates, thấy dự án lớn thật thì add_project.py.

  python step2_radar.py            # quét + ghi + in top
  python step2_radar.py --top 40
  python step2_radar.py --dry-run
"""
import argparse
import datetime as dt
import re
from collections import defaultdict

from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri
from lib_projects import build_alias_regex

OUT_DB, OUT_COLL = "dc_commodity", "Infra_Project_Candidates"
POOL_DB, POOL_COLL = "dc_news", "unmatched_raw"   # do step1 để lại (tin chưa khớp dự án)

# loại công trình + TÊN (tên phải là danh từ riêng: nối địa danh "A - B" hoặc "Tên + số/riêng")
# CHỈ hạ tầng giao thông (bỏ điện/nước; 'tàu điện/metro' vẫn giữ vì là giao thông)
TYPE = (r"(cao tốc|đường sắt tốc độ cao|đường sắt|tàu điện|đường vành đai|vành đai|sân bay|"
        r"cảng hàng không|cảng biển|cảng|metro|tuyến metro|hầm đường bộ)")   # bỏ 'cầu' (dính nhu/yêu cầu)
# tên hợp lệ: có nối địa danh (Châu Đốc - Cần Thơ) HOẶC danh từ riêng + số/riêng (Đông Hải 1)
NAME = (r"([A-ZĐÀ-Ỹ][\wÀ-ỹ]+(?:\s+[A-ZĐÀ-Ỹ0-9][\wÀ-ỹ]*)*"
        r"(?:\s*[-–]\s*[A-ZĐÀ-Ỹ][\wÀ-ỹ]+(?:\s+[\wÀ-ỹ]+)*)*)")
RX = re.compile(TYPE + r"\s+" + NAME, re.IGNORECASE)
# tên phải có ≥1 dấu hiệu "riêng": nối địa danh, hoặc có số, hoặc ≥2 từ viết hoa
def is_proper(name):
    if re.search(r"[-–]", name):                       # nối địa danh
        return True
    if re.search(r"\d", name):                         # có số (giai đoạn/tên)
        return True
    caps = re.findall(r"\b[A-ZĐÀ-Ỹ][\wÀ-ỹ]+", name)     # nhiều từ viết hoa
    return len(caps) >= 2

STOP = re.compile(r"\b(trọng điểm|quốc gia|này|đó|các|những|một số|nhiều|khác|đến năm|"
                  r"thời kỳ|giai đoạn|trên tuyến|vùng|khu vực|lớn|mới)\b", re.IGNORECASE)
IMPORTANT = re.compile(r"tỷ\s*USD|nghìn tỷ|\d[\d.,]*\s*tỷ|trọng điểm|quốc gia", re.IGNORECASE)


def norm(s):
    return re.sub(r"\s+", " ", s).strip(" -–.\"'").lower()


def run(dry, top):
    known = build_alias_regex()
    cand = defaultdict(lambda: {"label": "", "count": 0, "sources": set(),
                                "important": False, "samples": []})
    client = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    pool = client[POOL_DB][POOL_COLL]
    n = 0
    for it in pool.find({}, {"title": 1, "description": 1, "source": 1}):
        n += 1
        title = (it.get("title") or "").strip()
        desc = re.sub("<[^>]+>", "", it.get("description") or "")
        src = it.get("source", "?")
        text = title + ". " + desc
        for m in RX.finditer(text):
            label = f"{m.group(1).strip()} {m.group(2).strip()}"
            name = m.group(2).strip()
            if not is_proper(name) or STOP.search(name):
                continue
            if any(rx.search(label) for rx in known.values()):   # đã theo dõi
                continue
            c = cand[norm(label)]
            c["label"] = c["label"] or label
            c["count"] += 1
            c["sources"].add(src)
            if IMPORTANT.search(text):
                c["important"] = True
            if len(c["samples"]) < 2:
                c["samples"].append(title[:80])
    print(f"Đọc {n} tin chưa khớp từ {POOL_DB}.{POOL_COLL}")

    rows = []
    for key, c in cand.items():
        if c["count"] < 2 and len(c["sources"]) < 2:      # bỏ tin nhắc 1 lần (nhiễu one-off)
            continue
        score = c["count"] + 3 * len(c["sources"]) + (4 if c["important"] else 0)
        rows.append({"key": key, "label": c["label"], "count": c["count"],
                     "sources": sorted(c["sources"]), "important": c["important"],
                     "score": score, "samples": c["samples"]})
    rows.sort(key=lambda r: r["score"], reverse=True)

    print(f"Ứng viên dự án mới (nhắc ≥2 lần, ngoài registry): {len(rows)}")
    for r in rows[:top]:
        print(f"  {'★' if r['important'] else ' '} score {r['score']:3} ×{r['count']:2}  {r['label'][:58]}")

    if dry or not rows:
        return
    coll = client[OUT_DB][OUT_COLL]
    coll.create_index("key", unique=True)
    now = dt.datetime.now().isoformat()
    ops = [UpdateOne({"key": r["key"]}, {"$set": {**r, "updated": now}}, upsert=True) for r in rows]
    coll.bulk_write(ops)
    print(f"Đã ghi {len(rows)} ứng viên vào {OUT_DB}.{OUT_COLL}.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(a.dry_run, a.top)
