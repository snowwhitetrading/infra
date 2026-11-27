# -*- coding: utf-8 -*-
"""
export_re_satellite.py — Ảnh Sentinel-2 theo THÁNG cho các KHU ĐÔ THỊ LỚN có giá/tin rao trên trang Bất động sản.

Chỉ lấy khu đô thị quy mô lớn (hàng chục–hàng trăm ha): ở độ phân giải 10 m, một toà chung cư đơn lẻ không cho
thấy gì. Toạ độ lấy từ build_re_site.geo_match (Cafeland / Infra_RE_Geo) — dự án chưa có toạ độ thì bỏ qua.
Dùng lại bộ máy của export_satellite.py (chọn scene ít mây, phủ đủ khung). Ghi:
  satellite_export/re/<slug>/<tháng>_<ngày>_cloudNpct_<OK|CLOUDY>.png   +   satellite_export/re_manifest.csv
INCREMENTAL: (khu, tháng) đã có ảnh thì bỏ qua (trừ --force). Khu đô thị đối ứng (trong registry hạ tầng) do
export_satellite.py lo, không nằm ở đây.

  python export_re_satellite.py                      # 6 tháng gần nhất
  python export_re_satellite.py --from 2025-12 --to 2026-11
"""
import argparse
import csv
import datetime as dt
import os
import re
import unicodedata

from PIL import Image
from pymongo import MongoClient
from pystac_client import Client

import build_re_site as B
import export_satellite as X
from lib_db import mongo_uri

OUT = os.path.join(X.OUT_DIR, "re")
MANIFEST = os.path.join(X.OUT_DIR, "re_manifest.csv")
FIELDS = ["name", "site_key", "lat", "lon", "radius_km", "month", "capture_date", "aoi_cloud_pct", "status", "file"]
# tên dự án (đúng như trên trang Bất động sản) → bán kính khung ảnh (km)
BIG = {"Vinhomes Ocean Park 2": 2.5, "Vinhomes Ocean Park 3": 2.5, "Vinhomes Ocean Park Gia Lâm": 2.0, "Vinhomes Smart City": 1.5,
       "Vinhomes Grand Park": 2.0, "Vinhomes Wonder City Đan Phượng": 2.0, "Vinhomes Global Gate": 2.0, "Vinhomes Golden City": 2.0,
       "Vinhomes Royal Island": 2.5, "Khu đô thị Ecopark": 2.5, "Khu đô thị Waterpoint": 2.5, "Izumi City": 2.0, "Aqua City": 3.0,
       "Gamuda City": 1.5, "Celadon City": 1.5, "The Global City": 1.5, "Vạn Phúc City": 1.5, "Akari City": 1.0,
       "NovaWorld Hồ Tràm": 2.5, "Khu đô thị Nam Thăng Long - Ciputra": 2.0, "Splendora An Khánh": 2.0, "Bcons City": 1.0}


def slug(s):
    t = unicodedata.normalize("NFD", s.lower().replace("đ", "d")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


def sites(client):
    R = client["dc_realestate"]
    P, L = B.price_series(R), B.listing_series(R)
    projects = [{"n": nm, "city": (P.get(nm, {}).get("city") or L.get(nm, {}).get("prov") or "")} for nm in sorted(set(P) | set(L))]
    B.geo_match(client, projects)
    out = []
    for p in projects:
        if p["n"] in BIG and p.get("lat"):
            out.append({"name": p["n"], "site_key": slug(p["n"]), "lat": p["lat"], "lon": p["lng"], "radius_km": BIG[p["n"]]})
    miss = sorted(set(BIG) - {s["name"] for s in out})
    if miss:
        print("Chưa có toạ độ / không có trong dữ liệu, bỏ qua:", ", ".join(miss))
    return out


def main():
    six = dt.date.today() - dt.timedelta(days=170)
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default=f"{six:%Y-%m}")
    ap.add_argument("--to", dest="date_to", default=f"{dt.date.today():%Y-%m}")
    ap.add_argument("--px", type=int, default=360)
    ap.add_argument("--candidates", type=int, default=3)
    ap.add_argument("--max-cloud", type=float, default=30.0)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    S = sites(MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000))
    mlist = X.months(a.date_from, a.date_to)
    print(f"{len(S)} khu đô thị × {len(mlist)} tháng ({a.date_from}→{a.date_to})", flush=True)
    rows = list(csv.DictReader(open(MANIFEST, encoding="utf-8"))) if os.path.exists(MANIFEST) else []
    have = {(r["site_key"], r["month"]) for r in rows}
    client = Client.open(X.STAC)
    added = 0

    def save():
        rows.sort(key=lambda r: (r["site_key"], r["month"]))
        with open(MANIFEST, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)

    for s in S:
        bbox = X.aoi_bbox(s["lat"], s["lon"], s["radius_km"])
        folder = os.path.join(OUT, s["site_key"])
        for month in mlist:
            if (s["site_key"], month) in have and not a.force:
                continue
            try:
                item, cloud = X.pick_scene(client, bbox, month, a.candidates)
                if not item:
                    continue
                date = item.datetime.date().isoformat()
                status = "OK" if cloud < a.max_cloud else "CLOUDY"
                rel = f"re/{s['site_key']}/{month}_{date}_cloud{int(round(cloud))}pct_{status}.png"
                os.makedirs(folder, exist_ok=True)
                Image.fromarray(X.render_rgb(item, bbox, a.px)).save(os.path.join(X.OUT_DIR, rel))
            except Exception as e:
                print(f"  ! {s['site_key']} {month}: {str(e)[:80]}", flush=True)
                continue
            rows[:] = [r for r in rows if not (r["site_key"] == s["site_key"] and r["month"] == month)]
            rows.append({**s, "month": month, "capture_date": date, "aoi_cloud_pct": round(cloud, 1), "status": status, "file": rel})
            added += 1
            print(f"  {s['site_key']:34} {month}  {date}  mây {cloud:5.1f}%  {status}", flush=True)
        save()                                               # ghi sau mỗi khu → dừng giữa chừng không mất phần đã làm
    print(f"Thêm {added} ảnh.")


if __name__ == "__main__":
    main()
