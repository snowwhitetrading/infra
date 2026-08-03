# -*- coding: utf-8 -*-
"""
build_tracker.py — Dựng trang theo dõi tiến độ từ DB (nguồn sự thật).

Đọc dc_commodity.Infra_Project_Tracker → chèn GROUPS + P vào template HTML →
xuất vn-infra-tracker.built.html (self-contained, mở trực tiếp được).

  python build_tracker.py                 # dựng lại từ DB
  python build_tracker.py --out foo.html  # đổi tên file ra
"""
import argparse, json, os, sys
from pymongo import MongoClient

from infra_db import mongo_uri

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "vn-infra-tracker.template.html")
MONGO_URI = mongo_uri()
DB, COLL = "dc_commodity", "Infra_Project_Tracker"


def extract_array(text, anchor):
    """Lấy literal mảng [...] ngay sau anchor (quét cân bằng ngoặc, tôn trọng chuỗi/comment)."""
    i = text.index(anchor) + len(anchor)
    while text[i] != '[':
        i += 1
    start, depth, in_str, esc = i, 0, None, False
    while i < len(text):
        c = text[i]
        if in_str:
            if esc: esc = False
            elif c == '\\': esc = True
            elif c == in_str: in_str = None
        elif c in "'\"":
            in_str = c
        elif c == '/' and text[i+1:i+2] == '*':
            i = text.index('*/', i+2) + 2; continue
        elif c == '/' and text[i+1:i+2] == '/':
            i = text.index('\n', i+2); continue
        elif c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                return text[start:i+1]
        i += 1
    raise ValueError('không tìm thấy ngoặc đóng cho ' + anchor)


PROPOSED_CSS = """
/* mark đề xuất từ tin tức (chờ duyệt) — nét mờ, xám, tách khỏi mark đã duyệt */
.mk.tier-proposed{background:var(--ink3)!important;opacity:.4}
.mk.done.tier-proposed{background:transparent!important;border-bottom-color:var(--ink3)!important;opacity:.4}
</style>"""


def apply_proposed(out, client):
    """Chèn mark đề xuất (Infra_Project_Tracker_Proposed) vào từng dự án trong HTML đã build,
    gắn tier='proposed' để hiển thị mờ. Không đụng marks curated trong DB."""
    import json as _json
    prop = {d["id"]: d["proposed"] for d in client[DB]["Infra_Project_Tracker_Proposed"].find({})}
    if not prop:
        return out
    # nhúng bảng đề xuất + đoạn JS trộn vào mảng P sau khi P được khai báo
    inject = ("\n;(function(){var PROP=" + _json.dumps(prop, ensure_ascii=False) + ";"
              "P.forEach(function(p){var a=PROP[p.id];if(!a)return;"
              "a.forEach(function(m){p.marks.push({date:m.date,type:m.type,label:m.label,"
              "tier:'proposed',src:m.src+' · đề xuất',url:m.url});});});})();\n")
    out = out.replace("/* ---------- helpers ----------", inject + "\n/* ---------- helpers ----------", 1)
    # đăng ký tier 'proposed'
    out = out.replace("inferred:0, superseded:0};", "inferred:0, superseded:0, proposed:0};", 1)
    out = out.replace("superseded:'mốc cũ đã lùi'};",
                      "superseded:'mốc cũ đã lùi', proposed:'đề xuất từ tin (chờ duyệt)'};", 1)
    out = out.replace("</style>", PROPOSED_CSS, 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "vn-infra-tracker.built.html"))
    ap.add_argument("--with-proposed", action="store_true",
                    help="Phủ thêm mark đề xuất từ tin tức (mờ, chờ duyệt)")
    args = ap.parse_args()

    c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=20000)
    c.admin.command("ping")
    col = c[DB][COLL]

    gdoc = col.find_one({"_key": "groups"})
    if not gdoc:
        sys.exit("Chưa có groups trong DB — chạy nạp dữ liệu trước.")
    groups = gdoc["groups"]

    projects = list(col.find({"_key": "project"}, {"_id": 0, "_key": 0}).sort("id", 1))
    print(f"Đọc từ DB: {len(groups)} nhóm · {len(projects)} dự án")

    tpl = open(TEMPLATE, encoding="utf-8").read()
    old_g = extract_array(tpl, "const GROUPS")
    old_p = extract_array(tpl, "const P =")
    new_g = json.dumps(groups, ensure_ascii=False)
    new_p = json.dumps(projects, ensure_ascii=False)

    out = tpl.replace(old_g, new_g, 1).replace(old_p, new_p, 1)
    # dấu vết build để biết trang đang chạy bằng dữ liệu DB
    out = out.replace("</title>", "</title>\n<!-- built from dc_commodity.Infra_Project_Tracker -->")

    if args.with_proposed:
        out = apply_proposed(out, c)
        print("Đã phủ mark đề xuất (chờ duyệt) từ Infra_Project_Tracker_Proposed")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"Đã dựng: {args.out}")


if __name__ == "__main__":
    main()
