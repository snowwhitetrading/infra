# -*- coding: utf-8 -*-
"""
fetch_cafeland.py — Lấy TẤT CẢ data từ map.cafeland.vn (API mở, không cần auth).

Endpoint:
  /ha-tang/get-list-line  → 24 tuyến hạ tầng TP.HCM (polyline coords)  → Cafeland_Infra
  /get-duan?page=N        → ~5.240 dự án BĐS (262 trang × 20)          → Cafeland_Duan
  /get-kcn?page=N         → ~7.600 khu/cụm CN (38 trang × 200)         → Cafeland_KCN
Lưu: cafeland_*.json (soi tay) + dc_commodity.Cafeland_* (dùng/match).

  python fetch_cafeland.py                 # lấy tất cả
  python fetch_cafeland.py --what infra    # chỉ tuyến hạ tầng
  python fetch_cafeland.py --what duan kcn
  python fetch_cafeland.py --dry-run
"""
import argparse
import datetime as dt
import json
import os
import time

import httpx
from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

BASE = "https://map.cafeland.vn"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"}
HERE = os.path.dirname(os.path.abspath(__file__))
DB = "dc_commodity"


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _get(client, path, tries=3):
    for i in range(tries):
        try:
            r = client.get(f"{BASE}/{path}", timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(1.5)
    return None


def fetch_infra(client):
    j = _get(client, "ha-tang/get-list-line")
    out = []
    for x in (j or {}).get("result", []):
        try:
            coors = json.loads(x.get("coors") or "[]")
        except (ValueError, TypeError):
            coors = []
        coords = [[_f(c.get("lat")), _f(c.get("lng"))] for c in coors
                  if _f(c.get("lat")) and _f(c.get("lng"))]
        out.append({"caf_id": x.get("id"), "title": (x.get("title") or "").strip(),
                    "slug": x.get("slug"), "lat": _f(x.get("lat")), "lng": _f(x.get("lng")),
                    "line_type": x.get("line_type"), "line_color": x.get("line_color"),
                    "line_status": x.get("line_status"), "type": x.get("type"),
                    "province_id": x.get("province_id"), "n_points": len(coords),
                    "coords": coords})
    return out, "Cafeland_Infra", "caf_id"


def _paginate(client, path, extract, label):
    """Lấy hết trang: extract(json)->list. Đọc totalPage ở trang 1."""
    first = _get(client, f"{path}?page=1")
    if not first:
        return []
    total = int(first.get("totalPage") or 1)
    out = list(extract(first))
    for pg in range(2, total + 1):
        j = _get(client, f"{path}?page={pg}")
        if j:
            out.extend(extract(j))
        if pg % 20 == 0 or pg == total:
            print(f"    {label}: {pg}/{total} trang · {len(out)} bản ghi")
        time.sleep(0.25)
    return out


def fetch_duan(client):
    def ex(j):
        r = []
        for x in (j.get("result") or {}).get("list_duan", []):
            r.append({"caf_id": x.get("duan_id"), "title": (x.get("title") or "").strip(),
                      "alias": x.get("alias"), "address": x.get("address"),
                      "lat": _f(x.get("lat")), "lng": _f(x.get("lng")),
                      "type_name": x.get("type_name"), "status_name": x.get("status_name"),
                      "price_min": x.get("price_min"), "price_m2": x.get("price_m2"),
                      "company": x.get("company_name"), "url": x.get("urlduan"),
                      "city": x.get("city_name"), "district": x.get("district_name"),
                      "ward": x.get("ward_name")})
        return r
    return _paginate(client, "get-duan", ex, "duan"), "Cafeland_Duan", "caf_id"


def fetch_kcn(client):
    def ex(j):
        r = []
        for x in (j.get("result") or []):
            r.append({"caf_id": x.get("id"), "title": (x.get("title") or "").strip(),
                      "alias": x.get("alias"), "address": x.get("addressfull") or x.get("address"),
                      "lat": _f(x.get("lat")), "lng": _f(x.get("lng")),
                      "acreage": x.get("acreage"), "occupancy": x.get("occupancy"),
                      "price": x.get("price"), "status": x.get("icon_status_name"),
                      "url": x.get("url")})
        return r
    return _paginate(client, "get-kcn", ex, "kcn"), "Cafeland_KCN", "caf_id"


JOBS = {"infra": fetch_infra, "duan": fetch_duan, "kcn": fetch_kcn}


def run(what, dry):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    mc = None if dry else MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    with httpx.Client(headers=UA, follow_redirects=True) as client:
        for name in what:
            print(f"\n== {name} ==")
            docs, coll, key = JOBS[name](client)
            print(f"  Tổng {len(docs)} bản ghi.")
            if dry or not docs:
                continue
            with open(os.path.join(HERE, f"cafeland_{name}.json"), "w", encoding="utf-8") as f:
                json.dump(docs, f, ensure_ascii=False)
            col = mc[DB][coll]
            col.create_index(key, unique=True)
            col.bulk_write([UpdateOne({key: d[key]}, {"$set": {**d, "fetched_at": now}},
                                      upsert=True) for d in docs if d.get(key) is not None])
            print(f"  → cafeland_{name}.json + {DB}.{coll} ({col.estimated_document_count()} doc)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--what", nargs="*", default=["infra", "duan", "kcn"],
                    choices=list(JOBS), help="loại data (mặc định: tất cả)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(a.what, a.dry_run)
