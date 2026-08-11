# -*- coding: utf-8 -*-
"""add_project.py — thêm 1 dự án vào Infra_Projects_Registry (thăng hạng từ radar).
tid tự cấp (19, 20, ...). Sau khi thêm, pipeline tự theo dõi dự án này.

  python add_project.py rail_laocai "Đường sắt Lào Cai - Hà Nội - Hải Phòng" \
      "Lào Cai - Hà Nội - Hải Phòng" "đường sắt Lào Cai"
"""
import sys
from pymongo import MongoClient
from lib_db import mongo_uri

if len(sys.argv) < 4:
    sys.exit('Cách dùng: python add_project.py <id> "<tên>" "<alias1>" "<alias2>" ...')
pid, name, aliases = sys.argv[1], sys.argv[2], sys.argv[3:]
col = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)["dc_commodity"]["Infra_Projects_Registry"]
next_tid = max([d.get("tid", 0) or 0 for d in col.find({}, {"tid": 1})] + [0]) + 1
col.update_one({"id": pid}, {"$set": {
    "id": pid, "name": name, "aliases": aliases, "tid": next_tid,
    "active": True, "origin": "radar", "group": "Khác",
}}, upsert=True)
# gỡ khỏi bảng ứng viên nếu có
col.database["Infra_Project_Candidates"].delete_many({"label": {"$regex": aliases[0], "$options": "i"}})
print(f"Đã thêm '{name}' (id={pid}, tid={next_tid}, {len(aliases)} alias) vào registry. "
      f"Lần chạy step1 kế tiếp sẽ scrape dự án này.")
