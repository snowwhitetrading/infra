# -*- coding: utf-8 -*-
"""
export_satellite.py — Xuất ảnh Sentinel-2 (RGB) theo THÁNG cho các dự án trong registry.

Nguồn: Copernicus Sentinel-2 L2A (ESA) qua Earth Search / AWS Open Data (COG public, KHÔNG cần key).
Mỗi (dự án × tháng): tìm scene, chấm % mây TRÊN AOI (đọc band SCL), chọn scene ít mây nhất →
cắt cửa sổ AOI quanh toạ độ → stretch RGB → PNG. Ghi/nối satellite_export/manifest.csv.

INCREMENTAL: (dự án, tháng) đã có PNG trên đĩa thì bỏ qua (trừ --force).

  python export_satellite.py                       # 6 tháng gần nhất, mọi dự án active có toạ độ
  python export_satellite.py --from 2025-01 --to 2026-08
  python export_satellite.py --projects giabinh_airport ring4_hcmc --radius-km 1.5
  python export_satellite.py --limit 5 --dry-run
"""
import argparse
import csv
import datetime as dt
import math
import os

import numpy as np
import rasterio
from PIL import Image
from pymongo import MongoClient
from pystac_client import Client
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

from lib_db import mongo_uri

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "satellite_export")
STAC = "https://earth-search.aws.element84.com/v1"
CLOUD_SCL = {3, 8, 9, 10}         # shadow, cloud medium, cloud high, cirrus
GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                GDAL_HTTP_MULTIRANGE="YES", GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
                CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", AWS_NO_SIGN_REQUEST="YES")


def months(a, b):
    y, m = int(a[:4]), int(a[5:7])
    ey, em = int(b[:4]), int(b[5:7])
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def aoi_bbox(lat, lon, km):
    dlat = km / 111.0
    dlon = km / (111.0 * max(0.1, math.cos(math.radians(lat))))
    return [lon - dlon, lat - dlat, lon + dlon, lat + dlat]   # lon/lat order (minx,miny,maxx,maxy)


def read_band(href, bbox_ll, px, resampling):
    shape = px if isinstance(px, tuple) else (px, px)            # (cao, rộng) — site cũ có khung không vuông
    with rasterio.open(href) as src:
        l, b, r, t = transform_bounds("EPSG:4326", src.crs, *bbox_ll, densify_pts=21)
        win = from_bounds(l, b, r, t, src.transform)
        return src.read(1, window=win, out_shape=shape, boundless=True,
                        fill_value=0, resampling=resampling)


def aoi_cloud_pct(scl_href, bbox_ll, px=64):
    scl = read_band(scl_href, bbox_ll, px, Resampling.nearest)
    valid = scl > 0
    if not valid.any():
        return 100.0
    return 100.0 * np.isin(scl, list(CLOUD_SCL)).sum() / valid.sum()


def render_rgb(item, bbox_ll, px):
    r = read_band(item.assets["red"].href, bbox_ll, px, Resampling.bilinear)
    g = read_band(item.assets["green"].href, bbox_ll, px, Resampling.bilinear)
    b = read_band(item.assets["blue"].href, bbox_ll, px, Resampling.bilinear)
    rgb = np.stack([r, g, b]).astype("float32")
    rgb = np.clip(rgb / 3000.0, 0, 1) ** (1 / 1.4)          # stretch: chia 3000, gamma 1.4
    return (rgb * 255).astype("uint8").transpose(1, 2, 0)


def pick_scene(client, bbox_ll, month, candidates):
    y, m = int(month[:4]), int(month[5:7])
    start = f"{month}-01"
    end = f"{y + (m == 12):04d}-{(m % 12) + 1:02d}-01"
    items = list(client.search(collections=["sentinel-2-l2a"], bbox=bbox_ll,
                               datetime=f"{start}/{end}",
                               query={"eo:cloud_cover": {"lt": 60}}, max_items=12).items())
    if not items:
        return None, None
    items.sort(key=lambda it: it.properties.get("eo:cloud_cover", 100))
    best, best_c = None, 101.0
    for it in items[:candidates]:                            # chấm mây AOI cho vài ứng viên ít mây nhất
        try:
            c = aoi_cloud_pct(it.assets["scl"].href, bbox_ll)
        except Exception:
            continue
        if c < best_c:
            best, best_c = it, c
    return best, best_c


# SITE CŨ đã chỉnh tay AOI (11 công trường đầu tiên): không có trong registry với đúng toạ độ nên trước đây
# job hằng tháng bỏ sót → tab Vệ tinh kẹt ở ảnh cũ. Xuất tiếp cho chúng với ĐÚNG khung hình ban đầu:
# tâm = lat/lon trong manifest, kích thước = cỡ ảnh gốc × 10 m/px (ảnh gốc ở độ phân giải gốc Sentinel-2).
# site cũ → tid (khớp SITEKEY2TID trong step5_build_site.py). Dự án registry trùng tid KHÔNG xuất nữa:
# toạ độ registry của chúng lệch AOI (vd Gia Bình 106.28 thay vì 106.20) → chỉ tốn tải và sinh ảnh sai chỗ.
LEGACY_TID = {"apec_center": 1, "pq_airport": 2, "bai_dat_do": 3, "nui_ong_quan": 4, "pq_tram": 5,
              "rach_chiec": 6, "gb_road_hn": 7, "gb_road_bn": 8, "cangio_depot": 9, "halong_depot": 10,
              "gia_binh": 11}
LEGACY_SITES = tuple(LEGACY_TID)


def legacy_sites(rows):
    """{site_key: {lat, lon, folder, shape(h,w), bbox, no, name}} — suy từ manifest + ảnh gốc sớm nhất."""
    out = {}
    for sk in LEGACY_SITES:
        rs = sorted((r for r in rows if r["site_key"] == sk), key=lambda r: r["month"])
        if not rs:
            continue
        first = rs[0]
        folder = first["file"].split("/")[0]
        try:
            w, h = Image.open(os.path.join(OUT_DIR, first["file"])).size
        except OSError:
            continue
        lat, lon = float(first["lat"]), float(first["lon"])
        dlat = (h * 10.0 / 2000.0) / 111.0
        dlon = (w * 10.0 / 2000.0) / (111.0 * max(0.1, math.cos(math.radians(lat))))
        out[sk] = {"lat": lat, "lon": lon, "folder": folder, "shape": (h, w),
                   "bbox": [lon - dlon, lat - dlat, lon + dlon, lat + dlat],
                   "no": first["project_no"], "name": first["project_name"]}
    return out


def export_legacy(a, client, rows):
    """Xuất các tháng còn thiếu (hoặc sai khung) cho site cũ, vào đúng thư mục gốc của site. Trả (rows, số ảnh thêm)."""
    added = 0
    for sk, s in legacy_sites(rows).items():
        folder = os.path.join(OUT_DIR, s["folder"])
        for month in months(a.date_from, a.date_to):
            cur = [f for f in os.listdir(folder) if f.startswith(month + "_") and f.endswith(".png")]
            good = [f for f in cur if Image.open(os.path.join(folder, f)).size == (s["shape"][1], s["shape"][0])]
            if good and not a.force:
                continue                                      # đã có ảnh đúng khung của tháng này
            try:
                item, cloud = pick_scene(client, s["bbox"], month, a.candidates)
            except Exception as e:
                print(f"  ! {sk} {month}: search lỗi ({e})")
                continue
            if not item:
                continue
            date = item.datetime.date().isoformat()
            status = "OK" if cloud < a.max_cloud else "CLOUDY"
            fname = f"{month}_{date}_cloud{int(round(cloud))}pct_{status}.png"
            print(f"  {sk:24} {month}  {date}  cloud {cloud:4.1f}%  {status}  (site cũ, {s['shape'][1]}x{s['shape'][0]})", flush=True)
            if a.dry_run:
                added += 1
                continue
            try:
                img = render_rgb(item, s["bbox"], s["shape"])
            except Exception as e:
                print(f"    ! render lỗi: {e}")
                continue
            for f in cur:                                     # thay ảnh cũ của tháng (sai khung / nhiều mây)
                os.remove(os.path.join(folder, f))
            Image.fromarray(img).save(os.path.join(folder, fname))
            rows = [r for r in rows if not (r["site_key"] == sk and r["month"] == month)]
            rows.append({"project_no": s["no"], "project_name": s["name"], "site_key": sk,
                         "lat": s["lat"], "lon": s["lon"], "month": month, "capture_date": date,
                         "aoi_cloud_pct": round(cloud, 1), "status": status, "file": f"{s['folder']}/{fname}"})
            added += 1
    return rows, added


def load_manifest():
    path = os.path.join(OUT_DIR, "manifest.csv")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_manifest(rows):
    path = os.path.join(OUT_DIR, "manifest.csv")
    cols = ["project_no", "project_name", "site_key", "lat", "lon",
            "month", "capture_date", "aoi_cloud_pct", "status", "file"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


def run(a):
    reg = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)["dc_commodity"]["Infra_Projects_Registry"]
    projects = list(reg.find({"active": True, "lat": {"$ne": None}, "tid": {"$ne": None}})
                    .sort("tid", 1))
    if a.projects:
        want = set(a.projects)
        projects = [p for p in projects if p["id"] in want]
    projects = projects[:a.limit] if a.limit else projects
    mlist = months(a.date_from, a.date_to)
    print(f"{len(projects)} dự án × {len(mlist)} tháng ({a.date_from}→{a.date_to}), "
          f"AOI {a.radius_km}km, {a.px}px")

    client = Client.open(STAC)
    rows = load_manifest()
    added = 0
    if not a.projects or a.legacy_only:                      # site cũ đã chỉnh tay AOI: luôn xuất tiếp, đúng khung gốc
        rows, n_leg = export_legacy(a, client, rows)
        added += n_leg
    if a.legacy_only:
        projects = []
    skip = [p["id"] for p in projects if p["tid"] in LEGACY_TID.values()]
    if skip:
        print(f"Bỏ {len(skip)} dự án registry đã có site cũ đúng AOI: {', '.join(skip)}")
        projects = [p for p in projects if p["tid"] not in LEGACY_TID.values()]
    have = {(r["site_key"], r["month"]) for r in rows}       # (site, tháng) đã có trong manifest
    for p in projects:
        pid, tid, name = p["id"], p["tid"], p.get("name", "")
        bbox = aoi_bbox(p["lat"], p["lon"], a.radius_km)
        folder = os.path.join(OUT_DIR, pid)
        for month in mlist:
            existing = os.path.exists(folder) and any(f.startswith(month + "_") for f in os.listdir(folder))
            if (pid, month) in have or existing:
                if not a.force:
                    continue
            try:
                item, cloud = pick_scene(client, bbox, month, a.candidates)
            except Exception as e:
                print(f"  ! {pid} {month}: search lỗi ({e})")
                continue
            if not item:
                continue
            date = item.datetime.date().isoformat()
            status = "OK" if cloud < a.max_cloud else "CLOUDY"
            fname = f"{month}_{date}_cloud{int(round(cloud))}pct_{status}.png"
            rel = f"{pid}/{fname}"
            print(f"  {pid:24} {month}  {date}  cloud {cloud:4.1f}%  {status}")
            if not a.dry_run:
                os.makedirs(folder, exist_ok=True)
                try:
                    img = render_rgb(item, bbox, a.px)
                except Exception as e:
                    print(f"    ! render lỗi: {e}")
                    continue
                Image.fromarray(img).save(os.path.join(OUT_DIR, rel))
                rows = [r for r in rows if not (r["site_key"] == pid and r["month"] == month)]
                rows.append({"project_no": tid, "project_name": name, "site_key": pid,
                             "lat": p["lat"], "lon": p["lon"], "month": month,
                             "capture_date": date, "aoi_cloud_pct": round(cloud, 1),
                             "status": status, "file": rel})
            added += 1
    if not a.dry_run and added:
        rows.sort(key=lambda r: (str(r["site_key"]), str(r["month"])))
        save_manifest(rows)
    print(f"{'(dry) ' if a.dry_run else ''}Thêm {added} ảnh.")


if __name__ == "__main__":
    today = dt.date.today()
    six = (today.replace(day=1) - dt.timedelta(days=155))
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default=f"{six:%Y-%m}")
    ap.add_argument("--to", dest="date_to", default=f"{today:%Y-%m}")
    ap.add_argument("--projects", nargs="*", help="chỉ các id này (mặc định: tất cả)")
    ap.add_argument("--radius-km", type=float, default=1.0)
    ap.add_argument("--px", type=int, default=320, help="kích thước ảnh ra (px)")
    ap.add_argument("--candidates", type=int, default=3, help="số scene ít mây nhất để chấm AOI")
    ap.add_argument("--max-cloud", type=float, default=30.0, help="ngưỡng OK/CLOUDY")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--legacy-only", action="store_true", help="chỉ xuất 11 site cũ đã chỉnh tay AOI")
    ap.add_argument("--dry-run", action="store_true")
    with rasterio.Env(**GDAL_ENV):
        run(ap.parse_args())
