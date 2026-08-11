# -*- coding: utf-8 -*-
"""Nguồn URI MongoDB dùng chung — KHÔNG hardcode secret vào code (repo public).

Thứ tự tìm: biến môi trường IRIS_MONGO_URI  →  file mongo_uri.txt (gitignored, local).
Trên CI: đặt secret IRIS_MONGO_URI. Trên máy bạn: tạo file mongo_uri.txt.
"""
import os


def mongo_uri():
    u = os.environ.get("IRIS_MONGO_URI")
    if u:
        return u.strip()
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "mongo_uri.txt")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    raise SystemExit(
        "Thiếu URI MongoDB. Đặt biến môi trường IRIS_MONGO_URI, "
        "hoặc tạo file mongo_uri.txt (đã gitignore) cùng thư mục.")
