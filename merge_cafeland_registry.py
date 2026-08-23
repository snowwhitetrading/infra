# -*- coding: utf-8 -*-
"""
merge_cafeland_registry.py — Gộp HẠ TẦNG cafeland (Cafeland_Lines + điểm hạ tầng + OSM_Infra)
vào Infra_Projects_Registry để track chung (Tiến độ + Bản đồ). Dedup theo tên chuẩn hoá.

Mỗi mục thêm: id·tid·name·aliases·lat/lon·geo(line/point)·location·group·origin='cafeland'.
BĐS/KCN thương mại KHÔNG gộp (giữ tham khảo).

  python merge_cafeland_registry.py --dry-run
  python merge_cafeland_registry.py
"""
import argparse
import re
import unicodedata

from pymongo import MongoClient

from lib_db import mongo_uri

DB = "dc_commodity"


def _norm(s):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ",
                    unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()).split())


def _slug(s, used):
    b = re.sub(r"[^a-z0-9]+", "_", unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()).strip("_")[:40]
    b = b or "caf"
    s2, i = b, 2
    while s2 in used:
        s2 = f"{b}_{i}"
        i += 1
    used.add(s2)
    return s2


def main(dry):
    from step5_build_site import _point_class, _prov_norm, _infra_cat, _titlecase
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB]
    reg = c["Infra_Projects_Registry"]
    from lib_projects import is_dup_name, known_token_sets, dup_tokens
    _catf = lambda n, loc="": _infra_cat(n)
    existing = list(reg.find({}, {"name": 1, "aliases": 1, "id": 1, "tid": 1}))
    known_sets = known_token_sets(reg, _catf)   # (loại hình, token) của mọi dự án — fuzzy dedup cùng loại
    used_ids = {d.get("id") for d in existing}
    tid = max((d.get("tid", 0) or 0 for d in existing), default=0)
    pm = {d["pid"]: d["name"] for d in c["Cafeland_Provinces"].find({}, {"pid": 1, "name": 1})}

    # gom nguồn hạ tầng
    items = []  # (title, kind:line/point, coords_or_None, lat, lng, prov, cat)
    for d in c["Cafeland_Lines"].find({"coords.1": {"$exists": True}}):
        items.append((d.get("title", ""), "line", d.get("coords"), d.get("lat"), d.get("lng"),
                      _prov_norm(pm.get(d.get("getIdProvince"), "")), _infra_cat(d.get("title"))))
    for d in c["Cafeland_Points"].find({"lat": {"$ne": None}}):
        if _point_class(d.get("title"))[0] == "infra":
            items.append((d.get("title", ""), "point", None, d.get("lat"), d.get("lng"),
                          _prov_norm(pm.get(d.get("getIdProvince"), "")), _point_class(d.get("title"))[1]))
    for d in c["OSM_Infra"].find({}):
        segs = d.get("segments") or []
        coords = max(segs, key=len) if segs else None            # đoạn dài nhất làm geo
        if coords:
            lat = coords[len(coords) // 2][0]
            lng = coords[len(coords) // 2][1]
            items.append((d.get("title", ""), "line", coords, lat, lng, d.get("prov", ""), d.get("cat", "Đường bộ")))

    added, skip = [], 0
    for title, kind, coords, lat, lng, prov, cat in items:
        if not (title or "").strip() or is_dup_name(title, _infra_cat(title), known_sets):   # TRÙNG cùng loại → bỏ
            skip += 1
            continue
        known_sets.append((_infra_cat(title), dup_tokens(title)))         # thêm để không trùng nhau trong lô
        tid += 1
        doc = {"id": _slug(title, used_ids), "tid": tid, "name": _titlecase(title.strip()),
               "aliases": [_titlecase(title.strip())], "active": True, "origin": "cafeland",
               "group": cat, "location": prov,
               "lat": lat, "lng": lng, "lon": lng}
        if kind == "line" and coords:
            doc["geo"] = {"type": "line", "coords": coords}
        added.append(doc)

    print(f"Hạ tầng cafeland: {len(items)} · TRÙNG registry (bỏ): {skip} · THÊM MỚI: {len(added)}")
    from collections import Counter
    print("  theo loại:", dict(Counter(a["group"] for a in added)))
    for a in added[:12]:
        print(f"   + tid{a['tid']} [{a['group']}] {a['name'][:44]}")
    if dry:
        return
    reg.bulk_write([__import__("pymongo").UpdateOne({"id": a["id"]}, {"$set": a}, upsert=True) for a in added])
    print(f"Đã thêm {len(added)} dự án hạ tầng vào registry (tổng {reg.count_documents({})}).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    main(ap.parse_args().dry_run)
