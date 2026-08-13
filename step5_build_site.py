# -*- coding: utf-8 -*-
"""
step5_build_site.py — Dựng trang theo dõi tiến độ từ DB (nguồn sự thật).

Đọc dc_commodity.Infra_Project_Tracker → chèn GROUPS + P vào template HTML →
xuất vn-infra-tracker.built.html (self-contained, mở trực tiếp được).

  python step5_build_site.py                 # dựng lại từ DB
  python step5_build_site.py --out foo.html  # đổi tên file ra
"""
import argparse, csv, datetime as dt, json, os, sys
from pymongo import MongoClient

from lib_db import mongo_uri

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "vn-infra-tracker.template.html")
MONGO_URI = mongo_uri()
DB, COLL = "dc_commodity", "Infra_Project_Tracker"
NEWSFLOW_COLL = "Infra_Newsflow"   # nguồn ĐỘC LẬP với progress (do step3_newsflow.py ghi)


SITEKEY2TID = {"apec_center": 1, "pq_airport": 2, "bai_dat_do": 3, "nui_ong_quan": 4,
               "pq_tram": 5, "rach_chiec": 6, "gb_road_hn": 7, "gb_road_bn": 8,
               "cangio_depot": 9, "halong_depot": 10, "gia_binh": 11}

# Nhóm cho dự án NGOÀI 18 curated (I–IV giữ nguyên) — droplist tab Tiến độ.
CAT_META = [   # (id, tên hiển thị, meta) — thứ tự trong droplist
    ("Hà Nội", "Hà Nội / Vùng Thủ đô", "dự án tại Hà Nội"),
    ("TP.HCM", "TP.HCM", "dự án tại TP.HCM"),
    ("Hải Phòng", "Hải Phòng – Quảng Ninh", "dự án tại Hải Phòng"),
    ("Long Thành", "Cụm Long Thành", "sân bay Long Thành & kết nối"),
    ("Cao tốc", "Cao tốc & vành đai", "cao tốc liên tỉnh"),
    ("Cầu cảng", "Cầu & cảng", "cầu, hầm, cảng biển"),
    ("Sân bay", "Sân bay", "cảng hàng không"),
    ("Khác", "Khác", "hạ tầng khác"),
]


def categorize(name, loc=""):
    """Gán dự án (ngoài curated) vào 1 nhóm droplist: hub địa lý ưu tiên, rồi theo loại."""
    t = (name + " " + (loc or "")).lower()
    if "long thành" in t:
        return "Long Thành"
    if "hà nội" in t or "thủ đô" in t:
        return "Hà Nội"
    if "tp.hcm" in t or "tphcm" in t or "hồ chí minh" in t:
        return "TP.HCM"
    if "hải phòng" in t:
        return "Hải Phòng"
    if "sân bay" in t or "hàng không" in t:
        return "Sân bay"
    if "cầu" in t or "hầm" in t or "cảng" in t:
        return "Cầu cảng"
    if "cao tốc" in t or "vành đai" in t:
        return "Cao tốc"
    return "Khác"


def fetch_satellite(client):
    """Đọc satellite_export/manifest.csv -> {tid: [{month,date,cloud,ok,file}]}.
    site_key khớp id registry (ảnh mới từ export_satellite.py) hoặc SITEKEY2TID (11 site cũ)."""
    path = os.path.join(HERE, "satellite_export", "manifest.csv")
    if not os.path.exists(path):
        print("  (không có satellite_export/manifest.csv — bỏ qua tab Vệ tinh)")
        return {}
    reg_id2tid = {d["id"]: d["tid"] for d in
                  client["dc_commodity"]["Infra_Projects_Registry"].find({}, {"id": 1, "tid": 1})
                  if d.get("tid")}
    data = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sk = row.get("site_key")
            tid = reg_id2tid.get(sk) or SITEKEY2TID.get(sk)
            if not tid:
                continue
            data.setdefault(str(tid), []).append({
                "month": row["month"], "date": row["capture_date"],
                "cloud": round(float(row["aoi_cloud_pct"])),
                "ok": row["status"] == "OK",
                "file": "satellite_export/" + row["file"],
            })
    for tid in data:
        data[tid].sort(key=lambda x: x["month"])
    return data


def fetch_newsflow(client, projects):
    """Dòng tin từng bài — đọc thẳng từ Infra_Newsflow (tách khỏi progress/digest)."""
    from lib_projects import project_names_by_tid
    tid2name = dict(project_names_by_tid())              # registry (gồm dự án mới ngoài Gantt)
    tid2name.update({p["id"]: p["name"] for p in projects})   # tên curated trên tracker ưu tiên
    out = []
    for doc in client[DB][NEWSFLOW_COLL].find({}):
        for tid in doc.get("projects", []):
            out.append({"date": doc.get("date", ""), "pname": tid2name.get(tid, ""),
                        "summary": doc.get("title", ""), "source": doc.get("source", "?"),
                        "url": doc.get("url", "")})
    out.sort(key=lambda n: n["date"], reverse=True)
    return out


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


def fetch_mappts(client):
    """Điểm bản đồ cho các dự án có toạ độ trong registry."""
    def kind(n):
        n = n.lower()
        if "sân bay" in n or "hàng không" in n:
            return "airport"
        if "metro" in n or "đường sắt" in n or "tàu điện" in n:
            return "rail"
        if "cảng" in n:
            return "port"
        if "cầu" in n or "hầm" in n:
            return "bridge"
        if "cao tốc" in n or "vành đai" in n or "đường" in n:
            return "road"
        return "other"
    out = []
    for p in client["dc_commodity"]["Infra_Projects_Registry"].find(
            {"active": True, "lat": {"$exists": True}}):
        rec = {"lat": p["lat"], "lon": p["lon"], "name": p.get("name", ""),
               "tid": p.get("tid"), "kind": kind(p.get("name", ""))}
        if p.get("geo"):                       # line/area khoanh vùng (nếu đã nhập toạ độ)
            rec["geo"] = p["geo"]
        out.append(rec)
    return out


def build_auto_rows(client, projects):
    """Sinh dòng Gantt SƠ BỘ cho dự án trong registry chưa có trên Gantt (từ newsflow).
    1 thanh 'thi công' theo khoảng tin + mốc 'auto' từ tiêu đề tin (dedup theo tháng)."""
    from collections import defaultdict
    from lib_projects import load_registry
    curated = {p["id"] for p in projects}
    reg_loc = {d["id"]: d.get("location", "") for d in
               client["dc_commodity"]["Infra_Projects_Registry"].find({}, {"id": 1, "location": 1})}
    nf = defaultdict(list)
    for d in client[DB][NEWSFLOW_COLL].find({}):
        m = (d.get("date") or "")[:7]
        if len(m) != 7:
            continue
        for tid in d.get("projects", []):
            nf[tid].append({"date": m, "title": d.get("title", ""), "src": d.get("source", "?")})
    auto = []
    for p in load_registry(force=True):
        tid = p.get("tid")
        if not tid or tid in curated or tid not in nf:
            continue
        items = sorted(nf[tid], key=lambda x: x["date"])
        seen, marks = set(), []
        for x in items:
            if x["date"] in seen:
                continue
            seen.add(x["date"])
            marks.append({"date": x["date"], "type": "ms", "label": x["title"],
                          "tier": "auto", "src": x["src"] + " · tự động"})
        loc = reg_loc.get(p["id"], "")
        first, last = items[0]["date"], items[-1]["date"]
        today = dt.date.today().strftime("%Y-%m")
        # KHÔNG có dữ liệu hạn hoàn thành → thanh chỉ kéo tới HIỆN TẠI (không bịa endpoint tương lai).
        # Đặc tới mốc tin gần nhất (doneTo=last), dashed tới nay + chevron › = đang thi công, chưa rõ hạn.
        end = max(last, today)
        auto.append({
            "id": tid, "g": categorize(p["name"], loc), "name": p["name"], "status": "thi công",
            "owner": p.get("group", ""), "loc": loc,
            "phases": [{"kind": "build", "from": first, "to": end,
                        "state": "ongoing", "doneTo": last}],
            "marks": marks[-30:], "items": [], "huyDong": 0, "capex": [],
        })
    return auto


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
    print(f"Đọc từ DB: {len(groups)} nhóm · {len(projects)} dự án curated")

    auto = build_auto_rows(c, projects)
    if auto:
        used = {p["g"] for p in auto}
        for cid, cname, cmeta in CAT_META:
            if cid in used:
                groups = groups + [{"id": cid, "name": cname, "meta": cmeta, "huyDong": 0}]
        projects = projects + auto
        print(f"  + {len(auto)} dòng Gantt tự động ({len(used)} nhóm: {', '.join(sorted(used))})")

    tpl = open(TEMPLATE, encoding="utf-8").read()
    old_g = extract_array(tpl, "const GROUPS")
    old_p = extract_array(tpl, "const P =")
    new_g = json.dumps(groups, ensure_ascii=False)
    new_p = json.dumps(projects, ensure_ascii=False)

    newsflow = fetch_newsflow(c, projects)
    print(f"Newsflow: {len(newsflow)} tin")
    satellite = fetch_satellite(c)
    print(f"Vệ tinh: {len(satellite)} dự án có ảnh")
    mappts = fetch_mappts(c)
    print(f"Bản đồ: {len(mappts)} điểm dự án")

    out = tpl.replace(old_g, new_g, 1).replace(old_p, new_p, 1)
    out = out.replace("const NEWSFLOW = []", "const NEWSFLOW = " + json.dumps(newsflow, ensure_ascii=False), 1)
    out = out.replace("const SATELLITE = {}", "const SATELLITE = " + json.dumps(satellite, ensure_ascii=False), 1)
    out = out.replace("const MAPPTS = []", "const MAPPTS = " + json.dumps(mappts, ensure_ascii=False), 1)
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
