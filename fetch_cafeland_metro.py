# -*- coding: utf-8 -*-
"""
fetch_cafeland_metro.py — Lấy HÌNH TUYẾN METRO từ lớp "Quy hoạch › Giao thông" của map.cafeland.vn
(/api/giaothong, dữ liệu GIS quy hoạch; hiện phủ mạng metro TP.HCM + vùng phụ cận).

Lớp "tuyến hạ tầng" (fetch_cafeland.py → Cafeland_Lines) chỉ có vài tuyến metro đang làm; lớp quy hoạch
có đủ mạng lưới (số 1–10, 12, Bình Dương, Thủ Thiêm – Long Thành...). Script này:
  • lọc feature loai_tuy = Metro, route_key GISXD_* (bản GIS chi tiết), gộp theo route_key;
  • gắn tid (dự án đang theo dõi) theo bảng TID_OF — tuyến chưa theo dõi vẫn vẽ lên bản đồ (không pid);
  • bỏ tuyến đã có trong Cafeland_Lines (SKIP) để khỏi vẽ trùng;
  • ghi dc_commodity.Cafeland_Metro (key = route_key) + dời ghim Infra_Tracker_Geo về điểm giữa tuyến.

  python fetch_cafeland_metro.py            # lấy + ghi
  python fetch_cafeland_metro.py --dry-run
"""
import argparse
import datetime as dt
import math
import re

import httpx
from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

URL = "https://map.cafeland.vn/api/giaothong"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"}
DB = "dc_commodity"
PROVS = [("tp-ho-chi-minh", {"is_new": "1"}, "Hồ Chí Minh")]

# route_key → tid dự án trong registry (None = chỉ vẽ bản đồ)
TID_OF = {"GISXD_H3": 1262, "GISXD_H4": 27, "GISXD_H5": 1290, "GISXD_H6a": 1291, "GISXD_B1": 34,
          "GISXD_H7a": 1292, "GISXD_H7b": 1292, "GISXD_H8": 1293, "GISXD_H9": 1294, "GISXD_H10": 1295}
# đã có hình trong Cafeland_Lines (Metro 1, 2/2A, Bến Thành – Cần Giờ, Thủ Thiêm – Long Thành)
SKIP = {"GISXD_H1a", "GISXD_H2a", "GISXD_H12", "GISXD_TLT"}
# tên hiển thị (mặc định: ten_tuyen + " TP.HCM")
TITLE = {"GISXD_B1": "Metro TP.HCM - Bình Dương (TP mới Bình Dương - Suối Tiên)",
         "GISXD_B2": "Metro Thủ Dầu Một - TP.HCM",
         "GISXD_V3": "Metro Vũng Tàu - Bà Rịa - Phú Mỹ"}


def _dist_m(a, b):
    return math.hypot((a[0] - b[0]) * 111000, (a[1] - b[1]) * 111000 * math.cos(math.radians(a[0])))


def _thin(seg, step=40):
    """Bỏ điểm cách điểm giữ lại trước đó < step mét (giữ đầu/cuối) → nhẹ trang."""
    if len(seg) < 3:
        return seg
    out = [seg[0]]
    for p in seg[1:-1]:
        if _dist_m(out[-1], p) >= step:
            out.append(p)
    out.append(seg[-1])
    return out


def fetch():
    docs = {}
    with httpx.Client(headers=UA, timeout=180, follow_redirects=True) as c:
        for alias, extra, prov in PROVS:
            j = c.get(URL, params={"tinh_alias": alias, **extra}).json()
            for f in j.get("layers", {}).get("02_tuyen_master_final", {}).get("features", []):
                p = f.get("properties") or {}
                rk, name = str(p.get("route_key") or ""), str(p.get("ten_tuyen") or "")
                if "metro" not in str(p.get("loai_tuy") or "").lower() or not rk.upper().startswith("GISXD_"):
                    continue
                if rk in SKIP or not name.startswith("Tuyến Metro"):   # bỏ lớp "Bình đồ..." chi tiết thi công
                    continue
                g = f.get("geometry") or {}
                parts = g.get("coordinates") or []
                if g.get("type") == "LineString":
                    parts = [parts]
                segs = [_thin([[round(y, 5), round(x, 5)] for x, y in s]) for s in parts if len(s) > 1]
                if not segs:
                    continue
                d = docs.setdefault(rk, {"route_key": rk, "segments": [], "prov": prov})
                d["segments"] += segs
                title = TITLE.get(rk) or (re.sub(r"^Tuyến\s+", "", name) + " TP.HCM")
                d.update(title=title, status=p.get("trang_thai") or p.get("trangthai") or "",
                         km=p.get("dai_km"), start=p.get("diem_dau"), end=p.get("diem_cuoi"),
                         tid=TID_OF.get(rk))
    return list(docs.values())


def _mid(segs):
    seg = max(segs, key=len)
    return seg[len(seg) // 2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    docs = fetch()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for d in docs:
        n = sum(len(s) for s in d["segments"])
        print(f"  {d['route_key']:<10} tid={d['tid'] or '-':<5} {d['km'] or '?':>6} km · {len(d['segments'])} đoạn · {n} điểm · {d['title']}")
    print(f"{len(docs)} tuyến metro từ lớp quy hoạch Cafeland.")
    if a.dry_run or not docs:
        return
    db = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB]
    db["Cafeland_Metro"].bulk_write(
        [UpdateOne({"route_key": d["route_key"]}, {"$set": {**d, "fetched_at": now}}, upsert=True) for d in docs])
    geo = []
    for d in docs:
        if d["tid"]:
            lat, lng = _mid(d["segments"])
            geo.append(UpdateOne({"tid": d["tid"]}, {"$set": {
                "tid": d["tid"], "lat": lat, "lng": lng, "prov": d["prov"], "at": now,
                "src": "điểm giữa tuyến (Cafeland · quy hoạch giao thông)"}}, upsert=True))
    if geo:
        db["Infra_Tracker_Geo"].bulk_write(geo)
    print(f"Đã ghi {len(docs)} tuyến vào Cafeland_Metro; dời {len(geo)} ghim về điểm giữa tuyến.")


if __name__ == "__main__":
    main()
