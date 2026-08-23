# -*- coding: utf-8 -*-
"""
audit_aliases.py — Soi alias gây FALSE-POSITIVE trong Dòng tin.

Với mỗi (dự án, alias): đếm bài trong project_news_raw mà alias khớp (title/desc/body) NHƯNG
tiêu đề KHÔNG chia sẻ từ khoá nào với TÊN dự án ("lạc"). Alias có tỉ lệ lạc cao = nghi chung chung
(vd 'Tiên Sa', 'APEC 2027') → nên thay bằng tên riêng / cụm gắn địa danh, rồi re-match dọn 2 collection.

  python audit_aliases.py                 # ngưỡng mặc định: lạc >=5 và >=25% tổng khớp
  python audit_aliases.py --min 8 --rate 0.4
"""
import argparse
import re
from collections import defaultdict

from pymongo import MongoClient

from lib_db import mongo_uri

STOP = set("dự án của và - – đoạn".split())


def toks(s):
    return {w for w in re.sub(r"[^\w ]", " ", (s or "").lower()).split()
            if w not in STOP and len(w) > 1}


def run(a):
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    raw = c["dc_news"]["project_news_raw"]
    reg = c["dc_commodity"]["Infra_Projects_Registry"]
    name = {p["tid"]: p.get("name", "") for p in reg.find({}, {"tid": 1, "name": 1})}
    arx = defaultdict(list)
    for p in reg.find({"active": True}, {"tid": 1, "aliases": 1}):
        for al in p.get("aliases", []):
            arx[p["tid"]].append((al, re.compile(re.escape(al), re.I)))
    bad, tot = defaultdict(int), defaultdict(int)
    for d in raw.find({"projects": {"$ne": []}}, {"title": 1, "description": 1, "body": 1, "projects": 1}):
        ti = d.get("title") or ""
        tt = toks(ti)
        ft = " ".join(str(d.get(f, "") or "") for f in ("title", "description", "body"))
        for tid in d.get("projects", []):
            nm = toks(name.get(tid, ""))
            for al, r in arx.get(tid, []):
                if r.search(ft):
                    tot[(tid, al)] += 1
                    if tt and not (tt & nm):
                        bad[(tid, al)] += 1
    rows = [(bad[k], tot[k], k) for k in bad
            if bad[k] >= a.min and bad[k] / max(tot[k], 1) >= a.rate]
    rows.sort(reverse=True)
    print(f"Alias nghi false-positive (lạc >= {a.min} và >= {int(a.rate*100)}% khớp):")
    if not rows:
        print("  (không có — alias hiện sạch)")
    for b, tt, (tid, al) in rows:
        print(f"  tid{tid:<4} [{al[:36]:36}] lạc {b:>3}/{tt:<4} ({100*b//tt}%) · {name.get(tid,'?')[:24]}")
    print("\nSửa: cập nhật aliases trong Infra_Projects_Registry (tên riêng/cụm gắn địa danh), rồi "
          "re-match $pull tid khỏi bài không còn khớp trên CẢ project_news_raw VÀ Infra_Newsflow, "
          "xoá Newsflow projects rỗng.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=5, help="số bài lạc tối thiểu để cảnh báo")
    ap.add_argument("--rate", type=float, default=0.25, help="tỉ lệ lạc/tổng tối thiểu")
    run(ap.parse_args())
