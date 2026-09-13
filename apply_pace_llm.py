# -*- coding: utf-8 -*-
"""
apply_pace_llm.py — Ghi kết quả AGENT ĐỌC-HIỂU (pace_verdicts.json) vào Infra_Project_Tracker:
paceLLM/paceWhyLLM/paceConf + deadlineLLM + tmdtLLM + ownerLLM (đều có nguồn, có validate).

verdicts JSON = {tid: {pace, why, src, confidence, deadline, deadline_src, tmdt, tmdt_src, owner, owner_src}}
Field nào rỗng/null/không hợp lệ → BỎ QUA (không ghi) → step5 tự dùng deterministic fallback.

  python apply_pace_llm.py [--in pace_verdicts.json]
"""
import argparse
import json
import os
import re

from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

HERE = os.path.dirname(os.path.abspath(__file__))
DB = "dc_commodity"
_PACE = {"vượt tiến độ", "chậm tiến độ", "đúng tiến độ", ""}
_YM = re.compile(r"^\d{4}-\d{2}$")


def run(inp):
    with open(os.path.join(HERE, inp), encoding="utf-8") as f:
        verdicts = json.load(f)
    tr = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB]["Infra_Project_Tracker"]
    ops, bad = [], 0
    n_dl = n_tmdt = n_own = 0
    for tid, v in verdicts.items():
        pace = (v.get("pace") or "").strip()
        if pace not in _PACE:
            bad += 1
            continue
        st = {"paceLLM": pace, "paceConf": v.get("confidence", "")}
        why = (v.get("why") or "").strip()
        src = (v.get("src") or "").strip()
        st["paceWhyLLM"] = why + (f" (theo {src})" if src and pace else "")
        unset = {}

        dl = (v.get("deadline") or "").strip()
        if _YM.match(dl):
            st["deadlineLLM"] = dl
            st["deadlineLLMSrc"] = (v.get("deadline_src") or "").strip()
            n_dl += 1
        else:
            unset["deadlineLLM"] = ""; unset["deadlineLLMSrc"] = ""

        tm = v.get("tmdt")
        try:
            tm = int(tm) if tm is not None and str(tm).strip() != "" else None
        except (ValueError, TypeError):
            tm = None
        if tm is not None and 50 <= tm <= 5_000_000:
            st["tmdtLLM"] = tm
            st["tmdtLLMSrc"] = (v.get("tmdt_src") or "").strip()
            n_tmdt += 1
        else:
            unset["tmdtLLM"] = ""; unset["tmdtLLMSrc"] = ""

        own = (v.get("owner") or "").strip()
        if own:
            st["ownerLLM"] = own
            st["ownerLLMSrc"] = (v.get("owner_src") or "").strip()
            n_own += 1
        else:
            unset["ownerLLM"] = ""; unset["ownerLLMSrc"] = ""

        upd = {"$set": st}
        if unset:
            upd["$unset"] = unset       # rỗng → xoá field cũ để không dùng nhầm giá trị lần trước
        ops.append(UpdateOne({"_key": "project", "id": int(tid)}, upd))

    if ops:
        r = tr.bulk_write(ops)
        from collections import Counter
        dist = Counter((v.get("pace") or "(chưa đủ căn cứ)") for v in verdicts.values())
        print(f"Ghi LLM: {r.modified_count} dự án · pace {dict(dist)}")
        print(f"  có hạn: {n_dl} · có TMĐT: {n_tmdt} · có chủ đầu tư: {n_own} · bỏ (pace sai): {bad}")
    else:
        print("Không có verdict hợp lệ.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="pace_verdicts.json")
    run(ap.parse_args().inp)
