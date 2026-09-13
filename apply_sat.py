# -*- coding: utf-8 -*-
"""apply_sat.py — Ghi đánh giá VỆ TINH (sat_verdicts.json) vào Infra_Project_Tracker:
satObs (mô tả), satTrend (tiến triển|ít thay đổi|chưa khởi công|không rõ), satConf, satAt (ngày chạy).
  python apply_sat.py [--in sat_verdicts.json]"""
import argparse
import datetime as dt
import json
import os

from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

HERE = os.path.dirname(os.path.abspath(__file__))
DB = "dc_commodity"
_TREND = {"tiến triển", "ít thay đổi", "chưa khởi công", "không rõ"}


def run(inp):
    verdicts = json.load(open(os.path.join(HERE, inp), encoding="utf-8"))
    tr = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB]["Infra_Project_Tracker"]
    today = dt.date.today().strftime("%Y-%m-%d")
    ops = []
    for tid, v in verdicts.items():
        obs = (v.get("satObs") or "").strip()
        trend = (v.get("satTrend") or "").strip()
        if not obs or trend not in _TREND:
            continue
        ops.append(UpdateOne({"_key": "project", "id": int(tid)},
                   {"$set": {"satObs": obs, "satTrend": trend,
                             "satConf": v.get("satConf", ""), "satAt": today}}))
    if ops:
        r = tr.bulk_write(ops)
        from collections import Counter
        print(f"Ghi vệ tinh: {r.modified_count} dự án · trend",
              dict(Counter(v.get("satTrend", "?") for v in verdicts.values())))
    else:
        print("Không có verdict vệ tinh hợp lệ.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="sat_verdicts.json")
    run(ap.parse_args().inp)
