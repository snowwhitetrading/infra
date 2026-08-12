# -*- coding: utf-8 -*-
"""ingest_additions.py — đọc registry_additions.json (do routine ghi) → upsert vào registry.
Idempotent: bỏ qua dự án đã có (theo id). Không ghi ngược file nên không gây loop CI."""
import json, os
from pymongo import MongoClient
from lib_db import mongo_uri

P = "registry_additions.json"
if not os.path.exists(P):
    print("Không có registry_additions.json"); raise SystemExit
adds = json.load(open(P, encoding="utf-8"))
col = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)["dc_commodity"]["Infra_Projects_Registry"]
tid = max([d.get("tid", 0) or 0 for d in col.find({}, {"tid": 1})] + [0])
n = 0
for a in adds:
    pid = a.get("id")
    if not pid or not a.get("aliases") or col.find_one({"id": pid}):
        continue
    tid += 1
    col.update_one({"id": pid}, {"$set": {"id": pid, "name": a["name"], "aliases": a["aliases"],
        "tid": tid, "active": True, "origin": "routine", "group": a.get("group", "Khác")}}, upsert=True)
    n += 1
    print(f"  + {a['name']} (tid={tid})")
print(f"Ingest additions: thêm {n} dự án mới từ routine.")
