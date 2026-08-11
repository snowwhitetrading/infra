# -*- coding: utf-8 -*-
"""migrate_registry.py — đẩy 18 dự án (hardcode) vào dc_commodity.Infra_Projects_Registry.
Chạy 1 lần. Sau đó thêm dự án = thêm doc vào collection này (không sửa code)."""
from pymongo import MongoClient
from lib_db import mongo_uri
from lib_projects import PROJECTS
from lib_marks import PID2TID

c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
col = c["dc_commodity"]["Infra_Projects_Registry"]
col.create_index("id", unique=True)
col.create_index("active")
for p in PROJECTS:
    col.update_one({"id": p["id"]}, {"$set": {
        "id": p["id"], "tid": PID2TID.get(p["id"]), "name": p["name"],
        "group": p.get("group", ""), "developer": p.get("developer", ""),
        "location": p.get("location", ""), "aliases": p["aliases"],
        "active": True, "origin": "vsb",
    }}, upsert=True)
print("Infra_Projects_Registry:", col.count_documents({}), "dự án")
