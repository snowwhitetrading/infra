# -*- coding: utf-8 -*-
"""add_project.py — thêm 1 dự án vào Infra_Projects_Registry (tid tự cấp).
  python add_project.py <id> "<tên>" "<alias1>" "<alias2>" ...
"""
import sys
from pymongo import MongoClient
from lib_db import mongo_uri
if len(sys.argv) < 4:
    sys.exit('Cách dùng: python add_project.py <id> "<tên>" "<alias1>" ...')
pid, name, aliases = sys.argv[1], sys.argv[2], sys.argv[3:]
col = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)["dc_commodity"]["Infra_Projects_Registry"]
tid = max([d.get("tid", 0) or 0 for d in col.find({}, {"tid": 1})] + [0]) + 1
col.update_one({"id": pid}, {"$set": {"id": pid, "name": name, "aliases": aliases,
    "tid": tid, "active": True, "origin": "chat", "group": "Khác"}}, upsert=True)
print(f"Đã thêm '{name}' (id={pid}, tid={tid}) vào registry. Lần scrape CI sau tự có tin.")
