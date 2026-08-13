# -*- coding: utf-8 -*-
"""
ingest_facebook_inbox.py — Xử lý post FB thô mà Claude for Chrome ghi vào
dc_news.facebook_inbox (qua connector DC_Database).

Đọc facebook_inbox {date,text,url,page} → khớp alias 52 dự án → ghi
dc_news.project_news_raw (source='facebook') → xoá post đã xử lý.
Chạy trong CI (deterministic, không cần Claude).

  python ingest_facebook_inbox.py
  python ingest_facebook_inbox.py --dry-run
"""
import argparse
import datetime as dt

from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri
from lib_projects import build_alias_regex, pid2tid


def run(dry):
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    inbox = c["dc_news"]["facebook_inbox"]
    raw = c["dc_news"]["project_news_raw"]
    alias = build_alias_regex()
    p2t = pid2tid()
    now = dt.datetime.now().isoformat()
    docs, done_ids, skipped = [], [], 0
    for p in inbox.find({}):
        done_ids.append(p["_id"])
        text = (p.get("text") or "").strip()
        url = (p.get("url") or "").strip()
        if not text or not url:
            skipped += 1
            continue
        tids = sorted({p2t[pid] for pid, rx in alias.items() if pid in p2t and rx.search(text)})
        if not tids:
            skipped += 1
            continue
        docs.append({"url": url, "source": "facebook", "title": text[:180],
                     "description": text[:500], "body": text,
                     "date": (p.get("date") or now)[:10], "projects": tids,
                     "page": p.get("page", ""), "scraped_at": now})
    print(f"Inbox FB: {len(done_ids)} post · khớp dự án: {len(docs)} · bỏ: {skipped}")
    for d in docs:
        print(f"  {d['date']} · {d['projects']} · {d['title'][:56]}")
    if dry:
        return
    if docs:
        raw.bulk_write([UpdateOne({"url": d["url"]}, {"$setOnInsert": d}, upsert=True) for d in docs])
    if done_ids:
        inbox.delete_many({"_id": {"$in": done_ids}})   # xoá post đã xử lý (kể cả không khớp)
    print(f"Đã nạp {len(docs)} post FB vào project_news_raw, dọn inbox {len(done_ids)} post.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    run(ap.parse_args().dry_run)
