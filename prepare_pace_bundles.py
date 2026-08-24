# -*- coding: utf-8 -*-
"""
prepare_pace_bundles.py — Gom TIN GẦN ĐÂY của mỗi dự án theo dõi thành bundle JSON
để AGENT ĐỌC-HIỂU đánh giá nhịp độ (vượt/chậm/đúng), thay cho regex chỉ-vài-indicator.

Gate: TIÊU ĐỀ chứa alias dự án (tránh gán nhầm) · bỏ nhiễu (kẹt xe/CSGT/tai nạn/mở bán).
Ra: pace_bundles.json = {tid: {name, prov, news:[{date, source, title, desc}], marks:[...]}}
  (tin sắp mới→cũ, tối đa MAXN bài; kèm mốc thi công/hoàn thành đã có để agent tham chiếu.)

  python prepare_pace_bundles.py [--months 10] [--maxn 30] [--out pace_bundles.json]
"""
import argparse
import json
import os
from collections import defaultdict

from pymongo import MongoClient

from lib_db import mongo_uri
from lib_marks import RX_NOISE
from lib_projects import build_alias_regex, pid2tid

HERE = os.path.dirname(os.path.abspath(__file__))
DB = "dc_commodity"


def _minus_months(ym, k):
    y, m = int(ym[:4]), int(ym[5:7])
    t = y * 12 + (m - 1) - k
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def run(months, maxn, out):
    import datetime as dt
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    tr = c[DB]["Infra_Project_Tracker"]
    raw = c["dc_news"]["project_news_raw"]
    p2t = pid2tid()
    t2rx = defaultdict(list)
    for pid, r in build_alias_regex().items():
        if pid in p2t:
            t2rx[p2t[pid]].append(r)

    cutoff = _minus_months(dt.date.today().strftime("%Y-%m"), months)
    bundles = {}
    tr_meta = {p["id"]: p for p in tr.find({"_key": "project"},
               {"id": 1, "name": 1, "loc": 1, "phases": 1, "marks": 1})}
    news_by_tid = defaultdict(list)
    for d in raw.find({"projects": {"$ne": []}},
                      {"title": 1, "description": 1, "date": 1, "source": 1, "projects": 1}):
        ti = d.get("title") or ""
        date = (d.get("date") or "")[:10]
        if len(date) < 7 or date[:7] < cutoff:      # chỉ tin trong khoảng gần đây
            continue
        if RX_NOISE.search(ti):                      # bỏ nhiễu
            continue
        for tid in d.get("projects", []):
            if tid in tr_meta and any(r.search(ti) for r in t2rx.get(tid, [])):
                news_by_tid[tid].append({"date": date, "source": d.get("source", "?"),
                                         "title": ti, "desc": (d.get("description") or "")[:280]})

    for tid, arts in news_by_tid.items():
        seen, uniq = set(), []
        for a in sorted(arts, key=lambda x: x["date"], reverse=True):
            k = a["title"][:80]
            if k in seen:
                continue
            seen.add(k)
            uniq.append(a)
            if len(uniq) >= maxn:
                break
        p = tr_meta[tid]
        phz = [{"kind": ph.get("kind"), "from": ph.get("from"), "to": ph.get("to"),
                "state": ph.get("state")} for ph in p.get("phases", [])]
        dl = [m for m in p.get("marks", []) if m.get("tier") == "deadline"]
        bundles[str(tid)] = {"name": p.get("name", ""), "loc": p.get("loc", ""),
                             "phases": phz, "deadline": dl[-1] if dl else None, "news": uniq}

    with open(os.path.join(HERE, out), "w", encoding="utf-8") as f:
        json.dump(bundles, f, ensure_ascii=False, indent=1)
    n_news = sum(len(b["news"]) for b in bundles.values())
    print(f"Bundle: {len(bundles)} dự án · {n_news} tin (≤{months} tháng, ≤{maxn}/dự án) → {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=10)
    ap.add_argument("--maxn", type=int, default=30)
    ap.add_argument("--out", default="pace_bundles.json")
    a = ap.parse_args()
    run(a.months, a.maxn, a.out)
