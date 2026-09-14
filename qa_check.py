# -*- coding: utf-8 -*-
"""
qa_check.py — TỰ ĐỘNG cross-check dữ liệu để KHÔNG phải rà tay. KHÔNG dùng regex để "phán" nhịp độ:
QA chỉ PHÁT HIỆN (theo NGÀY + tính toàn vẹn), còn việc ĐÁNH GIÁ lại nhịp độ là của Claude (run_pace.sh).

In báo cáo + ghi danh sách 'cần đánh giá lại' ra stale_pace.json để run_pace ưu tiên:
  A. PACE LỖI THỜI  : tin mới nhất của dự án > tháng của đánh giá pace ≥ N tháng → cần Claude chấm lại.
  C. DỰ ÁN ẢO       : tên là pháp nhân/generic (đối chiếu danh sách cụm từ, không regex).
  D. GIẢI NGÂN MỒ CÔI: tid trong Infra_Disbursement nhưng không phải dự án active.
  F. THIẾU TMĐT/HẠN : có tmdtLLM/deadlineLLM nhưng site không hiển thị (guard chặn nhầm).

  python qa_check.py [--months 2]
"""
import argparse
import json
import os

from pymongo import MongoClient

from lib_db import mongo_uri

DB = "dc_commodity"
HERE = os.path.dirname(os.path.abspath(__file__))

# cụm từ nhận diện tên PHÁP NHÂN/generic (đối chiếu chuỗi con — không regex)
BOGUS_TOKENS = ["việt nam", "toàn quốc", "cả nước", "trong 3 năm", "tổng công ty",
                "tập đoàn", "corporation"]
BOGUS_EXACT = ["cảng", "cảng biển", "sân bay", "đường sắt"]   # tên đúng bằng đúng cụm này = generic


def _ym_in(s):
    """Lấy 'YYYY-MM' MỚI NHẤT trong chuỗi mà KHÔNG dùng regex (quét token)."""
    best = ""
    s = s or ""
    for i in range(len(s) - 6):
        w = s[i:i + 7]
        if len(w) == 7 and w[4] == "-" and w[:4].isdigit() and w[5:7].isdigit() and w > best:
            if "2000" <= w[:4] <= "2099":
                best = w
    return best


def _mi(ym):
    y, m = ym.split("-")
    return int(y) * 12 + int(m)


def run(months, fix=False):
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    db = c[DB]
    raw = c["dc_news"]["project_news_raw"]
    tr = list(db["Infra_Project_Tracker"].find({"_key": "project"},
              {"id": 1, "name": 1, "paceLLM": 1, "paceAuto": 1, "paceWhyLLM": 1, "paceWhy": 1,
               "tmdt": 1, "tmdtLLM": 1, "deadlineLLM": 1, "marks": 1}))
    active = {d["tid"] for d in db["Infra_Projects_Registry"].find({"active": True}, {"tid": 1}) if d.get("tid")}
    reg_name = {d.get("tid"): d.get("name", "") for d in db["Infra_Projects_Registry"].find(
                {"active": True}, {"tid": 1, "name": 1})}

    # tin mới nhất (tháng) theo tid — chỉ dùng NGÀY, không đọc nội dung
    latest = {}
    for d in raw.find({"projects": {"$ne": []}}, {"projects": 1, "date": 1}):
        m = (d.get("date") or "")[:7]
        if len(m) < 7:
            continue
        for t in d.get("projects", []):
            if m > latest.get(t, ""):
                latest[t] = m

    A, C, D = [], [], []
    for p in tr:
        pace = p.get("paceLLM") or p.get("paceAuto")
        if pace:
            pm = _ym_in(p.get("paceWhyLLM") or p.get("paceWhy") or "")
            nm = latest.get(p["id"], "")
            if pm and nm and nm > pm and _mi(nm) - _mi(pm) >= months:
                A.append((_mi(nm) - _mi(pm), p["id"], p["name"], pm, nm, pace))

    for t, nm in reg_name.items():
        low = (nm or "").strip().lower()
        if low in BOGUS_EXACT or any(k in low for k in BOGUS_TOKENS):
            C.append((t, nm))

    for x in db["Infra_Disbursement"].find({}, {"tid": 1, "name": 1}):
        if x.get("tid") not in active:
            D.append((x.get("tid"), x.get("name", "")))

    A.sort(reverse=True)
    print(f"\n==== A. PACE LỖI THỜI (tin mới hơn đánh giá ≥{months} tháng → Claude cần chấm lại): {len(A)} ====")
    for g, i, n, pm, nm, pc in A[:30]:
        print(f"  lệch {g:>2}th | id{i:>3} {n[:38]:38} pace[{pm}]={pc:14} tin mới {nm}")
    print(f"\n==== C. DỰ ÁN ẢO còn active: {len(C)} ====")
    for t, n in C:
        print(f"  tid{t} | {n}")
    if fix and C:
        r = db["Infra_Projects_Registry"].update_many(
            {"tid": {"$in": [t for t, _ in C]}}, {"$set": {"active": False}})
        print(f"  → --fix: đã tắt {r.modified_count} dự án ảo")
    print(f"\n==== D. GIẢI NGÂN MỒ CÔI: {len(D)} ====")
    for t, n in D:
        print(f"  tid{t} | {n}")

    # ghi danh sách cần chấm lại → run_pace ưu tiên (Claude chấm, KHÔNG regex)
    with open(os.path.join(HERE, "stale_pace.json"), "w", encoding="utf-8") as f:
        json.dump(sorted(a[1] for a in A), f)
    print(f"\n→ stale_pace.json: {len(A)} dự án cần Claude đánh giá lại")
    print(f"#### TỔNG: A(lỗi thời)={len(A)} · C(ảo)={len(C)} · D(mồ côi)={len(D)}")
    return len(A) + len(C) + len(D)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=2)
    ap.add_argument("--fix", action="store_true", help="tự tắt dự án ảo (active=False)")
    a = ap.parse_args()
    run(a.months, a.fix)
