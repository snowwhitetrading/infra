# -*- coding: utf-8 -*-
"""
step5b_radar_llm.py — RADAR mức B (LLM): trích ứng viên dự án SẠCH bằng Claude.

Quét feed rộng → gom tiêu đề hạ tầng (ngoài registry) → Claude trích DANH SÁCH dự án
riêng biệt + phân loại + độ quan trọng + alias gợi ý → ghi Infra_Project_Candidates
(origin='llm'). Sạch hơn hẳn regex (step5_radar.py).

Cần CLAUDE_CODE_OAUTH_TOKEN (gói Max, tạo bằng `claude setup-token`) hoặc ANTHROPIC_API_KEY.
Không có token → thoát nhẹ (CI bỏ qua).

  python step5b_radar_llm.py
  python step5b_radar_llm.py --dry-run
"""
import argparse
import datetime as dt
import json
import re
import xml.etree.ElementTree as ET

import httpx
from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri
from lib_projects import build_alias_regex
from lib_llm import get_client, build_system

MODEL = "claude-haiku-4-5"
OUT_DB, OUT_COLL = "dc_commodity", "Infra_Project_Candidates"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"}
FEEDS = [
    "https://vneconomy.vn/tin-moi.rss", "https://vneconomy.vn/dau-tu-ha-tang.rss",
    "https://baodauthau.vn/rss/home.rss", "https://baodauthau.vn/rss/dau-tu.rss",
    "https://baoxaydung.com.vn/rss/home.rss", "https://www.baogiaothong.vn/rss/home.rss",
    "https://baochinhphu.vn/kinh-te.rss", "https://vietstock.vn/761/kinh-te/vi-mo.rss",
]
INFRA = re.compile(r"cao tốc|đường sắt|sân bay|cảng|metro|vành đai|khu công nghiệp|"
                   r"nhà máy điện|thủy điện|nhiệt điện|điện gió|điện mặt trời|hầm|cầu |dự án", re.I)

SYSTEM = (
    "Bạn là chuyên gia hạ tầng Việt Nam. Từ danh sách TIÊU ĐỀ tin, hãy trích ra các "
    "DỰ ÁN HẠ TẦNG LỚN riêng biệt (giao thông: đường sắt/cao tốc/sân bay/cảng/metro/vành đai; "
    "năng lượng: nhà máy điện lớn; KCN lớn). BỎ QUA: tin doanh nghiệp/chứng khoán, giá vé, "
    "cầu/đường nhỏ lẻ (<500 tỷ), tin nước ngoài (TSMC...), chính sách chung không gắn dự án cụ thể, "
    "khu đô thị/nhà ở thương mại. Gộp các biến thể cùng một dự án làm một.\n"
    "Trả về DUY NHẤT một JSON array, mỗi phần tử: "
    '{"name":"tên chuẩn","category":"đường sắt|cao tốc|sân bay|cảng|metro|vành đai|điện|KCN",'
    '"importance":1-3 (3=mega quốc gia),"aliases":["cụm dò tin 1","cụm dò 2"]}. '
    "Chỉ JSON, không giải thích."
)


def gather_titles():
    known = build_alias_regex()
    seen, titles = set(), []
    with httpx.Client(headers=UA, timeout=15, follow_redirects=True) as cl:
        for u in FEEDS:
            try:
                items = ET.fromstring(cl.get(u).text).findall(".//item")
            except Exception:
                continue
            for it in items:
                t = (it.findtext("title") or "").strip()
                if t and INFRA.search(t) and t not in seen and not any(rx.search(t) for rx in known.values()):
                    seen.add(t)
                    titles.append(t)
    return titles


def llm_extract(titles):
    client = get_client()
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    resp = client.messages.create(
        model=MODEL, max_tokens=4096, system=build_system(SYSTEM),
        messages=[{"role": "user", "content": f"Trích dự án hạ tầng lớn từ {len(titles)} tiêu đề:\n\n{numbered}"}],
    )
    txt = (resp.content[0].text or "").strip()
    if txt.startswith("```"):
        txt = txt.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    m = re.search(r"\[.*\]", txt, re.S)
    return json.loads(m.group(0)) if m else []


def run(dry):
    titles = gather_titles()
    print(f"Tiêu đề hạ tầng thô: {len(titles)}")
    if not titles:
        return
    projs = llm_extract(titles)
    projs = [p for p in projs if isinstance(p, dict) and p.get("name") and p.get("aliases")]
    print(f"LLM trích: {len(projs)} dự án")
    for p in sorted(projs, key=lambda x: -x.get("importance", 0)):
        print(f"  {'★'*int(p.get('importance',1))} [{p.get('category','?')}] {p['name']}")
    if dry:
        return
    now = dt.datetime.now().isoformat()
    coll = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[OUT_DB][OUT_COLL]
    coll.create_index("key", unique=True)
    ops = []
    for p in projs:
        key = re.sub(r"[^a-z0-9]+", "_", p["name"].lower())[:40]
        ops.append(UpdateOne({"key": key}, {"$set": {
            "key": key, "label": p["name"], "category": p.get("category", ""),
            "importance": int(p.get("importance", 1)), "aliases": p["aliases"],
            "score": int(p.get("importance", 1)) * 10, "origin": "llm", "updated": now,
        }}, upsert=True))
    if ops:
        coll.bulk_write(ops)
        print(f"Đã ghi {len(ops)} ứng viên (LLM) vào {OUT_DB}.{OUT_COLL}.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    run(ap.parse_args().dry_run)
