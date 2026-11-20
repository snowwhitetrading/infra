# -*- coding: utf-8 -*-
"""
apply_disb.py — Ghi kết quả Claude trích từ Phụ lục III (disb_verdicts.json) vào CSV giải ngân
rồi nạp Mongo. KHỚP mỗi dòng với khoa_du_an CŨ (theo tên chuẩn hoá) để chuỗi series nối đúng kỳ.

  python apply_disb.py --verdicts disb_verdicts.json --manifest _disb_pages/manifest.json \
      [--vanban "908/BC-BTC"] [--csv giai_ngan_ha_tang_2025_2026.csv] [--dry-run]
"""
import argparse
import csv
import json
import os
import re
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_DEFAULT = "giai_ngan_ha_tang_2025_2026.csv"
COLS = ["as_of", "ky", "nam_ke_hoach", "van_ban_nguon", "file_nguon", "phu_luc", "don_vi",
        "khoi", "ma", "cap", "ma_cha", "khoa_du_an", "ten_du_an", "co_quan_chu_quan",
        "kh_tong", "kh_nstw", "kh_nsdp", "gn_tong", "gn_ty_le", "gn_nstw", "gn_nstw_ty_le",
        "gn_nsdp", "gn_nsdp_ty_le", "ghi_chu"]


def _n(s):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", unicodedata.normalize("NFD", (s or "").lower())
                    .encode("ascii", "ignore").decode()).split())


def _slug(s):
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", _n(s))).strip("_")


def _f(v):
    try:
        return round(float(v), 4) if v is not None and str(v).strip() != "" else ""
    except (ValueError, TypeError):
        return ""


def run(verdicts, manifest, vanban, csv_path, dry):
    vpath = verdicts if os.path.isabs(verdicts) else os.path.join(HERE, verdicts)
    V = json.load(open(vpath, encoding="utf-8"))
    M = json.load(open(manifest if os.path.isabs(manifest) else os.path.join(HERE, manifest), encoding="utf-8"))
    cpath = csv_path if os.path.isabs(csv_path) else os.path.join(HERE, csv_path)
    existing = list(csv.DictReader(open(cpath, encoding="utf-8-sig")))

    # map tên-chuẩn-hoá -> (khoa_du_an, cap) từ dữ liệu cũ (ưu tiên group/item — nhóm được ingest dùng)
    name2khoa = {}
    have_ky = set()                                  # (ky, khoa) đã có → chống trùng
    for r in existing:
        k, cap, ten = r.get("khoa_du_an", ""), r.get("cap", ""), r.get("ten_du_an", "")
        if k:
            have_ky.add((r.get("ky", ""), k))
            if cap in ("group", "item") and _n(ten):
                name2khoa.setdefault(_n(ten), (k, cap))

    ky = M.get("ky", ""); nam = M.get("nam", "") or ""
    as_of = (V.get("as_of") or "").strip()
    new_rows, matched, newproj, skipped = [], 0, [], 0
    for row in V.get("rows", []):
        ten = (row.get("ten") or "").strip()
        if not ten:
            continue
        nn = _n(ten)
        if nn in name2khoa:
            khoa, cap = name2khoa[nn]; matched += 1
        else:                                        # dự án CHƯA có trong CSV → slug mới, cap group (để xem lại)
            khoa, cap = _slug(ten), "group"; newproj.append(ten)
        if (ky, khoa) in have_ky:                     # đã có kỳ này cho dự án này → bỏ
            skipped += 1; continue
        new_rows.append({
            "as_of": as_of, "ky": ky, "nam_ke_hoach": nam,
            "van_ban_nguon": vanban, "file_nguon": M.get("file_nguon", ""),
            "phu_luc": "Phu luc III", "don_vi": "ty dong", "khoi": "",
            "ma": (row.get("ma") or "").strip(), "cap": cap, "ma_cha": "",
            "khoa_du_an": khoa, "ten_du_an": ten, "co_quan_chu_quan": "",
            "kh_tong": _f(row.get("kh_tong")), "kh_nstw": _f(row.get("kh_nstw")), "kh_nsdp": _f(row.get("kh_nsdp")),
            "gn_tong": _f(row.get("gn_tong")), "gn_ty_le": _f(row.get("gn_ty_le")),
            "gn_nstw": _f(row.get("gn_nstw")), "gn_nstw_ty_le": _f(row.get("gn_nstw_ty_le")),
            "gn_nsdp": _f(row.get("gn_nsdp")), "gn_nsdp_ty_le": _f(row.get("gn_nsdp_ty_le")),
            "ghi_chu": "",
        })
        have_ky.add((ky, khoa))

    print(f"Kỳ {ky} (as_of {as_of}) · trích {len(V.get('rows', []))} dòng → thêm {len(new_rows)} "
          f"(khớp dự án cũ: {matched}, dự án mới: {len(newproj)}, bỏ trùng: {skipped})")
    if newproj:
        print("  Dự án MỚI (chưa có trong CSV, nên kiểm tra):", "; ".join(newproj[:8]) + ("…" if len(newproj) > 8 else ""))
    if dry:
        print("  [dry-run] KHÔNG ghi CSV/Mongo. Ví dụ 3 dòng:")
        for r in new_rows[:3]:
            print("   ", r["ma"], r["khoa_du_an"], "kh=", r["kh_tong"], "gn=", r["gn_tong"], "tyle=", r["gn_ty_le"])
        return
    if not new_rows:
        print("  Không có dòng mới để thêm."); return
    with open(cpath, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        for r in new_rows:
            w.writerow(r)
    print(f"  Đã thêm {len(new_rows)} dòng vào {os.path.basename(cpath)} → nạp Mongo…")
    import ingest_disbursement
    ingest_disbursement.run(cpath, dry=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verdicts", default="disb_verdicts.json")
    ap.add_argument("--manifest", default="_disb_pages/manifest.json")
    ap.add_argument("--vanban", default="")
    ap.add_argument("--csv", default=CSV_DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(a.verdicts, a.manifest, a.vanban, a.csv, a.dry_run)
