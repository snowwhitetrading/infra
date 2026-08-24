# -*- coding: utf-8 -*-
"""
apply_pace_llm.py — Ghi ĐÁNH GIÁ NHỊP ĐỘ do AGENT ĐỌC-HIỂU tạo (pace_verdicts.json)
vào Infra_Project_Tracker: paceLLM / paceWhyLLM / paceSrcLLM.

verdicts JSON = {tid: {"pace": "vượt tiến độ|chậm tiến độ|đúng tiến độ|", "why": "...",
                       "src": "nguồn ngày", "confidence": "cao|vừa|thấp"}}
  pace = "" (rỗng) nghĩa là CHƯA ĐỦ CĂN CỨ → không phán (web tự về 'đang/chưa thi công').

step5 ưu tiên paceLLM > paceAuto. Chạy sau khi agent ghi pace_verdicts.json.

  python apply_pace_llm.py [--in pace_verdicts.json]
"""
import argparse
import json
import os

from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

HERE = os.path.dirname(os.path.abspath(__file__))
DB = "dc_commodity"
_VALID = {"vượt tiến độ", "chậm tiến độ", "đúng tiến độ", ""}


def run(inp):
    with open(os.path.join(HERE, inp), encoding="utf-8") as f:
        verdicts = json.load(f)
    tr = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB]["Infra_Project_Tracker"]
    ops, bad = [], 0
    for tid, v in verdicts.items():
        pace = (v.get("pace") or "").strip()
        if pace not in _VALID:
            bad += 1
            continue
        why = (v.get("why") or "").strip()
        src = (v.get("src") or "").strip()
        why_full = why + (f" (theo {src})" if src and pace else "")
        ops.append(UpdateOne({"_key": "project", "id": int(tid)},
                             {"$set": {"paceLLM": pace, "paceWhyLLM": why_full,
                                       "paceConf": v.get("confidence", "")}}))
    if ops:
        r = tr.bulk_write(ops)
        from collections import Counter
        dist = Counter((v.get("pace") or "(chưa đủ căn cứ)") for v in verdicts.values())
        print(f"Ghi paceLLM: {r.modified_count} dự án · bỏ {bad} bản ghi sai định dạng")
        print("  phân bố:", dict(dist))
    else:
        print("Không có verdict hợp lệ.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="pace_verdicts.json")
    run(ap.parse_args().inp)
