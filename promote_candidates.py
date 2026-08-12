# -*- coding: utf-8 -*-
"""
promote_candidates.py — MỨC C: TỰ ĐỘNG thêm ứng viên đủ mạnh vào registry (khỏi duyệt tay).

Chỉ thêm ứng viên có importance >= MIN (do radar LLM chấm — sạch). Ứng viên regex thô
(không có importance) KHÔNG được tự thêm → tránh rác. Dự án thêm gắn origin='auto'
(dễ nhận biết & gỡ nếu cần).

  python promote_candidates.py            # tự thêm ★★ trở lên
  python promote_candidates.py --min 3    # chỉ tự thêm ★★★ (chặt hơn)
"""
import argparse
import re
from pymongo import MongoClient
from lib_db import mongo_uri

DB = "dc_commodity"


def run(min_imp):
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    reg = c[DB]["Infra_Projects_Registry"]
    cand = c[DB]["Infra_Project_Candidates"]
    tid = max([d.get("tid", 0) or 0 for d in reg.find({}, {"tid": 1})] + [0])
    promoted = 0
    for cd in cand.find({"importance": {"$gte": min_imp}}):
        if not cd.get("aliases"):
            continue
        pid = re.sub(r"[^a-z0-9]+", "_", (cd.get("label") or "").lower()).strip("_")[:40]
        if not pid or reg.find_one({"id": pid}):
            cand.delete_one({"key": cd["key"]})
            continue
        tid += 1
        reg.update_one({"id": pid}, {"$set": {
            "id": pid, "name": cd["label"], "aliases": cd["aliases"], "tid": tid,
            "active": True, "origin": "auto", "group": cd.get("category", "Khác"),
            "importance": cd.get("importance"),
        }}, upsert=True)
        cand.delete_one({"key": cd["key"]})
        promoted += 1
        print(f"  + auto (★{cd.get('importance')}) {cd['label']} (tid={tid})")
    print(f"Tự thêm {promoted} dự án (importance>={min_imp}) vào registry. Lần scrape sau tự có tin.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=2, help="Ngưỡng importance tối thiểu để tự thêm")
    run(ap.parse_args().min)
