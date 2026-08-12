# -*- coding: utf-8 -*-
"""
ingest_facebook.py — Nạp post Facebook (thu thập qua Claude for Chrome) vào pipeline.

Đầu vào: file JSON danh sách post, mỗi post:
  {"date":"YYYY-MM-DD", "text":"nội dung post", "url":"https://facebook.com/...", "page":"tên trang"}
Khớp alias 52 dự án (từ registry) → ghi dc_news.project_news_raw (source='facebook')
→ step2_newsflow.py đưa lên Dòng tin (badge 'facebook', để phân biệt tin báo chí).

  python ingest_facebook.py posts.json
  python ingest_facebook.py posts.json --dry-run
"""
import argparse
import datetime as dt
import json

from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri
from lib_projects import build_alias_regex, pid2tid


def run(path, dry):
    posts = json.load(open(path, encoding="utf-8"))
    alias = build_alias_regex()
    p2t = pid2tid()
    now = dt.datetime.now().isoformat()
    docs, skipped = [], 0
    for p in posts:
        text = (p.get("text") or "").strip()
        url = (p.get("url") or "").strip()
        if not text or not url:
            skipped += 1
            continue
        tids = sorted({p2t[pid] for pid, rx in alias.items() if pid in p2t and rx.search(text)})
        if not tids:
            skipped += 1
            continue
        date = (p.get("date") or now)[:10]
        docs.append({"url": url, "source": "facebook", "title": text[:180],
                     "description": text[:500], "body": text, "date": date,
                     "projects": tids, "page": p.get("page", ""), "scraped_at": now})
    print(f"Khớp dự án: {len(docs)} post · bỏ (không khớp/thiếu url): {skipped}")
    for d in docs:
        print(f"  {d['date']} · {d['projects']} · {d['title'][:60]}")
    if dry or not docs:
        return
    coll = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)["dc_news"]["project_news_raw"]
    ops = [UpdateOne({"url": d["url"]}, {"$setOnInsert": d}, upsert=True) for d in docs]
    res = coll.bulk_write(ops)
    print(f"Nạp {res.upserted_count} post FB mới vào project_news_raw. Chạy step2_newsflow.py để lên web.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="file JSON danh sách post")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(a.file, a.dry_run)
