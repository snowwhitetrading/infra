# -*- coding: utf-8 -*-
"""
clean_news_noise.py — Dọn TIN NHIỄU gắn vào dự án (Claude phán, KHÔNG regex).

Tin lấy theo tên dự án (nhất là qua Google News) hay dính bài chỉ NHẮC ĐỊA DANH mà không nói về dự án:
"Kết quả xổ số TP HCM, Long An", "Giới trẻ đổ về bến Bạch Đằng", "Phát hiện bộ xương ở Quảng trường ..."...
Máy không phân biệt được bằng từ khoá → để Claude đọc tiêu đề và phán từng cặp (bài, dự án).

  python clean_news_noise.py --prepare --out noise_batches [--since 2026-11-23] [--batch 350]
      → gom các cặp (bài, dự án) CHƯA soát (thiếu cờ noise_checked) thành noise_batches/batch_N.json
  (mỗi lô)  cat batch_N.json | claude -p "$(cat news_noise_prompt.txt)" > raw_N.txt        # xem run_news_noise.sh
  python clean_news_noise.py --apply noise_batches
      → gỡ dự án khỏi bài bị phán BỎ (kho + Infra_Newsflow), ghi `not_projects` vào bài để các lượt quét /
        gắn lại sau KHÔNG gắn lại nữa; đặt cờ noise_checked cho mọi bài đã soát.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re

from bson import ObjectId
from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri


def _cols():
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    return (c["dc_commodity"]["Infra_Projects_Registry"], c["dc_news"]["project_news_raw"],
            c["dc_commodity"]["Infra_Newsflow"])


def prepare(a):
    reg, raw, _ = _cols()
    names = {d["tid"]: d.get("name", "") for d in reg.find({}, {"tid": 1, "name": 1}) if d.get("tid")}
    q = {"noise_checked": {"$exists": False}, "projects.0": {"$exists": True}}
    if a.source:
        q["source"] = {"$regex": "^" + re.escape(a.source)}
    if a.since:
        q["scraped_at"] = {"$gte": a.since}
    pairs = []
    for d in raw.find(q, {"title": 1, "projects": 1}):
        for tid in d.get("projects") or []:
            if tid in names and (d.get("title") or "").strip():
                pairs.append((str(d["_id"]), tid, d["title"].strip()))
    pairs.sort(key=lambda p: (p[1], p[2]))        # cùng dự án đứng cạnh nhau → Claude dễ so
    os.makedirs(a.out, exist_ok=True)
    for f in glob.glob(os.path.join(a.out, "*")):
        os.remove(f)
    nb = 0
    for i in range(0, len(pairs), a.batch):
        chunk = pairs[i:i + a.batch]
        items = [[i + k, tid, title] for k, (_, tid, title) in enumerate(chunk)]
        json.dump({"projects": {str(t): names[t] for t in sorted({p[1] for p in chunk})}, "items": items},
                  open(os.path.join(a.out, f"batch_{nb}.json"), "w", encoding="utf-8"), ensure_ascii=False)
        json.dump({str(i + k): [oid, tid] for k, (oid, tid, _) in enumerate(chunk)},
                  open(os.path.join(a.out, f"map_{nb}.json"), "w", encoding="utf-8"))
        nb += 1
    print(f"{len(pairs)} cặp (bài, dự án) chưa soát → {nb} lô trong {a.out}")


def apply(a):
    reg, raw, nf = _cols()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    drops, checked, nbad = {}, set(), 0
    for mf in sorted(glob.glob(os.path.join(a.apply, "map_*.json"))):
        n = re.search(r"map_(\d+)", mf).group(1)
        rf = os.path.join(a.apply, f"raw_{n}.txt")
        if not os.path.exists(rf):
            continue
        m = re.search(r"\{.*\}", open(rf, encoding="utf-8").read(), re.S)
        try:
            v = json.loads(m.group(0))["drop"]
        except Exception:
            nbad += 1
            print(f"  ! lô {n}: không đọc được kết quả — bỏ qua (các bài của lô này chưa bị coi là đã soát)")
            continue
        mp = json.load(open(mf, encoding="utf-8"))
        for oid, _ in mp.values():
            checked.add(oid)
        for k in v:
            if str(k) in mp:
                oid, tid = mp[str(k)]
                drops.setdefault(oid, set()).add(tid)
    npair = sum(len(v) for v in drops.values())
    print(f"Đã soát {len(checked)} bài · gỡ {npair} cặp (bài, dự án) trên {len(drops)} bài · {nbad} lô hỏng")
    if a.dry_run:
        for oid, tids in list(drops.items())[:40]:
            d = raw.find_one({"_id": ObjectId(oid)}, {"title": 1})
            print("  BỎ", sorted(tids), (d or {}).get("title", "")[:100])
        return
    ops, urls = [], {}
    for oid, tids in drops.items():
        ops.append(UpdateOne({"_id": ObjectId(oid)}, {"$pull": {"projects": {"$in": list(tids)}},
                                                      "$addToSet": {"not_projects": {"$each": list(tids)}}}))
    for i in range(0, len(ops), 1000):
        raw.bulk_write(ops[i:i + 1000], ordered=False)
    for d in raw.find({"_id": {"$in": [ObjectId(o) for o in drops]}}, {"url": 1, "projects": 1}):
        urls[d.get("url") or str(d["_id"])] = d.get("projects") or []
    nops = [UpdateOne({"url": u}, {"$set": {"projects": p}}) for u, p in urls.items() if p]
    gone = [u for u, p in urls.items() if not p]
    for i in range(0, len(nops), 1000):
        nf.bulk_write(nops[i:i + 1000], ordered=False)
    if gone:
        nf.delete_many({"url": {"$in": gone}})
    ids = [ObjectId(o) for o in checked]
    for i in range(0, len(ids), 5000):
        raw.update_many({"_id": {"$in": ids[i:i + 5000]}}, {"$set": {"noise_checked": now}})
    print(f"Đã ghi · {len(gone)} bài không còn dự án nào → rút khỏi Dòng tin.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--apply", metavar="DIR")
    ap.add_argument("--out", default="noise_batches")
    ap.add_argument("--since", help="chỉ bài scraped_at ≥ mốc này (ISO)")
    ap.add_argument("--source", default="gnews", help="tiền tố nguồn (mặc định gnews; '' = mọi nguồn)")
    ap.add_argument("--batch", type=int, default=350)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.prepare:
        prepare(a)
    elif a.apply:
        apply(a)
    else:
        ap.print_help()
