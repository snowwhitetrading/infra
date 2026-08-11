# -*- coding: utf-8 -*-
"""
step5_radar.py — RADAR hạ tầng: PHÁT HIỆN dự án quan trọng CHƯA có trong registry.

Quét feed rộng (báo ngành GTVT/XD/đấu thầu/chính phủ + tin gộp) → trích ỨNG VIÊN tên dự án
bằng regex (cao tốc/đường sắt/sân bay/cảng/metro/vành đai/KCN/nhà máy điện...) →
LOẠI dự án đã theo dõi (khớp alias registry) → xếp hạng theo độ nóng →
ghi dc_commodity.Infra_Project_Candidates.

Deterministic, KHÔNG cần AI. Sau đó xem bảng candidates (theo score) và thêm dự án đáng chú ý
vào Infra_Projects_Registry để nó được theo dõi đầy đủ như 18 dự án gốc.

  python step5_radar.py            # quét + ghi candidates
  python step5_radar.py --top 30   # in top ứng viên nóng nhất
  python step5_radar.py --dry-run
"""
import argparse
import datetime as dt
import re
import xml.etree.ElementTree as ET
from collections import defaultdict

import httpx
from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri
from lib_projects import build_alias_regex

OUT_DB, OUT_COLL = "dc_commodity", "Infra_Project_Candidates"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"}

# feed rộng về hạ tầng/đầu tư/chính sách
FEEDS = [
    "https://vneconomy.vn/tin-moi.rss", "https://vneconomy.vn/dau-tu-ha-tang.rss",
    "https://baodauthau.vn/rss/home.rss", "https://baodauthau.vn/rss/dau-tu.rss",
    "https://baoxaydung.com.vn/rss/home.rss", "https://www.baogiaothong.vn/rss/home.rss",
    "https://baochinhphu.vn/kinh-te.rss", "https://vietstock.vn/761/kinh-te/vi-mo.rss",
]

# loại công trình hạ tầng + tên đi kèm
TYPE = (r"(cao tốc|đường sắt tốc độ cao|đường sắt|đường vành đai|vành đai|sân bay|"
        r"cảng hàng không|cảng biển|cảng|metro|tuyến metro|khu công nghiệp|"
        r"nhà máy điện|thủy điện|nhiệt điện|điện gió|điện khí|đường ống|hầm|cầu)")
NAME = r"([A-ZĐÀ-Ỹ][\wÀ-ỹ\s\-–./]{2,45}?)"
STOP = r"(?=[,.:;\)\"]| với | của | do | tại | được | dự kiến | trị giá | hơn | khoảng | vốn | có |$)"
RX = re.compile(TYPE + r"\s+" + NAME + STOP, re.IGNORECASE)
IMPORTANT = re.compile(r"tỷ\s*USD|nghìn tỷ|trọng điểm|quốc gia|liên vùng|\d[\d.,]*\s*tỷ", re.IGNORECASE)
NOISE = re.compile(r"^(này|đó|trên|mới|các|những|một số|nhiều|khác)\b", re.IGNORECASE)


def norm(s):
    return re.sub(r"\s+", " ", s).strip(" -–.\"'").lower()


def extract(title, alias_rx):
    """Trả list (label, key) ứng viên trong 1 tiêu đề, đã loại dự án đã theo dõi."""
    out = []
    for m in RX.finditer(title):
        typ, name = m.group(1).strip(), m.group(2).strip()
        if NOISE.search(name) or len(name) < 4:
            continue
        label = f"{typ} {name}"
        if any(rx.search(label) for rx in alias_rx.values()):   # đã có trong registry
            continue
        out.append((label, norm(label)))
    return out


def run(dry, top):
    alias_rx = build_alias_regex()
    cand = defaultdict(lambda: {"label": "", "count": 0, "sources": set(),
                                "important": False, "samples": [], "last": ""})
    with httpx.Client(headers=UA, timeout=15, follow_redirects=True) as client:
        for u in FEEDS:
            try:
                items = ET.fromstring(client.get(u).text).findall(".//item")
            except Exception:
                continue
            src = re.sub(r"^https?://(www\.)?", "", u).split("/")[0]
            for it in items:
                title = (it.findtext("title") or "").strip()
                desc = re.sub("<[^>]+>", "", it.findtext("description") or "")
                for label, key in extract(title + ". " + desc, alias_rx):
                    c = cand[key]
                    c["label"] = c["label"] or label
                    c["count"] += 1
                    c["sources"].add(src)
                    if IMPORTANT.search(title + " " + desc):
                        c["important"] = True
                    if len(c["samples"]) < 3:
                        c["samples"].append(title[:90])

    rows = []
    for key, c in cand.items():
        score = c["count"] + 2 * len(c["sources"]) + (3 if c["important"] else 0)
        rows.append({"key": key, "label": c["label"], "count": c["count"],
                     "sources": sorted(c["sources"]), "important": c["important"],
                     "score": score, "samples": c["samples"]})
    rows.sort(key=lambda r: r["score"], reverse=True)

    print(f"Ứng viên dự án mới (ngoài registry): {len(rows)}")
    for r in rows[:top]:
        star = "★" if r["important"] else " "
        print(f"  {star} score {r['score']:3}  ×{r['count']:2}  {r['label'][:60]}")

    if dry or not rows:
        return
    coll = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[OUT_DB][OUT_COLL]
    coll.create_index("key", unique=True)
    now = dt.datetime.now().isoformat()
    ops = [UpdateOne({"key": r["key"]}, {"$set": {**r, "updated": now}}, upsert=True) for r in rows]
    coll.bulk_write(ops)
    print(f"Đã ghi {len(rows)} ứng viên vào {OUT_DB}.{OUT_COLL} (xem theo score, thêm cái đáng chú ý vào registry).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25, help="Số ứng viên in ra")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(a.dry_run, a.top)
