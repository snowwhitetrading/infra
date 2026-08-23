# -*- coding: utf-8 -*-
"""
step2_radar.py — RADAR dự án mới (deterministic, KHÔNG cần Claude).

Ý tưởng: đọc POOL tin CHƯA khớp dự án (dc_news.unmatched_raw, do step1 để lại khi scrape
16 báo) → trích "LOẠI + Tên Riêng" bằng regex → cái nào ngoài registry = ỨNG VIÊN dự án mới.
Gộp trùng, xếp theo độ nóng (số lần nhắc × số báo × tín hiệu 'tỷ USD/nghìn tỷ').
Ghi dc_commodity.Infra_Project_Candidates.

KHÔNG tự quét feed nữa — dùng chung dòng tin với step1 (chạy SAU step1 trong CI).
Deterministic, không LLM. Bạn liếc bảng candidates, thấy dự án lớn thật thì add_project.py.

  python step2_radar.py            # quét + ghi + in top
  python step2_radar.py --top 40
  python step2_radar.py --dry-run
"""
import argparse
import datetime as dt
import re
import unicodedata
from collections import defaultdict

from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri
from lib_projects import build_alias_regex

OUT_DB, OUT_COLL = "dc_commodity", "Infra_Project_Candidates"
POOL_DB, POOL_COLL = "dc_news", "unmatched_raw"   # do step1 để lại (tin chưa khớp dự án)

# loại công trình + TÊN (tên phải là danh từ riêng: nối địa danh "A - B" hoặc "Tên + số/riêng")
# Hạ tầng giao thông + công trình công cộng/thương mại (KHÔNG gồm BĐS nhà ở & công nghiệp)
TYPE = (r"(cao tốc|đường sắt tốc độ cao|đường sắt|tàu điện|đường vành đai|vành đai|sân bay|"
        r"cảng hàng không|cảng biển|cảng|metro|tuyến metro|hầm đường bộ|"
        r"đại lộ|trục cảnh quan|đường ven sông|đường ven biển|đường trên cao|nút giao|"
        r"trung tâm hành chính|khu hành chính|trung tâm chính trị|"
        r"trung tâm hội nghị|sân vận động|nhà thi đấu|khu liên hợp thể thao|"
        r"nhà hát|cung văn hoá|cung văn hóa|quảng trường|trung tâm triển lãm)")   # bỏ 'cầu' (dính nhu/yêu cầu); bỏ 'công viên/bảo tàng' (tin khai quật/sự kiện gây nhiễu)
# tên hợp lệ: có nối địa danh (Châu Đốc - Cần Thơ) HOẶC danh từ riêng + số/riêng (Đông Hải 1)
NAME = (r"([A-ZĐÀ-Ỹ][\wÀ-ỹ]+(?:\s+[A-ZĐÀ-Ỹ0-9][\wÀ-ỹ]*)*"
        r"(?:\s*[-–]\s*[A-ZĐÀ-Ỹ][\wÀ-ỹ]+(?:\s+[\wÀ-ỹ]+)*)*)")
RX = re.compile(TYPE + r"\s+" + NAME, re.IGNORECASE)
# tên phải có ≥1 dấu hiệu "riêng": nối địa danh, hoặc có số, hoặc ≥2 từ viết hoa
def is_proper(name):
    if re.search(r"[-–]", name):                       # nối địa danh
        return True
    if re.search(r"\d", name):                         # có số (giai đoạn/tên)
        return True
    caps = re.findall(r"\b[A-ZĐÀ-Ỹ][\wÀ-ỹ]+", name)     # nhiều từ viết hoa
    return len(caps) >= 2

STOP = re.compile(r"\b(trọng điểm|quốc gia|này|đó|các|những|một số|nhiều|khác|đến năm|"
                  r"thời kỳ|giai đoạn|trên tuyến|vùng|khu vực|lớn|mới)\b", re.IGNORECASE)
IMPORTANT = re.compile(r"tỷ\s*USD|nghìn tỷ|\d[\d.,]*\s*tỷ|trọng điểm|quốc gia", re.IGNORECASE)
# ngữ cảnh DỰ ÁN THẬT (không phải nhắc thoáng) → điều kiện để TỰ ADD
EVENT = re.compile(r"khởi công|động thổ|phê duyệt|chủ trương đầu tư|trúng thầu|thông xe|"
                   r"khánh thành|thi công|đầu tư xây dựng|dự án", re.IGNORECASE)
# bộ lọc auto-add (cân bằng): đủ nóng + có ngữ cảnh dự án thật
AUTO_MIN_SOURCES, AUTO_MIN_COUNT = 2, 3


def norm(s):
    return re.sub(r"\s+", " ", s).strip(" -–.\"'").lower()


def run(dry, top, auto_add=False):
    known = build_alias_regex()
    cand = defaultdict(lambda: {"label": "", "name": "", "count": 0, "sources": set(),
                                "important": False, "event": False, "samples": []})
    client = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    pool = client[POOL_DB][POOL_COLL]
    n = 0
    for it in pool.find({}, {"title": 1, "description": 1, "source": 1}):
        n += 1
        title = (it.get("title") or "").strip()
        desc = re.sub("<[^>]+>", "", it.get("description") or "")
        src = it.get("source", "?")
        text = title + ". " + desc
        for m in RX.finditer(text):
            label = f"{m.group(1).strip()} {m.group(2).strip()}"
            name = m.group(2).strip()
            if not is_proper(name) or STOP.search(name):
                continue
            if any(rx.search(label) for rx in known.values()):   # đã theo dõi
                continue
            c = cand[norm(label)]
            c["label"] = c["label"] or label
            c["name"] = c["name"] or name
            c["count"] += 1
            c["sources"].add(src)
            if IMPORTANT.search(text):
                c["important"] = True
            if EVENT.search(text):
                c["event"] = True
            if len(c["samples"]) < 2:
                c["samples"].append(title[:80])
    print(f"Đọc {n} tin chưa khớp từ {POOL_DB}.{POOL_COLL}")

    rows = []
    for key, c in cand.items():
        if c["count"] < 2 and len(c["sources"]) < 2:      # bỏ tin nhắc 1 lần (nhiễu one-off)
            continue
        score = c["count"] + 3 * len(c["sources"]) + (4 if c["important"] else 0)
        rows.append({"key": key, "label": c["label"], "name": c["name"], "count": c["count"],
                     "sources": sorted(c["sources"]), "important": c["important"],
                     "event": c["event"], "score": score, "samples": c["samples"]})
    rows.sort(key=lambda r: r["score"], reverse=True)

    print(f"Ứng viên dự án mới (nhắc ≥2 lần, ngoài registry): {len(rows)}")
    for r in rows[:top]:
        print(f"  {'★' if r['important'] else ' '} score {r['score']:3} ×{r['count']:2}  {r['label'][:58]}")

    if dry:
        if auto_add:
            auto_add_projects(client, rows, dry=True)
        return
    if rows:
        coll = client[OUT_DB][OUT_COLL]
        coll.create_index("key", unique=True)
        now = dt.datetime.now().isoformat()
        coll.bulk_write([UpdateOne({"key": r["key"]}, {"$set": {**r, "updated": now}}, upsert=True) for r in rows])
        print(f"Đã ghi {len(rows)} ứng viên vào {OUT_DB}.{OUT_COLL}.")
    if auto_add:
        auto_add_projects(client, rows)


def _ascii(s):
    return unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()


def _slug(s, used):
    b = re.sub(r"[^a-z0-9]+", "_", _ascii(s)).strip("_")[:40] or "radar"
    s2, i = b, 2
    while s2 in used:
        s2, i = f"{b}_{i}", i + 1
    used.add(s2)
    return s2


# từ định lượng/loại-hình → KHÔNG tính là token tên riêng
_NOISE = {"hon", "ty", "usd", "km", "so", "nghin", "trieu", "ti", "gan", "khoang", "toi",
          "cao", "toc", "duong", "sat", "san", "bay", "cang", "cau", "ham", "metro", "tuyen",
          "vanh", "dai", "quoc", "lo", "hang", "khong", "do", "thi", "nut", "giao", "tinh",
          "du", "an", "va", "cho", "ket", "noi", "moi", "giai", "doan"}


def _name_toks(s):
    """Token tên riêng: chữ, ≥3 ký tự, không phải từ loại-hình/định-lượng."""
    return set(t for t in _ascii(s).replace("-", " ").split() if len(t) >= 3 and t not in _NOISE and not t.isdigit())


_VN = re.compile(r"[àáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộ"
                 r"ơớờởỡợùúủũụưứừửữựỳýỷỹỵ]", re.IGNORECASE)


def _place_ok(name):
    """Tên đủ 'riêng' VN để add: (cặp địa danh A - B HOẶC ≥2 token tên riêng)
    VÀ có ≥1 từ tên mang dấu tiếng Việt (loại tên nước ngoài như 'Western Sydney')."""
    words = [w for w in name.replace("-", " ").split()
             if len(_ascii(w)) >= 3 and _ascii(w) not in _NOISE and not _ascii(w).isdigit()]
    pair = re.search(r"[a-zà-ỹ]{3,}\s*[-–]\s*[a-zà-ỹ]{3,}", name.lower())
    if not (pair or len(words) >= 2):
        return False
    return any(_VN.search(w) for w in words)


def auto_add_projects(client, rows, dry=False):
    """TỰ ADD ứng viên đạt lọc vào Infra_Projects_Registry (bỏ bước duyệt tay).
    Lọc: đủ nóng (≥N báo & ≥M lần) + ngữ cảnh dự án thật + tên đủ riêng + KHÔNG trùng
    (fuzzy: ≥60% token tên nằm trong 1 dự án đã có)."""
    from step5_build_site import _infra_cat
    reg = client["dc_commodity"]["Infra_Projects_Registry"]
    existing = list(reg.find({}, {"id": 1, "tid": 1, "name": 1, "aliases": 1}))
    known_sets = []                       # (loại hình, token) của từng dự án đã có
    for d in existing:
        cat = _infra_cat(d.get("name", ""))
        for s in [d.get("name")] + (d.get("aliases") or []):
            ts = _name_toks(s)
            if ts:
                known_sets.append((cat, ts))
    used_ids = {d.get("id") for d in existing}
    tid = max((d.get("tid", 0) or 0 for d in existing), default=0)

    def is_dup(nt, cat):                                   # trùng CÙNG LOẠI HÌNH, 2 chiều (name ⊂ cũ / bao trùm cũ)
        return any(kc == cat and ks and nt and
                   (len(nt & ks) / len(nt) >= 0.6 or len(nt & ks) / len(ks) >= 0.75)
                   for kc, ks in known_sets)

    added = 0
    for r in rows:
        name_part = r.get("name", "")
        nt = _name_toks(name_part)
        if not (len(r["sources"]) >= AUTO_MIN_SOURCES and r["count"] >= AUTO_MIN_COUNT):
            continue
        if not (r["event"] or r["important"]):
            continue
        if not _place_ok(name_part) or len(nt) < 2:       # loại tên vụn (số/định lượng)
            continue
        if is_dup(nt, _infra_cat(name_part)):              # đã có (kể cả tên biến thể, cùng loại)
            continue
        tid += 1
        name = re.sub(r"\s+", " ", r["label"]).strip().title()
        pid = _slug(r["label"], used_ids)
        if not dry:
            reg.update_one({"id": pid},
                           {"$set": {"id": pid, "tid": tid, "name": name,
                                     "aliases": [name, r["label"]], "active": True,
                                     "origin": "radar", "group": _infra_cat(r["label"])}},
                           upsert=True)
        known_sets.append((_infra_cat(name_part), nt))
        added += 1
        print(f"  {'[dry] ' if dry else ''}+ TỰ ADD tid{tid} [{_infra_cat(r['label'])}] {name[:48]}")
    print(f"Auto-add: thêm {added} dự án (lọc ≥{AUTO_MIN_SOURCES} báo & ≥{AUTO_MIN_COUNT} lần + ngữ cảnh + tên riêng + không trùng).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--auto-add", action="store_true",
                    help="Tự thêm ứng viên đạt lọc vào registry (bỏ duyệt tay)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(a.dry_run, a.top, a.auto_add)
