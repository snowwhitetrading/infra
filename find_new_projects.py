# -*- coding: utf-8 -*-
"""
find_new_projects.py — DISCOVERY qua chat: in tiêu đề tin hạ tầng THÔ (ngoài registry).

Deterministic, không LLM. Khi bạn muốn tìm dự án mới: chạy file này → đưa danh sách cho
Claude (chat) đọc + chấm + thêm vào registry bằng add_project.py.

  python find_new_projects.py
"""
import re
import xml.etree.ElementTree as ET
import httpx
from lib_projects import build_alias_regex

FEEDS = [
    "https://vneconomy.vn/tin-moi.rss", "https://vneconomy.vn/dau-tu-ha-tang.rss",
    "https://baodauthau.vn/rss/home.rss", "https://baodauthau.vn/rss/dau-tu.rss",
    "https://baoxaydung.com.vn/rss/home.rss", "https://www.baogiaothong.vn/rss/home.rss",
    "https://baochinhphu.vn/kinh-te.rss", "https://vietstock.vn/761/kinh-te/vi-mo.rss",
]
# chỉ hạ tầng giao thông (bỏ điện/nước)
INFRA = re.compile(r"cao tốc|đường sắt|tàu điện|sân bay|cảng|metro|vành đai|hầm |cầu ", re.I)

known = build_alias_regex()
seen, out = set(), []
with httpx.Client(headers={"User-Agent": "Mozilla/5.0"}, timeout=15, follow_redirects=True) as cl:
    for u in FEEDS:
        try:
            items = ET.fromstring(cl.get(u).text).findall(".//item")
        except Exception:
            continue
        for it in items:
            t = (it.findtext("title") or "").strip()
            if t and INFRA.search(t) and t not in seen and not any(rx.search(t) for rx in known.values()):
                seen.add(t)
                out.append(t)
print(f"### {len(out)} tiêu đề hạ tầng thô (ngoài {len(known)} dự án đang theo dõi):")
for t in out:
    print("•", t)
