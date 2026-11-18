# -*- coding: utf-8 -*-
"""
apply_retro.py — Ghi LỊCH SỬ hồi tố (nhịp độ + % hoàn thành) theo TỪNG THÁNG vào Infra_Pace_History.

Đầu vào: 1+ file JSON (output subagent) dạng NESTED:
  {"<tid>": {"<YYYY-MM>": {"score": int|null, "progress": int|null,
                            "pace": "...", "why": "...", "src": "..."}, ...}, ...}

CHỈ ghi lịch sử (không đụng verdict hiện tại trên tracker), trừ khi --tracker thì đồng bộ
paceScore/paceProgress THÁNG MỚI NHẤT (NOW) lên Infra_Project_Tracker.

  python apply_retro.py retro_out/*.json [--now 2026-11] [--tracker]
"""
import argparse
import datetime as dt
import glob
import json
import re

from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

DB = "dc_commodity"
_YM = re.compile(r"^\d{4}-\d{2}$")
_PACE = {"vượt tiến độ", "chậm tiến độ", "đúng tiến độ", ""}


def _clean_score(s):
    try:
        return max(-5, min(5, int(round(float(s)))))
    except (TypeError, ValueError):
        return None


def _clean_prog(p):
    try:
        if p is None or str(p).strip() == "":
            return None
        return max(0, min(100, int(round(float(p)))))
    except (TypeError, ValueError):
        return None


def run(patterns, now=None, to_tracker=False):
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat))
    if not files:
        print("Không thấy file đầu vào."); return
    db = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB]
    hist = db["Infra_Pace_History"]
    tr = db["Infra_Project_Tracker"]
    now = now or dt.date.today().strftime("%Y-%m")

    merged = {}                                   # {(tid,month): doc} — file sau đè file trước
    bad = n_files = 0
    for f in files:
        n_files += 1
        try:
            data = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ! bỏ {f}: {e}"); continue
        for tid, months in (data or {}).items():
            try:
                tid = int(tid)
            except (TypeError, ValueError):
                continue
            if not isinstance(months, dict):
                continue
            for month, v in months.items():
                if not _YM.match(month or "") or not isinstance(v, dict):
                    continue
                pace = (v.get("pace") or "").strip()
                if pace not in _PACE:
                    bad += 1; continue
                score = _clean_score(v.get("score"))
                prog = _clean_prog(v.get("progress"))
                if score is None and prog is None:
                    continue                      # không có tín hiệu lượng hoá → bỏ
                doc = {"tid": tid, "month": month, "pace": pace,
                       "why": (v.get("why") or "").strip(), "src": (v.get("src") or "").strip(),
                       "retro": True, "at": dt.datetime.now(dt.UTC).isoformat()}
                if score is not None:
                    doc["score"] = score
                if prog is not None:
                    doc["progress"] = prog
                merged[(tid, month)] = doc

    if not merged:
        print(f"Đọc {n_files} file · không có điểm hợp lệ (pace sai: {bad})."); return

    hops = [UpdateOne({"tid": t, "month": m}, {"$set": d}, upsert=True)
            for (t, m), d in merged.items()]
    hr = hist.bulk_write(hops)

    import collections
    ms = collections.Counter(m for (_, m) in merged)
    sc = collections.Counter(d["score"] for d in merged.values() if "score" in d)
    npg = sum(1 for d in merged.values() if "progress" in d)
    print(f"Đọc {n_files} file → {len(merged)} điểm "
          f"(ghi {hr.upserted_count + hr.modified_count}) · pace sai: {bad}")
    print(f"  theo tháng: {dict(sorted(ms.items()))}")
    print(f"  có % hoàn thành: {npg} · phân bố điểm: {dict(sorted(sc.items()))}")

    if to_tracker:                                # đồng bộ THÁNG NOW lên tracker (hiển thị hiện tại)
        tops = []
        for (t, m), d in merged.items():
            if m != now:
                continue
            st = {}
            if "score" in d:
                st["paceScore"] = d["score"]
            if "progress" in d:
                st["paceProgress"] = d["progress"]
            if st:
                tops.append(UpdateOne({"_key": "project", "id": t}, {"$set": st}))
        if tops:
            trr = tr.bulk_write(tops)
            print(f"  đồng bộ tracker ({now}): {trr.modified_count} dự án")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="file(s) JSON nested output subagent")
    ap.add_argument("--now", default=None, help="tháng hiện tại YYYY-MM (đồng bộ lên tracker)")
    ap.add_argument("--tracker", action="store_true", help="đồng bộ score/progress tháng NOW lên tracker")
    a = ap.parse_args()
    run(a.files, a.now, a.tracker)
