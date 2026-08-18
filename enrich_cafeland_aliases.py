# -*- coding: utf-8 -*-
"""
enrich_cafeland_aliases.py — Sinh thêm ALIAS ngắn/tự nhiên cho dự án gộp từ cafeland
(mỗi cái vốn chỉ có 1 alias = tên gốc dài/lạ nên bộ khớp tin không bắt được), và
tắt (active=False) các BẢN TRÙNG (tên chỉ khác nhau ở từ bao ngoài / tên tỉnh).

An toàn: KHÔNG tách lõi chung chung ("Vành Đai 2", "Quốc Lộ 22" trần → khớp nhầm);
chỉ BỎ TỪ BAO ngoài để lộ cụm đặc trưng, và thêm cặp điểm đầu-cuối "A - B".

  python enrich_cafeland_aliases.py --dry-run
  python enrich_cafeland_aliases.py
"""
import argparse
import re
import unicodedata

from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

DB = "dc_commodity"

# từ BAO ngoài (bỏ đi để lộ tên đặc trưng) — dùng cho cả enrich lẫn dedup
_PREFIX = re.compile(
    r"^(dự án đầu tư xây dựng|dự án mở rộng|dự án cải tạo|dự án nâng cấp|dự án|"
    r"đầu tư xây dựng|xây dựng|mở rộng|nâng cấp|cải tạo|nạo vét|"
    r"tuyến đường sắt|tuyến metro|tuyến đường|tuyến|công trình|đường dẫn và|datp\d+|"
    r"điểm đầu|điểm cuối|hạng mục)\s+", re.I)
_SUFFIX = re.compile(
    r"\s+(giai đoạn\s*\d+|gđ\s*\d+|đợt\s*\d+|qua\s+[\wÀ-ỹ ]+|"
    r"và đường dẫn.*|,?\s*đường dẫn.*)$", re.I)
_PAREN = re.compile(r"\s*\([^)]*\)\s*")
_CITY_TAIL = re.compile(r"\s+(tp\.?hcm|tphcm|tp\.? hồ chí minh|hà nội|hải phòng)$", re.I)

# token coi là "bao ngoài / địa danh phụ" khi so trùng
_WRAP_TOK = {"du", "an", "mo", "rong", "cai", "tao", "nang", "cap", "xay", "dung",
             "dau", "tu", "giai", "doan", "gd", "dot", "qua", "tuyen", "cong", "trinh",
             "duong", "dan", "va", "datp1", "datp2", "diem", "cuoi", "nao", "vet",
             "tphcm", "tp", "hcm", "ho", "chi", "minh", "ha", "noi", "hai", "phong",
             "dong", "nai", "thanh", "pho", "hang", "muc"}
# tên chung chung — KHÔNG cho làm bản "giữ" khi dedup (tránh nuốt tuyến cụ thể)
_GENERIC = re.compile(r"^(vành đai|đường vành đai|quốc lộ|tỉnh lộ|đường tỉnh|metro số|"
                      r"tuyến số|cao tốc|đường sắt)\s*\d*[a-z]?$", re.I)


def _norm(s):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ",
                    unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()).split())


def _strip_wrap(name, drop_city=True):
    """Bỏ tiền tố/hậu tố bao ngoài → cụm đặc trưng. drop_city=False khi so trùng
    (giữ tên tỉnh để VĐ2 TPHCM ≠ VĐ2 Hà Nội)."""
    s = _PAREN.sub(" ", name).strip()
    prev = None
    while prev != s:                       # bỏ nhiều lớp tiền tố
        prev = s
        s = _PREFIX.sub("", s).strip()
    s = _SUFFIX.sub("", s).strip()
    if drop_city:
        s = _CITY_TAIL.sub("", s)
    return s.strip(" ,-")


def derive(name):
    out = set()
    core = _strip_wrap(name)
    if core and _norm(core) != _norm(name) and len(core) >= 5:
        out.add(core)
    # cặp điểm đầu-cuối "A - B" (đủ đặc trưng, cả 2 vế ≥ 3 ký tự chữ)
    seg = re.search(r"([\wÀ-ỹ][\wÀ-ỹ ]{2,}?)\s+-\s+([\wÀ-ỹ][\wÀ-ỹ ]{2,})", name)
    if seg:
        pair = f"{seg.group(1).strip()} - {seg.group(2).strip()}"
        pair = _CITY_TAIL.sub("", pair).strip()
        if 8 <= len(pair) <= 50 and not _GENERIC.match(pair):
            out.add(pair)
    # dọn
    clean, seen = [], set()
    for a in out:
        a = re.sub(r"\s+", " ", a).strip(" ,-")
        n = _norm(a)
        if not n or n == _norm(name) or n in seen or len(n) < 5 or _GENERIC.match(a):
            continue
        seen.add(n)
        clean.append(a)
    return clean


def main(dry):
    reg = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB]["Infra_Projects_Registry"]
    projects = list(reg.find({"active": True}, {"tid": 1, "name": 1, "aliases": 1, "origin": 1}))

    # ── 1) enrich alias cho dự án cafeland ──
    ops, sample = [], []
    caf = [p for p in projects if p.get("origin") == "cafeland"]
    for p in caf:
        cur = set(p.get("aliases") or [])
        new = [a for a in derive(p["name"]) if a not in cur]
        if new:
            ops.append(UpdateOne({"_id": p["_id"]}, {"$addToSet": {"aliases": {"$each": new}}}))
            if len(sample) < 24:
                sample.append((p["name"], new))
    print(f"Enrich alias: {len(ops)}/{len(caf)} dự án cafeland thêm alias")
    for nm, new in sample:
        print(f"  {nm[:44]:44} + {new}")

    # ── 2) dedup: cùng LÕI (bỏ bao ngoài, GIỮ tên tỉnh) → gộp; giữ bản curated/gọn ──
    from collections import defaultdict
    groups = defaultdict(list)
    for p in projects:
        core = _norm(_strip_wrap(p["name"], drop_city=False))
        if len(core) >= 8 and not _GENERIC.match(_strip_wrap(p["name"], drop_city=False)):
            groups[core].append(p)
    dedup = []
    for core, ps in groups.items():
        if len(ps) < 2:
            continue
        # người GIỮ: ưu tiên curated (origin != cafeland), rồi tên ngắn nhất
        keep = min(ps, key=lambda p: (p.get("origin") == "cafeland", len(p["name"])))
        for p in ps:
            if p is not keep:
                dedup.append((p, keep))
    print(f"\nBản TRÙNG (tắt tên dài, giữ bản gọn/curated): {len(dedup)}")
    for drop, keep in dedup:
        print(f"  tắt tid{drop['tid']:>3} '{drop['name'][:46]:46}'  → giữ tid{keep['tid']} '{keep['name'][:34]}'")

    if dry:
        return
    if ops:
        reg.bulk_write(ops)
    if dedup:
        reg.bulk_write([UpdateOne({"_id": d["_id"]}, {"$set": {"active": False, "dup_of": k["tid"]}})
                        for d, k in dedup])
    print(f"\nĐã cập nhật: +alias {len(ops)} dự án · tắt {len(dedup)} bản trùng.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    main(ap.parse_args().dry_run)
