# -*- coding: utf-8 -*-
"""
set_geo.py — Gán hình học bản đồ cho 1 dự án trong Infra_Projects_Registry.

  python set_geo.py <id> line "lat,lon lat,lon lat,lon ..."   # đường (polyline)
  python set_geo.py <id> area "lat,lon lat,lon lat,lon ..."   # khoanh vùng (polygon, >=3 điểm)
  python set_geo.py <id> point                                # xoá geo → về chấm điểm

Toạ độ: từng cặp "vĩ độ,kinh độ" cách nhau bằng khoảng trắng. Lấy nhanh trên
Google Maps: chuột phải điểm → toạ độ hiện ra dạng lat, lon.

Ví dụ (đường Gia Bình→HN):
  python set_geo.py giabinh_road_hn line "21.05,106.28 21.02,106.10 21.01,105.95 21.03,105.85"
"""
import sys
from pymongo import MongoClient
from lib_db import mongo_uri


def parse_coords(s):
    pts = []
    for tok in s.split():
        lat, lon = tok.split(",")
        pts.append([float(lat), float(lon)])
    return pts


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    pid, kind = sys.argv[1], sys.argv[2]
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    reg = c["dc_commodity"]["Infra_Projects_Registry"]
    doc = reg.find_one({"id": pid})
    if not doc:
        sys.exit(f"Không thấy dự án id='{pid}' trong registry.")
    if kind == "point":
        reg.update_one({"id": pid}, {"$unset": {"geo": ""}})
        print(f"✓ {pid}: xoá geo → hiển thị chấm điểm.")
        return
    if kind not in ("line", "area"):
        sys.exit("kind phải là: line | area | point")
    if len(sys.argv) < 4:
        sys.exit("Thiếu chuỗi toạ độ.")
    coords = parse_coords(sys.argv[3])
    need = 2 if kind == "line" else 3
    if len(coords) < need:
        sys.exit(f"{kind} cần >= {need} điểm, mới có {len(coords)}.")
    reg.update_one({"id": pid}, {"$set": {"geo": {"type": kind, "coords": coords}}})
    print(f"✓ {pid} ({doc.get('name','')}): geo={kind}, {len(coords)} điểm.")


if __name__ == "__main__":
    main()
