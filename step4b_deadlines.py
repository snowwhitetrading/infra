# -*- coding: utf-8 -*-
"""
step4b_deadlines.py — HẠN hoàn thành lấy TỪ TIN (deterministic, có nguồn), KHÔNG để LLM bịa.

Quét dc_news.project_news_raw: với mỗi dự án, lấy HẠN từ TIN MỚI NHẤT có nêu hạn hoàn thành
(gate: TIÊU ĐỀ phải chứa alias dự án → tránh gán nhầm hạn của dự án khác trong cùng bài).
Ghi vào Infra_Project_Tracker:
  - 1 mark tier='deadline' (có src) tại tháng hạn — nguồn để đối chiếu.
  - đặt phase 'build' cuối .to = hạn đó (thanh Gantt kết thúc ở hạn CÓ NGUỒN).
Idempotent: mỗi lần XOÁ mark tier='deadline' cũ rồi ghi lại → chạy hàng giờ trong CI.

  python step4b_deadlines.py
"""
import datetime as dt
from collections import defaultdict

from pymongo import MongoClient

from lib_db import mongo_uri
from lib_marks import deadline_month
from lib_projects import build_alias_regex, pid2tid

DB = "dc_commodity"


def run():
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    tr = c[DB]["Infra_Project_Tracker"]
    raw = c["dc_news"]["project_news_raw"]
    p2t = pid2tid()
    t2rx = defaultdict(list)
    for pid, r in build_alias_regex().items():
        if pid in p2t:
            t2rx[p2t[pid]].append(r)

    # HẠN từ tin mới nhất mỗi dự án (gate tiêu-đề chứa alias)
    best = {}                      # tid -> (article_date, deadline_month, source)
    for d in raw.find({"projects": {"$ne": []}},
                      {"title": 1, "description": 1, "date": 1, "source": 1, "projects": 1}):
        pub = (d.get("date") or "")[:7]
        ti = d.get("title") or ""
        if len(pub) < 7:
            continue
        dl = deadline_month(ti + " " + (d.get("description") or ""), pub)
        if not dl:
            continue
        adate = d.get("date", "")
        for tid in d.get("projects", []):
            if any(r.search(ti) for r in t2rx.get(tid, [])):     # TIÊU ĐỀ về đúng dự án
                if tid not in best or adate > best[tid][0]:
                    best[tid] = (adate, dl, d.get("source", "?"))

    today = dt.date.today().strftime("%Y-%m")
    n_mark = n_phase = 0
    for tid, (adate, dl, src) in best.items():
        p = tr.find_one({"_key": "project", "id": tid})
        if not p:
            continue
        marks = [m for m in p.get("marks", []) if m.get("tier") != "deadline"]   # bỏ hạn cũ (idempotent)
        marks.append({"date": dl, "type": "ms", "tier": "deadline",
                      "label": "Hạn dự kiến hoàn thành (theo tin)", "src": f"{src} · {adate[:7]}"})
        phases = p.get("phases", [])
        builds = [ph for ph in phases if ph.get("kind") == "build"]
        if builds and dl > (builds[-1].get("from") or "0"):      # đặt hạn thanh build cuối
            builds[-1]["to"] = dl
            if dl <= today:                                       # đã tới hạn mà còn thi công → đang làm
                builds[-1]["state"] = "ongoing"
            n_phase += 1
        tr.update_one({"_key": "project", "id": tid}, {"$set": {"marks": marks, "phases": phases}})
        n_mark += 1

    # dọn mark 'deadline' ở dự án KHÔNG còn tin hạn (tránh sót hạn cũ)
    stale = tr.update_many({"_key": "project", "id": {"$nin": list(best)},
                            "marks.tier": "deadline"},
                           {"$pull": {"marks": {"tier": "deadline"}}}).modified_count
    print(f"Hạn từ tin: {len(best)} dự án · ghi mark {n_mark} · đặt phase.to {n_phase} · dọn hạn cũ {stale}")


if __name__ == "__main__":
    run()
