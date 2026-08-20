# -*- coding: utf-8 -*-
"""
geocode_tracker.py — Tra toạ độ (lat/lng) cho dự án đang theo dõi (Infra_Project_Tracker)
qua OpenStreetMap Nominatim, để step5 chấm điểm những dự án KHÔNG có tuyến/điểm Cafeland.

Ghi dc_commodity.Infra_Tracker_Geo (tid·lat·lng·q·display·kind). Nominatim giới hạn 1 req/giây.

  python geocode_tracker.py            # geocode dự án chưa có toạ độ
  python geocode_tracker.py --all      # geocode lại tất cả
  python geocode_tracker.py --dry-run
"""
import argparse
import datetime as dt
import re
import time

import httpx
from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri
from step5_build_site import _geo_of

NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = {"User-Agent": "infra-tracker-map/1.0 (project tracking site)"}

RX_PAREN = re.compile(r"\s*\([^)]*\)")                       # bỏ (mở rộng), (Hà Nội)…
RX_PREFIX = re.compile(r"^(dự án\s+|mở rộng\s+|nâng cấp\s+|xây dựng\s+|đầu tư\s+)+", re.I)
RX_SUB = re.compile(r"^(BT\d|Dự án đối ứng|Đài kiểm soát)", re.I)   # cụm con Gia Bình → dùng loc
ABBREV = [(r"\bCHKQT\b", "Sân bay quốc tế"), (r"\bCHK\b", "Sân bay"),
          (r"\bTDTT\b", "thể dục thể thao"), (r"\bKĐT\b", "khu đô thị")]


def _clean(name):
    s = RX_PAREN.sub("", name)
    for pat, rep in ABBREV:
        s = re.sub(pat, rep, s, flags=re.I)
    s = RX_PREFIX.sub("", s).strip()
    return re.sub(r"\s+", " ", s)


def geocode(client, q):
    try:
        r = client.get(NOMINATIM, params={"q": q, "format": "json", "limit": 1,
                                           "countrycodes": "vn"}, headers=UA, timeout=25)
        js = r.json()
        if js:
            return float(js[0]["lat"]), float(js[0]["lon"]), js[0].get("display_name", "")
    except Exception:
        pass
    return None


def queries(name, loc, prov=""):
    """Sinh các chuỗi tra theo thứ tự ưu tiên. `prov` = tỉnh sạch (suy từ _geo_of) để định vị đúng."""
    base = _clean(name)
    out = []
    if RX_SUB.match(name) and prov:                         # cụm con Gia Bình: tra theo tỉnh
        out.append(f"{prov}, Việt Nam")
    # ƯU TIÊN query kèm TỈNH SẠCH để tránh trùng tên ở tỉnh khác (vd Cầu Phong Châu)
    if prov:
        out.append(f"{base}, {prov}, Việt Nam")
        out.append(f"{base.split('-')[0].strip()}, {prov}, Việt Nam")
    out.append(base + ", Việt Nam")
    out.append(base)
    # tuyến 'A - B' → thử 2 đầu tuyến giúp Nominatim định vị điểm giữa
    m = re.search(r"[-–]\s*([^,(]+)$", base)
    if m:
        out.append(m.group(1).strip() + (f", {prov}" if prov else "") + ", Việt Nam")
    seen, uniq = set(), []
    for q in out:
        q = re.sub(r"\s+", " ", q).strip(", ")
        if q and q.lower() not in seen:
            seen.add(q.lower()); uniq.append(q)
    return uniq


def run(a):
    db = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)["dc_commodity"]
    geo = db["Infra_Tracker_Geo"]
    geo.create_index("tid", unique=True)
    have = set() if a.all else {d["tid"] for d in geo.find({"lat": {"$ne": None}}, {"tid": 1})}
    only = {int(x) for x in a.tids.split(",")} if a.tids else None
    projs = [p for p in db["Infra_Project_Tracker"].find(
        {"_key": "project"}, {"_id": 0, "id": 1, "name": 1, "loc": 1})
        if p.get("id") not in have and (only is None or p.get("id") in only)]
    print(f"Cần geocode {len(projs)} dự án" + (" (--all)" if a.all else " (chưa có toạ độ)"), flush=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    ok = 0
    ops = []
    with httpx.Client(follow_redirects=True) as cl:
        for i, p in enumerate(projs, 1):
            res = None
            prov = _geo_of(p.get("name", ""), p.get("loc", ""))[1]
            for q in queries(p.get("name", ""), p.get("loc", ""), prov):
                res = geocode(cl, q)
                time.sleep(1.05)                      # Nominatim: tối đa 1 req/giây
                if res:
                    break
            if res:
                lat, lng, disp = res
                ops.append(UpdateOne({"tid": p["id"]}, {"$set": {
                    "tid": p["id"], "name": p.get("name"), "lat": lat, "lng": lng,
                    "q": q, "display": disp, "geocoded_at": now}}, upsert=True))
                ok += 1
                tag = "OK "
            else:
                tag = "MISS"
            if i % 10 == 0 or i == len(projs):
                print(f"  {i}/{len(projs)} · khớp {ok}", flush=True)
            print(f"   {tag} tid{p['id']} {p.get('name','')[:44]}", flush=True)
    if not a.dry_run and ops:
        geo.bulk_write(ops)
    print(f"XONG: geocode {ok}/{len(projs)} dự án → Infra_Tracker_Geo" + (" [DRY]" if a.dry_run else ""), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--tids", default="", help="CSV tid cần geocode (mặc định: tất cả chưa có)")
    ap.add_argument("--dry-run", action="store_true")
    run(ap.parse_args())
