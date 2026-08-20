# -*- coding: utf-8 -*-
"""
fetch_osm_tracker_lines.py — Lấy polyline OSM cho dự án TUYẾN (cao tốc/đường sắt/metro)
đang theo dõi mà KHÔNG có tuyến Cafeland. Dùng điểm geocode (Infra_Tracker_Geo) làm tâm bbox,
khớp way theo token đầu-cuối tuyến. Ghi dc_commodity.OSM_Infra (key='trk:<tid>', kèm tid).
step5_build_site vẽ chung lớp tuyến; khối khớp alias tự gắn pid.

  python fetch_osm_tracker_lines.py --dry-run
  python fetch_osm_tracker_lines.py
"""
import argparse
import datetime as dt
import re
import unicodedata

import httpx
from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri
from step5_build_site import categorize

OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]
UA = {"User-Agent": "infra-tracker-map/1.0"}
LINE_CATS = {"Đường bộ", "Đường sắt/Metro"}
RX_CLEAN = re.compile(r"\([^)]*\)|cao tốc|đường sắt|tốc độ cao|đô thị|metro|tuyến|số\s*\d+|"
                      r"mở rộng|nâng cấp|dự án|đoạn|nhẹ|lrt|\bnối dài\b|mrt", re.I)


def _norm(s):
    return unicodedata.normalize("NFC", (s or "").strip()).lower()


def endpoints(name):
    """Trích token đầu-cuối tuyến (2 địa danh quanh dấu '-')."""
    core = RX_CLEAN.sub(" ", name)
    parts = [p.strip(" -–,") for p in re.split(r"[-–]", core) if p.strip(" -–,")]
    return [p for p in parts if len(p) >= 3][:3]


def overpass(query):
    for ep in OVERPASS:
        try:
            r = httpx.post(ep, data={"data": query}, timeout=90, headers=UA)
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                return r.json().get("elements", [])
        except Exception:
            continue
    return []


def fetch_line(name, cat, lat, lng, span=1.3):
    toks = endpoints(name)
    if not toks:
        return [], []
    bb = f"{lat-span},{lng-span},{lat+span},{lng+span}"
    kind = 'railway~"rail|subway|light_rail|construction"' if cat == "Đường sắt/Metro" \
        else 'highway~"motorway|trunk|primary"'
    # OSM name phải chứa ÍT NHẤT 1 token đầu-cuối (khớp lỏng để bắt được, lọc lại bên dưới)
    alt = "|".join(re.escape(t) for t in toks)
    q = f'[out:json][timeout:60];way[{kind}]["name"~"{alt}",i]({bb});out geom;'
    els = overpass(q)
    ntoks = [_norm(t) for t in toks]
    segs, names = [], set()
    for e in els:
        nm = e.get("tags", {}).get("name", "")
        low = _norm(nm)
        # GIỮ way khớp ≥2 token (tuyến 2 đầu) HOẶC ≥1 token nếu tuyến chỉ 1 địa danh đặc trưng
        hit = sum(1 for t in ntoks if t in low)
        if hit < min(2, len(ntoks)):
            continue
        geom = [[g["lat"], g["lon"]] for g in e.get("geometry", []) if g.get("lat")]
        if len(geom) > 1:
            segs.append(geom); names.add(nm)
    return segs, sorted(names)


def run(dry):
    db = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)["dc_commodity"]
    geo = {d["tid"]: d for d in db["Infra_Tracker_Geo"].find({"lat": {"$ne": None}}, {"_id": 0})}
    projs = []
    for p in db["Infra_Project_Tracker"].find(
            {"_key": "project"}, {"_id": 0, "id": 1, "name": 1, "loc": 1}):
        p["g"] = categorize(p.get("name", ""), p.get("loc", ""))
        if p["g"] in LINE_CATS and p.get("id") in geo:
            projs.append(p)
    print(f"{len(projs)} dự án tuyến có điểm geocode → thử lấy polyline OSM", flush=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    docs, nhit = [], 0
    for p in projs:
        g = geo[p["id"]]
        segs, names = fetch_line(p["name"], p["g"], g["lat"], g["lng"])
        npts = sum(len(s) for s in segs)
        if segs:
            nhit += 1
            docs.append({"key": f"trk:{p['id']}", "tid": p["id"], "title": p["name"],
                         "cat": p["g"], "prov": g.get("prov") or "", "source": "OpenStreetMap",
                         "segments": segs, "n_points": npts})
        print(f"  {'OK ' if segs else 'MISS'} tid{p['id']:<4} {p['name'][:40]:40} "
              f"{len(segs)} đoạn/{npts}đ · OSM:{names[:2]}", flush=True)
    print(f"Có polyline: {nhit}/{len(projs)}", flush=True)
    if dry or not docs:
        return
    col = db["OSM_Infra"]
    col.create_index("key", unique=True)
    col.bulk_write([UpdateOne({"key": d["key"]}, {"$set": {**d, "fetched_at": now}}, upsert=True)
                    for d in docs])
    print(f"Đã ghi {len(docs)} tuyến vào OSM_Infra.", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    run(ap.parse_args().dry_run)
