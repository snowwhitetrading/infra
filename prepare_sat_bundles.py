# -*- coding: utf-8 -*-
"""
prepare_sat_bundles.py — Chọn ẢNH VỆ TINH đại diện mỗi dự án (để agent thị giác đánh giá diễn biến).
Chỉ lấy dự án ĐANG/ĐÃ khởi công (build phase) có ≥2 ảnh; mỗi dự án ≤6 ảnh: sớm nhất + trải giữa + 2 ảnh rõ mới nhất
(ưu tiên ảnh OK ít mây). Ra sat_bundles.json = {tid: {name, deadline, imgs:[{month,file}]}}.

  python prepare_sat_bundles.py [--maximg 3]
"""
import argparse
import json
import os

from pymongo import MongoClient

from lib_db import mongo_uri

HERE = os.path.dirname(os.path.abspath(__file__))
DB = "dc_commodity"


def _pick(imgs, k):
    """Chọn k ảnh trải đều theo thời gian, ưu tiên ảnh OK (ít mây)."""
    ok = [im for im in imgs if im.get("ok")] or imgs
    if len(ok) <= k:
        return ok
    # LUÔN giữ 2 ảnh rõ MỚI NHẤT (để thấy nhịp thi công gần đây) + ảnh sớm nhất; phần còn lại trải đều ở giữa.
    # Trước đây chỉ 3 ảnh (sớm/giữa/mới) → nhận xét hay dừng ở ảnh cũ, không bắt được giai đoạn tăng tốc gần đây.
    head, tail = ok[:-2], ok[-2:]
    n = max(1, k - 2)
    idx = [round(i * (len(head) - 1) / (n - 1)) for i in range(n)] if n > 1 else [0]
    return [head[i] for i in sorted(set(idx))] + tail


def run(maximg, out="sat_bundles.json"):
    from step5_build_site import fetch_satellite
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    sat = fetch_satellite(c)                       # {str(tid): [{month,date,cloud,ok,file}] sắp theo tháng}
    tr = {p["id"]: p for p in c[DB]["Infra_Project_Tracker"].find(
          {"_key": "project"}, {"id": 1, "name": 1, "phases": 1, "marks": 1})}
    today = __import__("datetime").date.today().strftime("%Y-%m")
    bundles = {}
    for tid_s, imgs in sat.items():
        tid = int(tid_s)
        p = tr.get(tid)
        if not p or len(imgs) < 2:
            continue
        started = any(ph.get("kind") in ("build", "gpmb") and ph.get("from") and ph["from"] <= today
                      for ph in p.get("phases", []))
        if not started:                            # chưa khởi công → ảnh chưa nói được tiến độ thi công
            continue
        chosen = _pick(imgs, maximg)
        dl = [m for m in p.get("marks", []) if m.get("tier") == "deadline"]
        bundles[tid_s] = {"name": p.get("name", ""),
                          "deadline": (dl[-1]["date"] if dl else ""),
                          "imgs": [{"month": im["month"], "file": im["file"], "cloud": im["cloud"]}
                                   for im in chosen]}
    with open(os.path.join(HERE, out), "w", encoding="utf-8") as f:
        json.dump(bundles, f, ensure_ascii=False, indent=1)
    nimg = sum(len(b["imgs"]) for b in bundles.values())
    print(f"Sat bundle: {len(bundles)} dự án đang thi công · {nimg} ảnh (≤{maximg}/dự án) → sat_bundles.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--maximg", type=int, default=6)
    ap.add_argument("--out", default="sat_bundles.json")
    a = ap.parse_args()
    run(a.maximg, a.out)
