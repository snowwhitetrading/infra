# -*- coding: utf-8 -*-
"""
prepare_sat_bundles.py — Chọn ẢNH VỆ TINH đại diện mỗi dự án (để agent thị giác đánh giá diễn biến).
Chỉ lấy dự án ĐANG/ĐÃ khởi công (build phase) có ≥2 ảnh; mỗi dự án ≤3 ảnh: sớm nhất + giữa + mới nhất
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
    idx = [round(i * (len(ok) - 1) / (k - 1)) for i in range(k)]   # sớm nhất, giữa, mới nhất
    return [ok[i] for i in sorted(set(idx))]


def run(maximg):
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
    with open(os.path.join(HERE, "sat_bundles.json"), "w", encoding="utf-8") as f:
        json.dump(bundles, f, ensure_ascii=False, indent=1)
    nimg = sum(len(b["imgs"]) for b in bundles.values())
    print(f"Sat bundle: {len(bundles)} dự án đang thi công · {nimg} ảnh (≤{maximg}/dự án) → sat_bundles.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--maximg", type=int, default=3)
    run(ap.parse_args().maximg)
