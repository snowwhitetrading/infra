# -*- coding: utf-8 -*-
"""
geocode_re.py — Tìm toạ độ cho dự án bất động sản CHƯA ghép được vị trí theo tên với map.cafeland.vn.

Chạy TAY (Nominatim giới hạn 1 truy vấn/giây, không đưa vào CI). Kết quả lưu dc_commodity.Infra_RE_Geo
{n, lat, lng, src, q, display}; build_re_site.py đọc bảng này để bù chỗ trống. Thứ tự thử cho mỗi dự án:
  1) ghép Cafeland với TÊN ĐÃ LÀM SẠCH (VHM → Vinhomes, bỏ tiền tố "Khu đô thị", "Chung cư", ngoặc đơn);
  2) phân khu "A - B" → lấy vị trí DỰ ÁN MẸ (B, rồi A) — vd "The Origami - Vinhomes Grand Park";
  3) Nominatim (OpenStreetMap) với "tên, tỉnh, Việt Nam".
Chỉ nhận kết quả khi vĩ độ hợp với tỉnh của dự án và tên trả về chứa từ đặc trưng của dự án. Không khớp → bỏ trống.

  python geocode_re.py            # chỉ dự án chưa có trong Infra_RE_Geo
  python geocode_re.py --redo     # làm lại tất cả
  python geocode_re.py --dry-run
"""
import argparse
import datetime as dt
import re
import time

import httpx
from pymongo import MongoClient, UpdateOne

import build_re_site as B
from lib_db import mongo_uri

NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = {"User-Agent": "infra-tracker-map/1.0 (project tracking site)"}
GENERIC = B._GEO_STOP | set("park residence residences tower towers complex garden gardens center centre plaza premium the".split())


def clean(name):
    n = re.sub(r"\s*\(.*?\)", "", name or "")
    n = re.sub(r"^VHM\s+", "Vinhomes ", n)
    n = re.sub(r"^(Khu đô thị|Tiểu khu đô thị mới|Chung cư|Khu căn hộ|Dự án)\s+", "", n, flags=re.I)
    return n.replace("OCP", "Ocean Park").strip()


def variants(name):
    """Các tên để thử, theo thứ tự ưu tiên: tên sạch → dự án mẹ (sau/trước dấu ' - ')."""
    c = clean(name)
    out = [c]
    if re.search(r"\s[-–]\s", c) or "-" in c:
        parts = [x.strip() for x in re.split(r"\s*[-–]\s*", c) if x.strip()]
        out += [clean(x) for x in parts[::-1]]
    seen, res = set(), []
    for v in out:
        if v and v.lower() not in seen and len(B._n(v).split()) >= 2:
            seen.add(v.lower()); res.append(v)
    return res


def caf_lookup(caf, name, city):
    k = B._n(name); kt = set(k.split()) - B._GEO_STOP
    best = None
    for t, x in caf:
        if not B._lat_ok(city, x["lat"]):
            continue
        tt = set(t.split())
        if t == k:
            return x
        if len(kt) >= 2 and kt <= tt:
            sc = 2 - 0.01 * len(tt)
            if not best or sc > best[0]:
                best = (sc, x)
    return best[1] if best else None


def nominatim(cl, name, city):
    q = ", ".join(x for x in (name, city, "Việt Nam") if x)
    try:
        r = cl.get(NOMINATIM, params={"q": q, "format": "jsonv2", "limit": 3, "countrycodes": "vn", "accept-language": "vi"}, timeout=20)
        js = r.json()
    except Exception:
        return None
    key = set(B._n(name).split()) - GENERIC                    # từ đặc trưng (bỏ park, tower, city...)
    for it in js or []:
        lat, lng = float(it["lat"]), float(it["lon"])
        disp = B._n(it.get("display_name", ""))
        if key and B._lat_ok(city, lat) and sum(1 for t in key if t in disp.split()) >= min(2, len(key)):
            return {"lat": lat, "lng": lng, "display": it.get("display_name", "")[:160], "q": q}
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    R = c["dc_realestate"]
    P, L = B.price_series(R), B.listing_series(R)
    projects = [{"n": nm, "city": (P.get(nm, {}).get("city") or L.get(nm, {}).get("prov") or "")} for nm in sorted(set(P) | set(L))]
    B.geo_match(c, projects, use_cache=False)
    col = c["dc_commodity"]["Infra_RE_Geo"]
    done = set() if a.redo else {d["n"] for d in col.find({}, {"n": 1})}
    todo = [p for p in projects if not p.get("lat") and p["n"] not in done]
    print(f"{len(projects)} dự án · {sum(1 for p in projects if p.get('lat'))} đã ghép Cafeland · cần tìm: {len(todo)}", flush=True)
    caf = [(B._n(x.get("title")), x) for x in c["dc_commodity"]["Infra_RealEstate"].find(
        {"lat": {"$ne": None}}, {"_id": 0, "title": 1, "lat": 1, "lng": 1})]
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    ops, n = [], {"cafeland": 0, "mẹ": 0, "osm": 0, "trống": 0}
    with httpx.Client(headers=UA, follow_redirects=True) as cl:
        for p in todo:
            got, vs = None, variants(p["n"])
            for i, v in enumerate(vs):                              # 1+2: Cafeland theo tên sạch / dự án mẹ
                x = caf_lookup(caf, v, p["city"])
                if x:
                    got = {"lat": x["lat"], "lng": x["lng"], "src": "cafeland" if i == 0 else "cafeland (dự án mẹ)", "q": v, "display": x.get("title", "")[:160]}
                    n["cafeland" if i == 0 else "mẹ"] += 1
                    break
            if not got:
                for v in vs:                                        # 3: OpenStreetMap
                    r = nominatim(cl, v, p["city"]); time.sleep(1.05)
                    if r:
                        got = {**r, "src": "openstreetmap"}; n["osm"] += 1
                        break
            if not got:
                n["trống"] += 1
            print(f"  {'OK  ' if got else 'MISS'} {p['n'][:44]:44} {(got or {}).get('src', ''):22} {(got or {}).get('display', '')[:70]}", flush=True)
            ops.append(UpdateOne({"n": p["n"]}, {"$set": {"n": p["n"], "city": p["city"], "at": now, **(got or {"lat": None, "lng": None, "src": ""})}}, upsert=True))
    print(f"Kết quả: {n}")
    if ops and not a.dry_run:
        col.bulk_write(ops)


if __name__ == "__main__":
    main()
