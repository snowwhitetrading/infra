# -*- coding: utf-8 -*-
"""
retag_news.py — TỰ GẮN LẠI tin cũ khi có dự án MỚI hoặc bí danh ĐỔI.

Vì sao cần: tin chỉ được gắn dự án (field `projects`) lúc scrape. Dự án thêm sau, hoặc bí danh sửa sau,
sẽ không bao giờ nhận lại các bài đã nằm trong kho → dự án trông như "không có tin" dù kho có hàng chục bài
(vd Trục đại lộ cảnh quan sông Hồng: 4 tin → 197 tin sau khi gắn lại).

Cách làm (deterministic, không AI, chỉ THÊM không gỡ):
  • Mỗi dự án trong registry lưu `retag_hash` = băm bộ bí danh ở lần gắn gần nhất.
    Băm khác (dự án mới / bí danh đổi) → dự án "chờ gắn lại".
  • Quét TIÊU ĐỀ trong dc_news.project_news_raw và dc_news.unmatched_raw (bài hạ tầng chưa khớp ai) bằng
    regex bí danh của các dự án chờ → $addToSet tid. Bài ở unmatched_raw khớp thì chuyển sang project_news_raw.
  • Đẩy luôn các bài vừa gắn vào Infra_Newsflow (cùng logic step3) — vì step3 hằng giờ chỉ xét 30 ngày gần đây.

  python retag_news.py              # chỉ dự án mới / bí danh đổi (chạy hằng giờ, thường 0 dự án → thoát ngay)
  python retag_news.py --all        # gắn lại toàn bộ dự án
  python retag_news.py --tids 1273 1190
  python retag_news.py --dry-run
  python retag_news.py --baseline   # lần đầu: lấy hiện trạng làm mốc (bí danh rộng sẵn có không bị quét lại đồng loạt)
"""
import argparse
import datetime as dt
import hashlib
import re

from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri
from lib_marks import is_relevant, news_tag
from lib_projects import _guard_alias

SINCE = "2025-01-01"


def _hash(aliases):
    return hashlib.sha1("\n".join(sorted({a.strip().lower() for a in aliases if a})).encode("utf-8")).hexdigest()[:16]


def run(a):
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    reg = c["dc_commodity"]["Infra_Projects_Registry"]
    pend = {}
    for d in reg.find({"active": {"$ne": False}}, {"tid": 1, "name": 1, "aliases": 1, "retag_hash": 1}):
        tid, al = d.get("tid"), [x for x in (d.get("aliases") or []) if x]
        if not tid or not al:
            continue
        h = _hash(al)
        if a.tids:
            if tid not in a.tids:
                continue
        elif not a.all and d.get("retag_hash") == h:
            continue
        pend[tid] = (d["_id"], h, d.get("name", ""),
                     re.compile("|".join(sorted({_guard_alias(x) for x in al}, key=len, reverse=True)), re.I))
    print(f"Dự án cần gắn lại tin (mới / bí danh đổi): {len(pend)}", flush=True)
    if not pend:
        return
    if a.baseline:      # lấy hiện trạng làm mốc: KHÔNG quét, chỉ ghi băm → từ nay chỉ dự án mới / bí danh đổi mới bị gắn lại
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        reg.bulk_write([UpdateOne({"_id": _id}, {"$set": {"retag_hash": h, "retag_at": now}})
                        for _id, h, _, _ in pend.values()])
        print(f"Đã lấy mốc cho {len(pend)} dự án (không gắn lại).", flush=True)
        return
    raw, unm = c["dc_news"]["project_news_raw"], c["dc_news"]["unmatched_raw"]
    per, ops, touched, moved = {}, [], [], []
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for x in raw.find({"date": {"$gte": SINCE}}, {"title": 1, "projects": 1}):
        cur, t = set(x.get("projects") or []), x.get("title") or ""
        add = [tid for tid, (_, _, _, rx) in pend.items() if tid not in cur and rx.search(t)]
        if add:
            ops.append(UpdateOne({"_id": x["_id"]}, {"$addToSet": {"projects": {"$each": add}}}))
            touched.append(x["_id"])
            for tid in add:
                per[tid] = per.get(tid, 0) + 1
    for x in unm.find({"date": {"$gte": SINCE}}, {"_id": 0, "_ts": 0}):
        t = x.get("title") or ""
        add = [tid for tid, (_, _, _, rx) in pend.items() if rx.search(t)]
        if add and x.get("url"):
            doc = {k: v for k, v in x.items() if k != "projects"}
            ops.append(UpdateOne({"url": x["url"]}, {"$setOnInsert": {**doc, "retagged_at": now},
                                                     "$addToSet": {"projects": {"$each": add}}}, upsert=True))
            moved.append(x["url"])
            for tid in add:
                per[tid] = per.get(tid, 0) + 1
    names = {tid: v[2] for tid, v in pend.items()}
    for tid, n in sorted(per.items(), key=lambda kv: -kv[1])[:40]:
        print(f"  +{n:<4} tid {tid} {names[tid][:60]}", flush=True)
    print(f"Gắn thêm: {sum(per.values())} lượt · {len(touched)} bài trong kho · {len(moved)} bài từ pool chưa khớp", flush=True)
    if a.dry_run:
        return
    for i in range(0, len(ops), 1000):
        raw.bulk_write(ops[i:i + 1000], ordered=False)
    if moved:
        unm.delete_many({"url": {"$in": moved}})
    # đẩy các bài vừa gắn vào Infra_Newsflow (logic step3: bỏ rác, giữ mọi tin về dự án)
    nf, nops = c["dc_commodity"]["Infra_Newsflow"], []
    q = {"$or": [{"_id": {"$in": touched}}, {"url": {"$in": moved}}]} if (touched or moved) else None
    for doc in (raw.find(q) if q else []):
        title = (doc.get("title") or "").strip()
        if doc.get("source") != "facebook" and not is_relevant(title + " " + (doc.get("description") or "")):
            continue
        url = doc.get("url") or str(doc["_id"])
        nops.append(UpdateOne({"url": url}, {"$set": {
            "url": url, "date": (doc.get("date") or "")[:10], "dt": doc.get("dt") or "",
            "source": doc.get("source", "?"), "title": title, "projects": doc.get("projects") or [],
            "tag": news_tag(title)}}, upsert=True))
    for i in range(0, len(nops), 1000):
        nf.bulk_write(nops[i:i + 1000], ordered=False)
    reg.bulk_write([UpdateOne({"_id": _id}, {"$set": {"retag_hash": h, "retag_at": now}})
                    for _id, h, _, _ in pend.values()])
    print(f"Đã ghi · đưa {len(nops)} bài vào Infra_Newsflow · đánh dấu {len(pend)} dự án đã gắn.", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="gắn lại toàn bộ dự án")
    ap.add_argument("--tids", type=int, nargs="*", help="chỉ các tid này")
    ap.add_argument("--baseline", action="store_true", help="chỉ ghi mốc bí danh hiện tại, không quét (chạy 1 lần khi mới bật)")
    ap.add_argument("--dry-run", action="store_true")
    run(ap.parse_args())
