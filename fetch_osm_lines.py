# -*- coding: utf-8 -*-
"""
fetch_osm_lines.py — Bổ sung tuyến hạ tầng cafeland THIẾU, lấy geometry từ OpenStreetMap (Overpass).

Dùng cho tuyến đã hoàn thành/khai thác mà cafeland (chỉ theo dõi dự án đang triển khai) không có,
vd Vành đai 3 Hà Nội. Thêm entry vào CURATED để lấy thêm tuyến.
Ghi dc_commodity.OSM_Infra (title·cat·prov·segments). step5_build_site vẽ chung lớp bản đồ.

  python fetch_osm_lines.py
  python fetch_osm_lines.py --dry-run
"""
import argparse
import datetime as dt

import httpx
from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

OVERPASS = ["https://overpass.private.coffee/api/interpreter",
            "https://overpass-api.de/api/interpreter"]
UA = {"User-Agent": "infra-map/1.0"}

# Tuyến bổ sung: name_re = chuỗi khớp trong tên OSM; exclude = loại bỏ nếu tên chứa các cụm này.
CURATED = [
    {"title": "Đường Vành đai 3 Hà Nội", "cat": "Đường bộ", "prov": "Hà Nội",
     "bbox": "20.95,105.75,21.10,105.95", "name_re": "Vành đai 3",
     "exclude": ["3.5", "đi ", "nhánh"]},
]


def overpass(query):
    for ep in OVERPASS:
        try:
            r = httpx.post(ep, data={"data": query}, timeout=90, headers=UA)
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                return r.json().get("elements", [])
        except Exception:
            continue
    return []


def fetch_one(c):
    q = f'[out:json][timeout:60];way["name"~"{c["name_re"]}"]({c["bbox"]});out geom;'
    els = overpass(q)
    segs, names = [], set()
    for e in els:
        nm = (e.get("tags", {}).get("name") or "")
        low = nm.lower()
        if c["name_re"].lower() not in low:
            continue
        if any(x.lower() in low for x in c.get("exclude", [])):
            continue
        geom = [[g["lat"], g["lon"]] for g in e.get("geometry", []) if g.get("lat")]
        if len(geom) > 1:
            segs.append(geom)
            names.add(nm)
    return segs, names


def run(dry):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    docs = []
    for c in CURATED:
        segs, names = fetch_one(c)
        npts = sum(len(s) for s in segs)
        print(f"  {c['title']}: {len(segs)} đoạn · {npts} điểm · tên OSM: {sorted(names)[:4]}")
        docs.append({"key": c["title"], "title": c["title"], "cat": c["cat"], "prov": c["prov"],
                     "source": "OpenStreetMap", "segments": segs, "n_points": npts})
    if dry:
        return
    col = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)["dc_commodity"]["OSM_Infra"]
    col.create_index("key", unique=True)
    if docs:
        col.bulk_write([UpdateOne({"key": d["key"]}, {"$set": {**d, "fetched_at": now}}, upsert=True)
                        for d in docs])
    print(f"Đã ghi {len(docs)} tuyến vào dc_commodity.OSM_Infra.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    run(ap.parse_args().dry_run)
