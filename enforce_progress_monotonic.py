# -*- coding: utf-8 -*-
"""
enforce_progress_monotonic.py — Ép % HOÀN THÀNH (progress) trong Infra_Pace_History ĐƠN ĐIỆU
theo thời gian cho từng dự án: tiến độ công trình CHỈ TĂNG, dữ liệu nào thấp hơn mức đã đạt
(nhiễu ước lượng LLM theo tháng) → nâng lên bằng mức cao nhất trước đó.

Đồng bộ paceProgress trên tracker = mức % cao nhất (mới nhất sau khi ép). Chạy sau mỗi lần ghi
(apply_pace_llm / apply_retro gọi), và hằng giờ trong deploy.yml để luôn nhất quán.

  python enforce_progress_monotonic.py
"""
import collections

from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

DB = "dc_commodity"


def run(client=None):
    db = (client or MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000))[DB]
    H = db["Infra_Pace_History"]
    by = collections.defaultdict(list)
    for d in H.find({"progress": {"$ne": None}}, {"_id": 1, "tid": 1, "month": 1, "progress": 1}):
        by[d["tid"]].append(d)

    hops, tops, nfix = [], [], 0
    for tid, docs in by.items():
        docs.sort(key=lambda d: d.get("month", ""))
        mx = None
        for d in docs:
            p = d["progress"]
            newp = p if mx is None else max(mx, p)
            mx = newp
            if newp != p:
                hops.append(UpdateOne({"_id": d["_id"]}, {"$set": {"progress": newp}}))
                nfix += 1
        if mx is not None:                           # tracker: % hiện tại = mức cao nhất đã đạt
            tops.append(UpdateOne({"_key": "project", "id": int(tid)},
                                  {"$set": {"paceProgress": int(round(mx))}}))

    if hops:
        H.bulk_write(hops)
    if tops:
        db["Infra_Project_Tracker"].bulk_write(tops)
    print(f"Ép đơn điệu progress: chỉnh {nfix} điểm lịch sử · đồng bộ {len(tops)} tracker")


if __name__ == "__main__":
    run()
