# -*- coding: utf-8 -*-
"""
fetch_cafeland.py — Lấy tuyến hạ tầng từ map.cafeland.vn (API mở, không cần auth).

Nguồn: https://map.cafeland.vn/ha-tang/get-list-line — 24 tuyến hạ tầng khu TP.HCM,
mỗi tuyến có polyline toạ độ thật (coors), loại, màu, trạng thái.
Đích:  cafeland_infra.json (để soi) + dc_commodity.Cafeland_Infra (để match/dùng).

  python fetch_cafeland.py
  python fetch_cafeland.py --dry-run
"""
import argparse
import datetime as dt
import json
import os

import httpx
from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

BASE = "https://map.cafeland.vn"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"}
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DB, OUT_COLL = "dc_commodity", "Cafeland_Infra"


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_lines():
    r = httpx.get(f"{BASE}/ha-tang/get-list-line", headers=UA, timeout=30)
    r.raise_for_status()
    out = []
    for x in r.json().get("result", []):
        try:
            coors = json.loads(x.get("coors") or "[]")
        except (ValueError, TypeError):
            coors = []
        coords = [[_f(c.get("lat")), _f(c.get("lng"))] for c in coors
                  if _f(c.get("lat")) and _f(c.get("lng"))]
        out.append({
            "caf_id": x.get("id"), "title": (x.get("title") or "").strip(),
            "slug": x.get("slug"), "lat": _f(x.get("lat")), "lng": _f(x.get("lng")),
            "line_type": x.get("line_type"), "line_color": x.get("line_color"),
            "line_stroke": x.get("line_stroke"), "line_status": x.get("line_status"),
            "type": x.get("type"), "province_id": x.get("province_id"),
            "n_points": len(coords), "coords": coords,
        })
    return out


def run(dry):
    docs = fetch_lines()
    print(f"Lấy được {len(docs)} tuyến hạ tầng cafeland:")
    for d in sorted(docs, key=lambda x: -x["n_points"]):
        print(f"  caf{d['caf_id']:>4} · {d['n_points']:>3} điểm · type={d['line_type']} "
              f"status={d['line_status']} · {d['title'][:44]}")
    if dry:
        return
    with open(os.path.join(HERE, "cafeland_infra.json"), "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=1)
    col = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[OUT_DB][OUT_COLL]
    col.create_index("caf_id", unique=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    col.bulk_write([UpdateOne({"caf_id": d["caf_id"]}, {"$set": {**d, "fetched_at": now}},
                              upsert=True) for d in docs])
    print(f"\nĐã ghi cafeland_infra.json + {OUT_DB}.{OUT_COLL} ({col.estimated_document_count()} doc)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    run(ap.parse_args().dry_run)
