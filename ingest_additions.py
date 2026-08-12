# -*- coding: utf-8 -*-
"""ingest_additions.py — nạp file registry_additions.json (nếu có) vào registry.
An toàn: không có file thì bỏ qua. (Dùng cho luồng đề xuất dự án qua file, nếu cần.)"""
import json, os
from pymongo import MongoClient
from lib_db import mongo_uri
F = "registry_additions.json"
if not os.path.exists(F):
    print("no additions"); raise SystemExit
col = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)["dc_commodity"]["Infra_Projects_Registry"]
tid = max([d.get("tid", 0) or 0 for d in col.find({}, {"tid": 1})] + [0])
added = 0
for p in json.load(open(F, encoding="utf-8")):
    if not p.get("id") or not p.get("aliases") or col.find_one({"id": p["id"]}):
        continue
    tid += 1
    col.update_one({"id": p["id"]}, {"$set": {"id": p["id"], "name": p.get("name", p["id"]),
        "aliases": p["aliases"], "tid": tid, "active": True, "origin": "additions",
        "group": p.get("group", "Khác")}}, upsert=True)
    added += 1
print(f"Nạp {added} dự án từ {F} vào registry.")
