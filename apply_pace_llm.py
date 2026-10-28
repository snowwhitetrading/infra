# -*- coding: utf-8 -*-
"""
apply_pace_llm.py — Ghi kết quả AGENT ĐỌC-HIỂU (pace_verdicts.json) vào Infra_Project_Tracker:
paceLLM/paceWhyLLM/paceConf + deadlineLLM + tmdtLLM + ownerLLM (đều có nguồn, có validate).

verdicts JSON = {tid: {pace, why, src, confidence, deadline, deadline_src, tmdt, tmdt_src, owner, owner_src}}
Field nào rỗng/null/không hợp lệ → BỎ QUA (không ghi) → step5 tự dùng deterministic fallback.

  python apply_pace_llm.py [--in pace_verdicts.json]
"""
import argparse
import datetime as dt
import json
import os
import re

from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

HERE = os.path.dirname(os.path.abspath(__file__))
DB = "dc_commodity"
_PACE = {"vượt tiến độ", "chậm tiến độ", "đúng tiến độ", ""}
_YM = re.compile(r"^\d{4}-\d{2}$")


def _score_of(v, pace):
    """Điểm nhịp độ -5..+5 (0 = đúng tiến độ). Ưu tiên 'score' do agent cho; thiếu thì suy từ nhãn."""
    s = v.get("score")
    try:
        s = int(round(float(s)))
        return max(-5, min(5, s))
    except (TypeError, ValueError):
        pass
    if "vượt" in pace:
        return 2
    if "chậm" in pace:
        return -3
    if "đúng" in pace:
        return 0
    return None


def run(inp, month=None, hist_only=False):
    with open(os.path.join(HERE, inp), encoding="utf-8") as f:
        verdicts = json.load(f)
    db = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB]
    tr = db["Infra_Project_Tracker"]
    hist = db["Infra_Pace_History"]
    month = month or dt.date.today().strftime("%Y-%m")   # hồi tố: ghi vào tháng được chỉ định
    ops, hops, bad = [], [], 0
    n_dl = n_tmdt = n_own = n_sc = 0
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

        score = _score_of(v, pace)                        # điểm -5..+5, snapshot theo tháng
        if score is not None:
            st["paceScore"] = score
            n_sc += 1
            hops.append(UpdateOne(
                {"tid": int(tid), "month": month},
                {"$set": {"tid": int(tid), "month": month, "score": score,
                          "pace": pace, "why": why, "src": src,
                          "at": dt.datetime.utcnow().isoformat()}}, upsert=True))
        else:
            unset["paceScore"] = ""

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

        if not hist_only:               # hồi tố: CHỈ ghi lịch sử, KHÔNG đè verdict hiện tại trên tracker
            upd = {"$set": st}
            if unset:
                upd["$unset"] = unset   # rỗng → xoá field cũ để không dùng nhầm giá trị lần trước
            ops.append(UpdateOne({"_key": "project", "id": int(tid)}, upd))

    # hồi tố: ghi lịch sử kể cả khi không đụng tracker
    if hist_only:
        if hops:
            hr = hist.bulk_write(hops)
            print(f"Hồi tố {month}: ghi {hr.upserted_count + hr.modified_count} điểm · bỏ (pace sai): {bad}")
        else:
            print(f"Hồi tố {month}: không có điểm hợp lệ")
        return
    if ops:
        r = tr.bulk_write(ops)
        from collections import Counter
        dist = Counter((v.get("pace") or "(chưa đủ căn cứ)") for v in verdicts.values())
        print(f"Ghi LLM: {r.modified_count} dự án · pace {dict(dist)}")
        print(f"  có hạn: {n_dl} · có TMĐT: {n_tmdt} · có chủ đầu tư: {n_own} · có điểm: {n_sc} · bỏ (pace sai): {bad}")
        if hops:
            hr = hist.bulk_write(hops)
            print(f"  lịch sử {month}: ghi {hr.upserted_count + hr.modified_count} điểm → Infra_Pace_History")
    else:
        print("Không có verdict hợp lệ.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="pace_verdicts.json")
    ap.add_argument("--month", default=None, help="HỒI TỐ: ghi điểm vào tháng này (YYYY-MM)")
    ap.add_argument("--hist-only", action="store_true", help="HỒI TỐ: chỉ ghi lịch sử, không đè verdict hiện tại")
    a = ap.parse_args()
    run(a.inp, a.month, a.hist_only)
