# -*- coding: utf-8 -*-
"""
step3_newsflow.py — Dòng tin infra từ project_news_raw (do step1 scrape trực tiếp 16 báo).

ĐỘC LẬP với dc_news. Deterministic, KHÔNG cần AI. Đọc dc_news.project_news_raw (đã khớp
dự án, có field `projects`=[id tracker]) → lọc CỘT MỐC tiến độ (is_progress) →
ghi dc_commodity.Infra_Newsflow (1 doc/bài, upsert theo url).

  python step3_newsflow.py            # toàn bộ project_news_raw
  python step3_newsflow.py --days 30  # chỉ 30 ngày gần đây
"""
import argparse
import datetime as dt

from pymongo import MongoClient

from lib_db import mongo_uri
from lib_marks import is_progress

SRC_DB, SRC_COLL = "dc_news", "project_news_raw"
OUT_DB, OUT_COLL = "dc_commodity", "Infra_Newsflow"


def run(days=None, dry=False):
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    src = c[SRC_DB][SRC_COLL]
    coll = c[OUT_DB][OUT_COLL]
    coll.create_index("url", unique=True)
    coll.create_index("date")
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat() if days else None

    matched = added = 0
    for doc in src.find({}):
        date = (doc.get("date") or "")[:10]
        if cutoff and date and date < cutoff:
            continue
        title = (doc.get("title") or "").strip()
        desc = doc.get("description") or ""
        # lọc cột mốc: xét tiêu đề + mô tả (chặt, tránh bắt nhầm).
        # Post Facebook đã được lọc tay lúc thu thập → miễn lọc is_progress.
        if doc.get("source") != "facebook" and not is_progress("", title + " " + desc):
            continue
        tids = doc.get("projects") or []
        if not tids:
            continue
        matched += 1
        url = doc.get("url") or str(doc.get("_id"))
        rec = {"url": url, "date": date, "source": doc.get("source", "?"),
               "title": title, "projects": tids}
        if not dry:
            if coll.update_one({"url": url}, {"$setOnInsert": rec}, upsert=True).upserted_id:
                added += 1
    total = "dry" if dry else coll.estimated_document_count()
    print(f"Cột mốc: {matched} bài · thêm mới: {added} · tổng Infra_Newsflow: {total}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None, help="Chỉ N ngày gần đây (mặc định: tất cả)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(a.days, a.dry_run)
