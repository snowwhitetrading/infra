# -*- coding: utf-8 -*-
"""
seed_pace_history.py — Nạp điểm nhịp độ (-5..+5) THÁNG HIỆN TẠI vào Infra_Pace_History
từ verdict đang có, để lịch sử có dữ liệu ngay (các tháng sau run_pace.sh tự thêm điểm mới).

Điểm = paceScore nếu agent đã cho; chưa có thì suy từ nhãn (giống schedOf):
  vượt=+2 · đúng=0 · chậm=-3 (quá hạn kế hoạch → -4) · chưa có TT/đề xuất → bỏ.

  python seed_pace_history.py
"""
import datetime as dt

from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

DB = "dc_commodity"
TODAY = dt.date.today()
TODAY_Y = TODAY.year + (TODAY.month - 1) / 12


def _toY(s):
    if not s:
        return None
    try:
        y, m = (str(s).split("-") + ["1"])[:2]
        return int(y) + (int(m) - 1) / 12
    except (ValueError, TypeError):
        return None


def sched_of(p):
    """Nhãn nhịp độ giống frontend: paceLLM/paceAuto; thiếu → suy từ lịch thi công dự kiến."""
    if p.get("proposal"):
        return "đang đề xuất"
    pa = p.get("paceLLM") or p.get("paceAuto")
    if pa:
        return pa
    bph = [ph for ph in p.get("phases", []) if ph.get("kind") not in ("gpmb", "swap")]
    froms = [_toY(ph.get("from")) for ph in bph if ph.get("from")]
    tos = [_toY(ph.get("to")) for ph in bph if ph.get("to")]
    froms = [x for x in froms if x is not None]
    tos = [x for x in tos if x is not None]
    started = any(_toY(ph.get("from")) is not None and _toY(ph.get("from")) <= TODAY_Y
                  for ph in p.get("phases", []) if ph.get("from"))
    if started:
        if tos and max(tos) < TODAY_Y:
            return "chậm tiến độ"
        if tos or froms:
            return "đúng tiến độ"
        return "chưa có thông tin"
    if froms and min(froms) < TODAY_Y:
        return "chậm tiến độ"
    return "chưa có thông tin"


def score_of(p, label):
    if p.get("paceScore") is not None:
        try:
            return max(-5, min(5, int(p["paceScore"])))
        except (ValueError, TypeError):
            pass
    if "vượt" in label:
        return 2
    if "chậm" in label:
        bph = [ph for ph in p.get("phases", []) if ph.get("kind") not in ("gpmb", "swap")]
        tos = [_toY(ph.get("to")) for ph in bph if ph.get("to")]
        tos = [x for x in tos if x is not None]
        return -4 if (tos and max(tos) < TODAY_Y) else -3     # quá hạn kế hoạch → nặng hơn
    if "đúng" in label:
        return 0
    return None   # chưa có TT / đề xuất → không chấm điểm


def run():
    db = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB]
    active = {d["tid"] for d in db["Infra_Projects_Registry"].find({"active": True}, {"tid": 1}) if d.get("tid")}
    month = TODAY.strftime("%Y-%m")
    ops = []
    for p in db["Infra_Project_Tracker"].find({"_key": "project"},
              {"id": 1, "paceLLM": 1, "paceAuto": 1, "paceScore": 1, "proposal": 1, "phases": 1}):
        if p["id"] not in active:
            continue
        label = sched_of(p)
        s = score_of(p, label)
        if s is None:
            continue
        # CHỈ điền khi tháng đó CHƯA có điểm (không đè điểm LLM từ run_pace/apply_pace_llm)
        ops.append(UpdateOne({"tid": p["id"], "month": month},
                   {"$setOnInsert": {"tid": p["id"], "month": month, "score": s, "pace": label,
                                     "at": dt.datetime.now(dt.UTC).isoformat(), "seed": True}},
                   upsert=True))
    if ops:
        r = db["Infra_Pace_History"].bulk_write(ops)
        print(f"Seed {month}: {r.upserted_count + r.modified_count} điểm → Infra_Pace_History")
    else:
        print("Không có điểm để seed.")


if __name__ == "__main__":
    run()
