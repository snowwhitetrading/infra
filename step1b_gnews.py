# -*- coding: utf-8 -*-
"""
step1b_gnews.py — Scrape tin dự án qua GOOGLE NEWS RSS (gom MỌI báo VN, gồm báo địa phương 34 tỉnh).

Tìm theo TÊN dự án (alias) trong registry → mỗi query trả tin từ nhiều nguồn (Tuổi Trẻ, Lao Động,
Hànộimới, báo tỉnh…). Hỗ trợ HISTORICAL qua cửa sổ thời gian (after:/before:) để dựng DB tiến độ.
Ghi dc_news.project_news_raw (source='gnews:<báo>', projects=[tid]).

  python step1b_gnews.py                    # tin gần đây, tất cả dự án
  python step1b_gnews.py --history 2025-01  # backfill từ 2025-01 tới nay (cửa sổ 3 tháng)
  python step1b_gnews.py --limit 5 --dry-run
"""
import argparse
import datetime as dt
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx
from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

DB, COLL = "dc_news", "project_news_raw"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"}
GNEWS = "https://news.google.com/rss/search?q={q}&hl=vi&gl=VN&ceid=VN:vi"


def _toks(s):
    return set(t for t in re.sub(r"[^a-z0-9 ]", " ",
               unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()).split()
               if len(t) > 2)


def _relevant(alias, text):
    """Bài có thực sự nhắc dự án? ≥60% token đặc trưng của alias xuất hiện trong tiêu đề+mô tả."""
    at = _toks(alias)
    if len(at) < 2:
        return True                       # alias quá ngắn → tin query
    return len(at & _toks(text)) / len(at) >= 0.6


def _date(s):
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (ValueError, TypeError):
        return ""


def _windows(start):
    """Cửa sổ 3 tháng từ start (YYYY-MM) tới nay → [(after,before)]."""
    y, m = int(start[:4]), int(start[5:7])
    today = dt.date.today()
    out = []
    cur = dt.date(y, m, 1)
    while cur < today:
        nxt = cur + dt.timedelta(days=92)
        out.append((cur.isoformat(), min(nxt, today).isoformat()))
        cur = nxt
    return out


def query_gnews(client, alias, after=None, before=None):
    q = f'"{alias}"'
    if after:
        q += f" after:{after} before:{before}"
    try:
        r = client.get(GNEWS.format(q=quote(q)), timeout=25, follow_redirects=True, headers=UA)
        items = ET.fromstring(r.text).findall(".//item")
    except Exception:
        return []
    out = []
    for it in items:
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "gnews").strip()
        # bỏ đuôi " - Nguồn" trong title của Google News
        title = re.sub(r"\s*-\s*" + re.escape(src) + r"\s*$", "", title).strip()
        out.append({"title": title, "source": "gnews:" + src,
                    "url": (it.findtext("link") or "").strip(),
                    "description": re.sub("<[^>]+>", " ", it.findtext("description") or "")[:400],
                    "date": _date(it.findtext("pubDate", ""))})
    return out


def run(a):
    reg = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)["dc_commodity"]["Infra_Projects_Registry"]
    projects = [p for p in reg.find({"active": True}, {"tid": 1, "name": 1, "aliases": 1}).sort("tid", 1)
                if p.get("tid")]
    if a.limit:
        projects = projects[:a.limit]
    windows = _windows(a.history) if a.history else [(None, None)]
    print(f"{len(projects)} dự án × {len(windows)} cửa sổ" + (f" (history từ {a.history})" if a.history else " (gần đây)"))

    docs = {}
    with httpx.Client(headers=UA, follow_redirects=True) as cl:
        for i, p in enumerate(projects, 1):
            alias = max((p.get("aliases") or [p["name"]]), key=len)   # alias dài nhất (đặc trưng nhất)
            for after, before in windows:
                for r in query_gnews(cl, alias, after, before):
                    if not r["url"] or not _relevant(alias, r["title"] + " " + r["description"]):
                        continue
                    r["projects"] = [p["tid"]]
                    docs.setdefault(r["url"], r)
                time.sleep(0.15)
            if i % 25 == 0 or i == len(projects):
                print(f"  {i}/{len(projects)} dự án · {len(docs)} tin (unique)")

    print(f"Tổng tin gnews: {len(docs)}")
    if a.dry_run:
        for d in list(docs.values())[:15]:
            print(f"  {d['date']:10} {d['source'][:22]:22} {d['title'][:52]}")
        return
    col = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB][COLL]
    col.create_index("url", unique=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    ops = [UpdateOne({"url": d["url"]}, {"$setOnInsert": {**d, "scraped_at": now}}, upsert=True)
           for d in docs.values()]
    if ops:
        res = col.bulk_write(ops)
        print(f"Ghi mới {res.upserted_count} tin vào {DB}.{COLL} (tổng {col.estimated_document_count()}).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", metavar="YYYY-MM", help="Backfill lịch sử từ tháng này (cửa sổ 3 tháng)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    run(ap.parse_args())
