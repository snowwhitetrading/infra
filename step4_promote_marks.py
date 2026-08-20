# -*- coding: utf-8 -*-
"""
step4_promote_marks.py — Tự động biến tin cột mốc (Infra_Newsflow) thành MARK trên Gantt.

KHÔNG cần người duyệt, KHÔNG cần AI. Mark gắn tier='auto' → hiển thị MỜ trên web,
lọc ẩn được bằng bộ lọc độ tin cậy. Mark curated (tier khác 'auto') KHÔNG bị đụng.

Idempotent: mỗi lần chạy XOÁ hết mark tier='auto' rồi ghi lại từ Infra_Newsflow hiện tại
→ chạy daily trong CI, không sinh trùng.

  python step4_promote_marks.py
"""
import re
from collections import defaultdict
from pymongo import MongoClient
from lib_db import mongo_uri
from lib_marks import infer_type

DB = "dc_commodity"
TRACKER = "Infra_Project_Tracker"
NF = "Infra_Newsflow"


def norm(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()[:50]


def run():
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    tr = c[DB][TRACKER]
    nf = c[DB][NF]

    by_tid = defaultdict(list)
    for doc in nf.find({}):
        month = (doc.get("date") or "")[:7]
        if len(month) < 7:
            continue
        title = doc.get("title", "")
        for tid in doc.get("projects", []):
            by_tid[tid].append({
                "date": month, "type": infer_type("", title), "label": title,
                "tier": "auto", "src": (doc.get("source", "?")) + " · tự động",
                "url": doc.get("url", ""),
            })

    total = 0
    for p in tr.find({"_key": "project"}):
        tid = p["id"]
        cur_marks = [m for m in p.get("marks", []) if m.get("tier") != "auto"]
        # tránh trùng với mốc curated/AI cùng tháng + nhãn na ná
        curated = {(m["date"][:7], norm(m.get("label"))) for m in cur_marks}
        # chỉ THÊM mốc MỚI HƠN timeline curated/AI (không lặp lại lịch sử đã tổng hợp)
        latest = max((m["date"][:7] for m in cur_marks), default="")
        seen, fresh = set(), []
        for m in by_tid.get(tid, []):
            if latest and m["date"] <= latest:
                continue
            k = (m["date"], norm(m["label"]))
            if k in seen or k in curated:
                continue
            seen.add(k)
            fresh.append(m)
        # reset auto cũ, ghi lại (không đụng curated)
        tr.update_one({"_key": "project", "id": tid},
                      {"$pull": {"marks": {"tier": "auto"}}})
        if fresh:
            tr.update_one({"_key": "project", "id": tid},
                          {"$push": {"marks": {"$each": fresh}}})
        total += len(fresh)

    print(f"Đã ghi {total} mark tier='auto' vào {TRACKER} "
          f"(xoá auto cũ, giữ nguyên curated). Chạy step5_build_site.py để cập nhật web.")


if __name__ == "__main__":
    run()
