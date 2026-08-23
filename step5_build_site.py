# -*- coding: utf-8 -*-
"""
step5_build_site.py — Dựng trang theo dõi tiến độ từ DB (nguồn sự thật).

Đọc dc_commodity.Infra_Project_Tracker → chèn GROUPS + P vào template HTML →
xuất vn-infra-tracker.built.html (self-contained, mở trực tiếp được).

  python step5_build_site.py                 # dựng lại từ DB
  python step5_build_site.py --out foo.html  # đổi tên file ra
"""
import argparse, csv, datetime as dt, html, json, os, re, sys
from pymongo import MongoClient


def _dec(d):
    """Giải mã HTML entity (&agrave;→à, &ocirc;→ô…) trong dict detail của cafeland."""
    return {html.unescape(k): html.unescape(v) for k, v in (d or {}).items()}


# Tỉnh (tên chuẩn hoá) → vùng, cho filter Vùng trên bản đồ
_REGION = {"Miền Bắc": ["Hà Nội", "Hải Phòng", "Quảng Ninh", "Bắc Ninh", "Bắc Giang", "Vĩnh Phúc",
                        "Phú Thọ", "Thái Nguyên", "Lạng Sơn", "Cao Bằng", "Hà Giang", "Tuyên Quang",
                        "Lào Cai", "Yên Bái", "Điện Biên", "Lai Châu", "Sơn La", "Hòa Bình", "Hưng Yên",
                        "Hải Dương", "Thái Bình", "Nam Định", "Hà Nam", "Ninh Bình", "Bắc Kạn"],
           "Miền Trung": ["Thanh Hóa", "Nghệ An", "Hà Tĩnh", "Quảng Bình", "Quảng Trị", "Thừa Thiên Huế",
                          "Đà Nẵng", "Quảng Nam", "Quảng Ngãi", "Bình Định", "Phú Yên", "Khánh Hòa",
                          "Ninh Thuận", "Bình Thuận", "Kon Tum", "Gia Lai", "Đắk Lắk", "Đắk Nông", "Lâm Đồng"],
           "Miền Nam": ["Hồ Chí Minh", "Bà Rịa - Vũng Tàu", "Bình Dương", "Bình Phước", "Đồng Nai",
                        "Tây Ninh", "Long An", "Tiền Giang", "Bến Tre", "Vĩnh Long", "Trà Vinh", "Đồng Tháp",
                        "An Giang", "Kiên Giang", "Cần Thơ", "Hậu Giang", "Sóc Trăng", "Bạc Liêu", "Cà Mau"]}
PROV_REGION = {p: r for r, ps in _REGION.items() for p in ps}


def _geo_of(name, loc=""):
    """Suy (vùng, tỉnh) từ tên+địa điểm — cho filter Vùng/Tỉnh tab Tiến độ (giống Bản đồ)."""
    low = (str(loc) + " " + str(name)).lower()
    if "tp.hcm" in low or "tphcm" in low or "hồ chí minh" in low or " hcm" in low:
        return "Miền Nam", "Hồ Chí Minh"
    for prov, reg in PROV_REGION.items():
        if prov.lower() in low:
            return reg, prov
    return "", ""

from lib_db import mongo_uri

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "vn-infra-tracker.template.html")
MONGO_URI = mongo_uri()
DB, COLL = "dc_commodity", "Infra_Project_Tracker"
NEWSFLOW_COLL = "Infra_Newsflow"   # nguồn ĐỘC LẬP với progress (do step3_newsflow.py ghi)

# CHỦ ĐẦU TƯ: chỉ 2 loại — TẬP ĐOÀN TƯ NHÂN (whitelist) hoặc "Nhà nước" (còn lại: state/tỉnh/EVN/unknown).
OWNER_OVERRIDE = {11: "Masterise", 102: "Vingroup", 20: "Nhà nước"}   # Gia Bình=Masterise · Cầu Cần Giờ=Vingroup · HSR Bắc-Nam=Nhà nước
_PRIVATE_OWNERS = [
    (re.compile(r"vinspeed|vingroup|vinhomes|\bvic\b"), "Vingroup"),
    (re.compile(r"sun ?group|mặt trời"), "Sun Group"),
    (re.compile(r"masterise|\bmai\b"), "Masterise"),
    (re.compile(r"đèo cả"), "Đèo Cả"),
    (re.compile(r"becamex"), "Becamex"),
    (re.compile(r"geleximco"), "Geleximco"),
    (re.compile(r"t ?& ?t|cienco ?-? ?4"), "T&T"),          # T&T và Cienco4 → chung T&T
    (re.compile(r"tasco"), "Tasco"),
    (re.compile(r"trung nam"), "Trung Nam"),
    (re.compile(r"xuân trường"), "Xuân Trường"),
    (re.compile(r"cường thuận"), "Cường Thuận"),
    (re.compile(r"đức long"), "Đức Long"),
    (re.compile(r"thaco|trường hải"), "Thaco"),
    (re.compile(r"him lam"), "Him Lam"),
    (re.compile(r"ecopark"), "Ecopark"),
    (re.compile(r"sovico"), "Sovico"),
    (re.compile(r"\bipp\b|hạnh nguyễn"), "IPP Group"),
    (re.compile(r"hateco"), "Hateco"),
    (re.compile(r"đại dũng"), "Đại Dũng"),
    (re.compile(r"\bmsc\b|maersk|\btil\b"), "MSC/Maersk"),
]


def canon_owner(o):
    low = (o or "").lower()
    for rx, nm in _PRIVATE_OWNERS:
        if rx.search(low):
            return nm
    return "Nhà nước"                                       # còn lại → Nhà nước


# Đổi TÊN từ Title-Case (mọi từ viết hoa) → proper text (chỉ từ đầu + tên riêng viết hoa).
_LOWER_WORDS = set("đường sắt tốc độ số sân bay tuyến nút giao vành đai ven biển quốc lộ tỉnh "
                   "nâng cấp mở rộng dự án đoạn nối dài kết trục đô thị xây dựng đầu tư hạ tầng cải tạo "
                   "giai với đến của và hầm nhẹ hàng không bộ trên khu".split())


def proper_case(name):
    ws = (name or "").split()
    out = []
    for i, w in enumerate(ws):
        core = re.sub(r"[^\wÀ-ỹ]", "", w).lower()
        out.append(w.lower() if i > 0 and core in _LOWER_WORDS else w)
    s = " ".join(out)
    return (s[:1].upper() + s[1:]) if s else s


SITEKEY2TID = {"apec_center": 1, "pq_airport": 2, "bai_dat_do": 3, "nui_ong_quan": 4,
               "pq_tram": 5, "rach_chiec": 6, "gb_road_hn": 7, "gb_road_bn": 8,
               "cangio_depot": 9, "halong_depot": 10, "gia_binh": 11}

# Nhóm tab Tiến độ — theo LOẠI HẠ TẦNG, khớp taxonomy tab Bản đồ (_infra_cat / ICON_INFRA).
CAT_META = [   # (id, tên hiển thị, meta) — thứ tự hiển thị loại hình
    ("Đường bộ", "Đường bộ", "cao tốc, quốc lộ, vành đai, tỉnh lộ"),
    ("Đường sắt/Metro", "Đường sắt / Metro", "đường sắt, metro, tàu điện"),
    ("Sân bay", "Sân bay", "cảng hàng không"),
    ("Kênh/Rạch", "Kênh / Rạch", "kênh, rạch, nạo vét"),
    ("Cầu/Hầm", "Cầu / Hầm", "cầu, hầm"),
    ("Nút giao", "Nút giao", "nút giao, ngã tư"),
    ("Cảng", "Cảng biển", "cảng biển, cảng sông"),
    ("Toà nhà", "Toà nhà", "trung tâm hội nghị, sân vận động, nhà thi đấu, công trình công cộng"),
]


def categorize(name, loc=""):
    """Nhóm dự án theo LOẠI HẠ TẦNG — cùng taxonomy với tab Bản đồ (_infra_cat)."""
    return _infra_cat(name)


def category_map(client):
    """tid -> nhóm droplist (categorize theo name+location trong registry)."""
    out = {}
    for d in client["dc_commodity"]["Infra_Projects_Registry"].find(
            {}, {"tid": 1, "name": 1, "location": 1}):
        if d.get("tid"):
            out[d["tid"]] = categorize(d.get("name", ""), d.get("location", ""))
    return out


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


def fetch_newsflow(client, projects, tid2cat):
    """Dòng tin từng bài — đọc thẳng từ Infra_Newsflow (tách khỏi progress/digest)."""
    from lib_projects import project_names_by_tid
    tid2name = dict(project_names_by_tid())              # registry (gồm dự án mới ngoài Gantt)
    tid2name.update({p["id"]: p["name"] for p in projects})   # tên curated trên tracker ưu tiên
    from lib_marks import news_tag
    out = []
    for doc in client[DB][NEWSFLOW_COLL].find({}):
        title = doc.get("title", "")
        tag = news_tag(title)
        dtv = doc.get("dt") or ""     # giờ đăng thật 'YYYY-MM-DDTHH:MM' nếu có
        for tid in doc.get("projects", []):
            out.append({"date": doc.get("date", ""), "dt": dtv, "pname": tid2name.get(tid, ""),
                        "summary": title, "source": doc.get("source", "?"), "tag": tag,
                        "url": doc.get("url", ""), "g": tid2cat.get(tid, "Khác")})
    # sort theo giờ thật khi có (dt), else theo ngày (đầu ngày); date rỗng → chìm cuối
    out.sort(key=lambda n: n["dt"] or (n["date"] + "T00:00" if n["date"] else ""), reverse=True)
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
               "tid": p.get("tid"), "kind": kind(p.get("name", "")),
               "g": categorize(p.get("name", ""), p.get("location", ""))}
        if p.get("geo"):                       # line/area khoanh vùng (nếu đã nhập toạ độ)
            rec["geo"] = p["geo"]
        out.append(rec)
    return out


_TC_KEEP = {"TPHCM", "TP.HCM", "QL", "ĐT", "HN", "KCN", "CCN", "ATC", "LRT", "BRT", "HCM", "APEC", "SLP"}


def _titlecase(s):
    """Chuẩn hoá tên IN HOA cafeland → Title Case (giữ viết tắt QL/ĐT/TPHCM + mã có số)."""
    out = []
    for w in (s or "").split():
        if w.upper() in _TC_KEEP:
            out.append(w.upper())
        elif re.search(r"\d", w):          # mã số: QL22, ĐT.983C, 2A → giữ nguyên
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


def _prov_norm(s):
    s = (s or "").strip()
    for p in ("TP. ", "TP.", "Thành phố ", "thành phố ", "Tỉnh ", "tỉnh "):
        if s.startswith(p):
            return s[len(p):].strip()
    return s


def _infra_cat(t):
    t = (t or "").lower()
    # đường nối/cao tốc/quốc lộ/vành đai TỚI sân bay vẫn là ĐƯỜNG BỘ (không phải sân bay)
    road = any(k in t for k in ("đường kết nối", "đường nối", "đường vào", "đường trục",
                                "đường liên", "cao tốc", "quốc lộ", "vành đai", " ql", "tỉnh lộ"))
    if not road and ("sân bay" in t or "hàng không" in t or "chkqt" in t
                     or "cất hạ cánh" in t or "khu bay" in t):
        return "Sân bay"
    if "metro" in t or "đường sắt" in t or "tàu điện" in t:
        return "Đường sắt/Metro"
    # toà nhà / công trình công cộng-thương mại (TT hội nghị, sân vận động, nhà thi đấu, nhà hát...)
    # xét TRƯỚC cầu/cảng/kênh vì tên có thể chứa địa danh "Rạch/Cầu…" (vd Liên hợp TT Rạch Chiếc)
    if any(k in t for k in ("hội nghị", "sân vận động", "svđ", "nhà thi đấu", "thể thao",
                            "thể dục", "nhà hát", "quảng trường", "triển lãm", "tòa nhà",
                            "toà nhà", "trụ sở", "cung văn hoá", "cung văn hóa",
                            "trung tâm hành chính", "khu hành chính", "trung tâm chính trị",
                            "công viên", "bảo tàng", "trung tâm văn hoá", "trung tâm văn hóa")):
        return "Toà nhà"
    # cao tốc/quốc lộ… mà tên có địa danh "Cầu X" (vd Pháp Vân - Cầu Giẽ) vẫn là Đường bộ
    if road and not re.match(r"^(cầu|hầm|cảng)\b", t):
        return "Đường bộ"
    if "cầu" in t or "hầm" in t:
        return "Cầu/Hầm"
    if "cảng" in t:
        return "Cảng"
    if "kênh" in t or "rạch" in t or "nạo vét" in t:
        return "Kênh/Rạch"
    if "nút giao" in t:
        return "Nút giao"
    return "Đường bộ"


def _point_class(t):
    """Điểm nổi bật cafeland: là DỰ ÁN (khu đô thị/nhà máy/KCN…) hay HẠ TẦNG thật (sân bay/cầu/cảng…)?
    → ('proj', loại dự án) hoặc ('infra', loại hạ tầng)."""
    tl = (t or "").lower()
    # HẠ TẦNG rõ ràng trước
    if "sân bay" in tl or "hàng không" in tl:
        return "infra", "Sân bay"
    if "metro" in tl or "đường sắt" in tl or "tàu điện" in tl:
        return "infra", "Đường sắt/Metro"
    # DỰ ÁN — công nghiệp / đô thị / du lịch...
    if any(k in tl for k in ("khu công nghiệp", "cụm công nghiệp", "ccn ", "nhà máy", "kcn",
                             "công nghệ cao", "công nghệ số", "nhà xưởng", "logistics",
                             "khu kinh tế", "phi thuế quan")):
        return "proj", "Khu công nghiệp"
    if "khu đô thị" in tl or "khu dân cư" in tl or "khu nhà ở" in tl or "tái định cư" in tl:
        return "proj", "Khu đô thị"
    if "nhà ở xã hội" in tl or "noxh" in tl:
        return "proj", "Nhà ở xã hội"
    if "chung cư" in tl or "căn hộ" in tl or "tòa nhà" in tl or "toà nhà" in tl or "complex" in tl:
        return "proj", "Căn hộ"
    if "biệt thự" in tl or "nhà phố" in tl:
        return "proj", "Nhà phố/Biệt thự"
    if any(k in tl for k in ("nghỉ dưỡng", "resort", "khách sạn", "du lịch", "khu nghỉ",
                             "golf", "gofl", "vui chơi", "giải trí")):
        return "proj", "Nghỉ dưỡng"
    if "khu đất" in tl or "lô đất" in tl or "khu đô thị" in tl:
        return "proj", "Khác"
    # cầu / cảng / nút giao (điểm hạ tầng)
    if "cầu" in tl or "hầm" in tl:
        return "infra", "Cầu/Hầm"
    if "cảng" in tl:
        return "infra", "Cảng"
    if "nút giao" in tl or "ngã tư" in tl or "ngã ba" in tl:
        return "infra", "Nút giao"
    # ĐƯỜNG BỘ — chỉ khi có từ khoá đường; bến xe cũng là hạ tầng giao thông
    if any(k in tl for k in ("đường", "quốc lộ", "cao tốc", "vành đai", "tuyến", "tỉnh lộ",
                             " ql", "đt.", "đt ", "bến xe")):
        return "infra", "Đường bộ"
    # còn lại KHÔNG có từ khoá hạ tầng → coi là dự án
    return "proj", "Khác"


_DUAN_LABEL = {"can_ho_chung_cu": "Căn hộ", "khu_do_thi": "Khu đô thị",
               "khu_cong_nghiep": "Khu công nghiệp", "dat_nen_du_an": "Đất nền",
               "nha_pho_biet_thu": "Nhà phố/Biệt thự", "bat_dong_san_nghi_duong": "Nghỉ dưỡng",
               "nha_o_xa_hoi": "Nhà ở xã hội", "loai_hinh_khac": "Khác"}


def fetch_cafeland_map(client):
    """3 lớp bản đồ cafeland — mỗi mục gắn `prov` (tỉnh) + `cat` (loại) để lọc:
    lines (tuyến) · points (điểm/sân bay) · projects (dự án BĐS + KCN gộp)."""
    db = client["dc_commodity"]
    pm = {d["pid"]: d["name"] for d in db["Cafeland_Provinces"].find({}, {"_id": 0, "pid": 1, "name": 1})}
    # danh sách tỉnh chính thức (chuẩn hoá) → chuẩn hoá + validate, bỏ rác (vd "496", trùng hoa/thường)
    canon = {}
    for nm in pm.values():
        d0 = _prov_norm(nm)
        if d0:
            canon[d0.lower()] = d0
    cp = lambda raw: canon.get(_prov_norm(raw).lower(), "")
    lines = [{"title": _titlecase(d.get("title", "")), "coords": d.get("coords", []),
              "color": d.get("line_color") or "#e67e22", "detail": _dec(d.get("detail")),
              "prov": cp(pm.get(d.get("getIdProvince"), "")), "cat": _infra_cat(d.get("title"))}
             for d in db["Cafeland_Lines"].find(
                 {"coords.1": {"$exists": True}},
                 {"_id": 0, "title": 1, "coords": 1, "line_color": 1, "detail": 1, "getIdProvince": 1})]
    for d in db["OSM_Infra"].find({}):                     # bổ sung tuyến cafeland thiếu (từ OSM)
        icon_shown = False
        for seg in d.get("segments", []):
            if len(seg) > 1:
                line = {"title": _titlecase(d.get("title", "")), "coords": seg, "color": "#0d9488",
                        "detail": {"Nguồn": "OpenStreetMap", "Loại": d.get("cat", ""),
                                   "Trạng thái": "đã khai thác"},
                        "prov": d.get("prov", ""), "cat": d.get("cat", "Đường bộ"),
                        "noicon": icon_shown}          # chỉ đặt 1 icon cho cả tuyến (nhiều đoạn OSM)
                icon_shown = True
                if d.get("tid"):                            # tuyến tracker → gắn thẳng pid (khỏi re-match)
                    line["pid"] = d["tid"]
                lines.append(line)
    points, projects = [], []
    for d in db["Cafeland_Points"].find(
            {"lat": {"$ne": None}},
            {"_id": 0, "title": 1, "lat": 1, "lng": 1, "detail": 1, "getIdProvince": 1}):
        prov = cp(pm.get(d.get("getIdProvince"), ""))
        kind, cat = _point_class(d.get("title"))
        if kind == "infra":
            points.append({"title": _titlecase(d.get("title", "")), "lat": d.get("lat"), "lng": d.get("lng"),
                           "detail": _dec(d.get("detail")), "prov": prov, "cat": cat})
        else:      # điểm là DỰ ÁN → đưa sang lớp dự án (kèm detail chi tiết)
            projects.append({"t": _titlecase((d.get("title") or ""))[:90], "a": d.get("lat"), "o": d.get("lng"),
                             "cat": cat, "prov": prov, "detail": _dec(d.get("detail"))})
    for d in db["Infra_RealEstate"].find(
            {"lat": {"$ne": None}},
            {"_id": 0, "title": 1, "lat": 1, "lng": 1, "type_name": 1, "status_name": 1,
             "price_min": 1, "price_m2": 1, "city": 1}):
        projects.append({"t": _titlecase((d.get("title") or ""))[:90], "a": d.get("lat"), "o": d.get("lng"),
                         "cat": _DUAN_LABEL.get(d.get("type_name"), "Khác"),
                         "prov": cp(d.get("city")), "s": d.get("status_name", ""),
                         "pm2": (d.get("price_m2") or "").strip(), "p": (d.get("price_min") or "").strip()})
    for d in db["Infra_IndustrialPark"].find(
            {"lat": {"$ne": None}},
            {"_id": 0, "title": 1, "lat": 1, "lng": 1, "acreage": 1, "status": 1, "address": 1}):
        projects.append({"t": _titlecase((d.get("title") or ""))[:90], "a": d.get("lat"), "o": d.get("lng"),
                         "cat": "Khu công nghiệp",
                         "prov": cp((d.get("address") or "").split(",")[-1]),
                         "s": d.get("status", ""), "ac": d.get("acreage", "")})
    # dedup dự án theo tên chuẩn hoá (hoa/thường + khoảng trắng) — ưu tiên bản có detail chi tiết
    uniq, extra = {}, []
    for p in projects:
        k = " ".join((p.get("t") or "").split()).lower()
        if not k:
            extra.append(p)
        elif k not in uniq or (p.get("detail") and not uniq[k].get("detail")):
            uniq[k] = p
    projects = list(uniq.values()) + extra
    return lines, points, projects


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
        if not tid or tid in curated:
            continue
        if tid not in nf:                     # dự án chưa có tin → vẫn hiện (đang theo dõi)
            loc = reg_loc.get(p["id"], "")
            auto.append({"id": tid, "g": categorize(p["name"], loc), "name": p["name"],
                         "status": "theo dõi", "owner": "", "loc": loc,
                         "phases": [], "marks": [], "items": [], "huyDong": 0, "capex": [],
                         "note": "Chưa có tin cập nhật — đang theo dõi"})
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
            "owner": "", "loc": loc,
            "phases": [{"kind": "build", "from": first, "to": end,
                        "state": "ongoing", "doneTo": last}],
            "marks": marks[-30:], "items": [], "huyDong": 0, "capex": [],
        })
    return auto


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "vn-infra-tracker.built.html"))
    args = ap.parse_args()

    c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=20000)
    c.admin.command("ping")
    col = c[DB][COLL]

    projects = list(col.find({"_key": "project"}, {"_id": 0, "_key": 0}).sort("id", 1))
    if not projects:
        sys.exit("Chưa có dự án trong DB — chạy nạp dữ liệu trước.")
    active = {d["tid"] for d in c[DB]["Infra_Projects_Registry"].find(
             {"active": True}, {"tid": 1}) if d.get("tid")}
    projects = [p for p in projects if p.get("id") in active]   # bỏ dự án đã tắt trong registry
    for p in projects:                    # default an toàn (entry AI có thể thiếu vài field) → tránh lỗi JS khi bung
        p.setdefault("capex", {}); p.setdefault("huyDong", 0)
        p.setdefault("items", []); p.setdefault("marks", []); p.setdefault("phases", [])
        p.setdefault("owner", ""); p.setdefault("loc", "")
        for it in p["items"]:
            it.setdefault("phases", []); it.setdefault("marks", [])
    tid2cat = category_map(c)
    for p in projects:                    # phân 18 dự án gốc vào 8 nhóm như các dự án khác (bỏ I-IV)
        p["g"] = tid2cat.get(p["id"]) or categorize(p.get("name", ""), p.get("loc", ""))
    print(f"Đọc từ DB: {len(projects)} dự án curated")

    projects = projects + build_auto_rows(c, projects)
    for p in projects:                    # vùng+tỉnh cho filter tab Tiến độ (giống Bản đồ)
        p["region"], p["prov"] = _geo_of(p.get("name", ""), p.get("loc") or p.get("location") or "")
        raw = OWNER_OVERRIDE.get(p.get("id")) or p.get("owner") or p.get("ownerAuto")   # tín hiệu tốt nhất
        p["owner"] = canon_owner(raw)                                              # → tập đoàn tư nhân / Nhà nước
        p["name"] = proper_case(p.get("name", ""))                                # Title-Case → proper text
    used = {p["g"] for p in projects}
    groups = [{"id": cid, "name": cname, "meta": cmeta, "huyDong": 0}
              for cid, cname, cmeta in CAT_META if cid in used]
    print(f"  {len(projects)} dòng · {len(groups)} nhóm: {', '.join(g['id'] for g in groups)}")

    tpl = open(TEMPLATE, encoding="utf-8").read()
    old_g = extract_array(tpl, "const GROUPS")
    old_p = extract_array(tpl, "const P =")
    new_g = json.dumps(groups, ensure_ascii=False)
    new_p = json.dumps(projects, ensure_ascii=False)

    newsflow = fetch_newsflow(c, projects, tid2cat)
    print(f"Newsflow: {len(newsflow)} tin")
    satellite = fetch_satellite(c)
    print(f"Vệ tinh: {len(satellite)} dự án có ảnh")
    caf_lines, caf_points, caf_projects = fetch_cafeland_map(c)
    print(f"Bản đồ (Cafeland): {len(caf_lines)} tuyến · {len(caf_points)} điểm · "
          f"{len(caf_projects)} dự án+KCN")

    # Khớp tên tuyến/điểm cafeland ↔ dự án đang theo dõi (tab Tiến độ) qua alias registry đã vetted
    # → gắn pid=tid để panel Bản đồ hiển thị thông tin + Dòng tin của dự án.
    from lib_projects import build_alias_regex, pid2tid
    _arx, _p2t = build_alias_regex(), pid2tid()
    _tracked = {p["id"] for p in projects}
    _rx_tracked = [(rx, _p2t[pid]) for pid, rx in _arx.items()
                   if pid in _p2t and _p2t[pid] in _tracked]

    def _pid_of(title):
        t = title or ""
        for rx, tid in _rx_tracked:
            if rx.search(t):
                return tid
        return None

    nmatch = 0
    for it in caf_lines + caf_points:
        if it.get("pid"):                 # đã gắn sẵn (tuyến OSM tracker) → giữ nguyên
            continue
        tid = _pid_of(it.get("title"))
        if tid:
            it["pid"] = tid
            nmatch += 1
    print(f"Khớp Bản đồ↔Tiến độ: {nmatch}/{len(caf_lines)+len(caf_points)} tuyến/điểm gắn dự án")

    # Dự án theo dõi KHÔNG có tuyến/điểm Cafeland → chấm điểm từ toạ độ geocode (Infra_Tracker_Geo)
    geo = {d["tid"]: d for d in c["dc_commodity"]["Infra_Tracker_Geo"].find(
        {"lat": {"$ne": None}}, {"_id": 0, "tid": 1, "lat": 1, "lng": 1})}
    pmeta = {p["id"]: p for p in projects}
    covered = {it["pid"] for it in caf_lines + caf_points if it.get("pid")}
    nadd = 0
    for tid in _tracked - covered:
        g, p = geo.get(tid), pmeta.get(tid)
        if not g or not p:
            continue
        caf_points.append({"title": p.get("name", f"Dự án {tid}"), "lat": g["lat"], "lng": g["lng"],
                           "detail": {}, "prov": p.get("prov", ""), "cat": p.get("g", "Khác"),
                           "pid": tid, "src": "tracker"})
        nadd += 1
    print(f"Chấm điểm dự án thiếu bản đồ (geocode): {nadd}")

    out = tpl.replace(old_g, new_g, 1).replace(old_p, new_p, 1)
    out = out.replace("const NEWSFLOW = []", "const NEWSFLOW = " + json.dumps(newsflow, ensure_ascii=False), 1)
    out = out.replace("const SATELLITE = {}", "const SATELLITE = " + json.dumps(satellite, ensure_ascii=False), 1)
    out = out.replace("const CAT_ORDER = []", "const CAT_ORDER = " + json.dumps([c[0] for c in CAT_META], ensure_ascii=False), 1)
    out = out.replace("const CAF_LINES = []", "const CAF_LINES = " + json.dumps(caf_lines, ensure_ascii=False), 1)
    out = out.replace("const CAF_POINTS = []", "const CAF_POINTS = " + json.dumps(caf_points, ensure_ascii=False), 1)
    out = out.replace("const CAF_PROJECTS = []", "const CAF_PROJECTS = " + json.dumps(caf_projects, ensure_ascii=False), 1)
    out = out.replace("const PROV_REGION = {}", "const PROV_REGION = " + json.dumps(PROV_REGION, ensure_ascii=False), 1)
    # dấu vết build để biết trang đang chạy bằng dữ liệu DB
    out = out.replace("</title>", "</title>\n<!-- built from dc_commodity.Infra_Project_Tracker -->")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"Đã dựng: {args.out}")


if __name__ == "__main__":
    main()
