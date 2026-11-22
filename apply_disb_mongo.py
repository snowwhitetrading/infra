# -*- coding: utf-8 -*-
"""
apply_disb_mongo.py — Ghi 1 KỲ giải ngân Claude vừa trích (disb_verdicts.json) THẲNG vào Mongo
Infra_Disbursement (append vào series), KHÔNG qua CSV → chạy được trên GitHub Actions.

Khớp dự án → tid giống ingest_disbursement (DISB_MAP theo slug + fuzzy token tên so với tracker).
Thêm/cập nhật điểm series cho kỳ --ky; cập nhật kh/gn/pct/as_of = kỳ MỚI nhất.

  python apply_disb_mongo.py --verdicts disb_verdicts.json --ky 8T2026 [--dry-run]
"""
import argparse
import json
import os
import re
import unicodedata

from pymongo import MongoClient

from lib_db import mongo_uri
from ingest_disbursement import DISB_MAP

HERE = os.path.dirname(os.path.abspath(__file__))
DB = "dc_commodity"


def _n(s):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", unicodedata.normalize("NFD", (s or "").lower().replace("đ", "d"))
                    .encode("ascii", "ignore").decode()).split())


def _slug(s):
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", _n(s))).strip("_")


def _ky_key(ky):
    m = re.match(r"(\d+)T(\d{4})", ky or "")
    return (int(m.group(2)), int(m.group(1))) if m else (0, 0)


def _f(x):
    try:
        return round(float(x), 4) if x is not None and str(x).strip() != "" else None
    except (ValueError, TypeError):
        return None


def run(verdicts, ky, dry, expect_ky="", src=None):
    V = json.load(open(verdicts if os.path.isabs(verdicts) else os.path.join(HERE, verdicts), encoding="utf-8"))
    rows = V.get("rows", [])
    if not ky:                                        # KỲ = tháng của as_of (quy ước CSV: as_of 2026-07-31 → 7T2026)
        aso = (V.get("as_of") or "").strip()
        m = re.match(r"(\d{4})-(\d{2})", aso)
        if not m:
            raise SystemExit(f"Không suy được kỳ từ as_of='{aso}' — truyền --ky thủ công.")
        ky = f"{int(m.group(2))}T{m.group(1)}"
    # CHỐT AN TOÀN: kỳ đọc được trên bảng (as_of) phải khớp kỳ ghi trên TIÊU ĐỀ báo cáo đã tải.
    # Lệch → gần như chắc chắn đọc nhầm loại báo cáo/phụ lục (vd báo cáo khoá sổ "13 tháng năm trước"
    # từng bị nạp thành 1T2026) → DỪNG, không ghi gì.
    if expect_ky and _ky_key(ky) != _ky_key(expect_ky):
        raise SystemExit(f"DỪNG: bảng ghi số liệu kỳ {ky} (as_of={V.get('as_of')}) nhưng báo cáo tải về là kỳ "
                         f"{expect_ky} — không ghi Mongo. Kiểm tra lại trang phụ lục đã đọc.")
    db = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB]
    tr_norm = [(p["id"], set(_n(p["name"]).split()))
               for p in db["Infra_Project_Tracker"].find({"_key": "project"}, {"id": 1, "name": 1})]

    def match_tid(ten):
        slug = _slug(ten)
        if slug in DISB_MAP:
            return DISB_MAP[slug]
        for k, tid in DISB_MAP.items():               # tên ngắn (metro) là phần đuôi của khoa dài
            if slug and (slug in k or k.endswith("_" + slug) or k.endswith(slug)):
                return tid
        toks = set(_n(ten).split())
        best, bs = None, 0.0
        for tid, ttok in tr_norm:
            if not ttok:
                continue
            ov = len(toks & ttok) / max(1, len(ttok))
            if ov > bs:
                bs, best = ov, tid
        return best if bs >= 0.6 else None

    valid = {tid for tid, _ in tr_norm}
    # GỘP theo tid: 1 dự án theo dõi có thể nằm trên NHIỀU dòng của bảng (các đoạn qua từng tỉnh, các
    # dự án thành phần) → cộng KH + giải ngân, tính lại tỷ lệ. Trước đây dòng sau đè dòng trước.
    agg, unmatched, n_skip = {}, [], 0
    for r in rows:
        ten = (r.get("ten") or "").strip()
        pct, gn, kh = _f(r.get("gn_ty_le")), _f(r.get("gn_tong")), _f(r.get("kh_tong"))
        if not ten or pct is None:
            continue
        if "tid" in r:                                # agent ĐÃ gán mã dự án (đọc-hiểu, có danh sách tracker)
            tid = r.get("tid")
            if tid is None:                           # agent kết luận: không thuộc dự án đang theo dõi
                n_skip += 1; continue
            if tid not in valid:
                unmatched.append(f"{ten} (tid lạ: {tid})"); continue
        else:                                         # bản trích cũ không có tid → khớp theo tên
            tid = match_tid(ten)
            if not tid:
                unmatched.append(ten); continue
        a = agg.setdefault(tid, {"ten": ten, "kh": 0.0, "gn": 0.0, "pct": pct, "n": 0, "kh_ok": True})
        a["n"] += 1
        a["gn"] += gn or 0.0
        if kh is None:
            a["kh_ok"] = False
        else:
            a["kh"] += kh

    n_up, n_new = 0, 0
    for tid, a in agg.items():
        kh = round(a["kh"], 1) if a["kh_ok"] else None
        gn = round(a["gn"], 1)
        pct = a["pct"] if a["n"] == 1 else (round(gn / kh * 100, 1) if kh else None)
        if pct is None:
            unmatched.append(f"{a['ten']} (gộp {a['n']} dòng nhưng thiếu kế hoạch vốn)"); continue
        doc = db["Infra_Disbursement"].find_one({"tid": tid}) or {"tid": tid, "name": a["ten"], "series": []}
        series = [s for s in doc.get("series", []) if s.get("ky") != ky]      # bỏ kỳ trùng (refresh)
        series.append({"ky": ky, "pct": pct, "gn": gn, "kh": kh})
        series.sort(key=lambda s: _ky_key(s.get("ky", "")))
        last = series[-1]
        new_doc = {"tid": tid, "name": doc.get("name") or a["ten"],
                   "khoa": doc.get("khoa", _slug(a["ten"])), "as_of": last["ky"],
                   "kh": kh if last["ky"] == ky else doc.get("kh"),
                   "gn": last.get("gn"), "pct": last.get("pct"), "series": series}
        if not dry:
            db["Infra_Disbursement"].update_one({"tid": tid}, {"$set": new_doc}, upsert=True)
        if doc.get("_id"):
            n_up += 1
        else:
            n_new += 1

    if not dry and src and (n_up or n_new):          # nhật ký báo cáo đã nạp (nguồn + link) → tab hiện "theo báo cáo nào"
        import datetime as dt
        db["Infra_Disbursement_Reports"].update_one({"ky": ky}, {"$set": {
            "ky": ky, "as_of": V.get("as_of"), "title": src.get("title", ""), "url": src.get("url", ""),
            "source": src.get("source", ""), "rows": n_up + n_new,
            # dòng TỔNG SỐ của phụ lục (cả danh mục trong báo cáo) → mốc so sánh "bình quân" ở tab Giải ngân
            "total_kh": _f((V.get("tong") or {}).get("kh_tong")), "total_gn": _f((V.get("tong") or {}).get("gn_tong")),
            "total_pct": _f((V.get("tong") or {}).get("gn_ty_le")),
            "at": dt.datetime.now(dt.timezone.utc).isoformat()}}, upsert=True)
    print(f"Giải ngân kỳ {ky}: cập nhật {n_up} dự án · thêm mới {n_new} · ngoài danh mục theo dõi {n_skip} · chưa khớp {len(unmatched)}"
          + (" [dry-run]" if dry else ""))
    for nm in unmatched[:15]:
        print("   ? chưa khớp:", nm[:55])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verdicts", default="disb_verdicts.json")
    ap.add_argument("--ky", default="", help="kỳ NT YYYY (mặc định: suy từ as_of, vd as_of 2026-07-31 → 7T2026)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--expect-ky", default="", help="kỳ theo TIÊU ĐỀ báo cáo (fetch_disb_report) — lệch với as_of thì dừng")
    ap.add_argument("--source-title", default="")
    ap.add_argument("--source-url", default="")
    ap.add_argument("--source", default="")
    a = ap.parse_args()
    run(a.verdicts, a.ky, a.dry_run, a.expect_ky,
        {"title": a.source_title, "url": a.source_url, "source": a.source} if (a.source_title or a.source_url) else None)
