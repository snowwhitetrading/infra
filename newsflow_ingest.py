# -*- coding: utf-8 -*-
"""
newsflow_ingest.py — Dòng tin infra ĐỘC LẬP với progress. Deterministic, KHÔNG cần AI.

Quét dc_news (4 báo) → dò 18 dự án theo alias → lọc CỘT MỐC tiến độ (RX_PROGRESS/RX_NOISE)
→ ghi dc_commodity.Infra_Newsflow (1 doc/bài, upsert theo url, không đè bài cũ).

Chạy được daily trong CI (chỉ đọc dc_news + ghi 1 collection, không gọi LLM).

  python newsflow_ingest.py            # quét toàn bộ
  python newsflow_ingest.py --days 30  # chỉ 30 ngày gần đây (cho CI daily cho nhanh)
"""
import argparse, datetime as dt, re
from pymongo import MongoClient
from infra_db import mongo_uri
from update_project_news import (build_alias_regex, article_text, doc_date,
                                 SOURCE_COLLECTIONS, CONTEXT_TERMS, NEWS_DB)
from propose_marks import is_progress, PID2TID

OUT_DB, OUT_COLL = "dc_commodity", "Infra_Newsflow"


def run(days=None, dry=False):
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    alias_rx = build_alias_regex()
    context_rx = re.compile("|".join(re.escape(t) for t in CONTEXT_TERMS), re.I)
    cutoff = (dt.datetime.utcnow() - dt.timedelta(days=days)) if days else None
    coll = c[OUT_DB][OUT_COLL]
    coll.create_index("url", unique=True)
    coll.create_index("date")

    matched = added = 0
    for cname, dfield in SOURCE_COLLECTIONS.items():
        q = {"$or": [{"title": context_rx}, {"body": context_rx},
                     {"description": context_rx}, {"tags": context_rx}]}
        for doc in c[NEWS_DB][cname].find(q):
            d = doc_date(doc, dfield)
            if not d or (cutoff and d < cutoff):
                continue
            pids = [pid for pid, rx in alias_rx.items() if rx.search(article_text(doc))]
            if not pids:
                continue
            title = (doc.get("title") or doc.get("headline") or "").strip()
            desc = doc.get("description") or doc.get("sapo") or ""
            # lọc cột mốc: chỉ xét TIÊU ĐỀ + sapo (chặt hơn, tránh bắt nhầm từ body)
            if not is_progress("", title + " " + desc):
                continue
            tids = sorted({PID2TID[p] for p in pids if p in PID2TID})
            if not tids:
                continue
            matched += 1
            url = doc.get("url") or str(doc.get("_id"))
            rec = {"url": url, "date": d.date().isoformat(),
                   "source": doc.get("source") or cname.split("_")[0],
                   "title": title, "projects": tids}
            if not dry:
                r = coll.update_one({"url": url}, {"$setOnInsert": rec}, upsert=True)
                if r.upserted_id:
                    added += 1
    total = "dry" if dry else coll.estimated_document_count()
    print(f"Khớp + cột mốc: {matched} bài · thêm mới: {added} · tổng Infra_Newsflow: {total}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None,
                    help="Chỉ quét N ngày gần đây (mặc định: tất cả)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(a.days, a.dry_run)
