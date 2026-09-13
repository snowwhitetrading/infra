# -*- coding: utf-8 -*-
"""
ingest_disbursement.py — Nạp TIẾN ĐỘ GIẢI NGÂN (Bộ Tài chính, Phụ lục III/IV) từ CSV → Mongo.
Nguồn: giai_ngan_ha_tang_2025_2026.csv (cột: as_of, ky, khoa_du_an, ten_du_an, cap, kh_tong, gn_tong, gn_ty_le...).

Với mỗi dự án (khoa_du_an, cấp group hoặc tuyến metro/đường sắt item): gom chuỗi giải ngân theo kỳ →
khớp sang tracker (DISB_MAP tay + fuzzy tên) → ghi dc_commodity.Infra_Disbursement:
  {tid, name, as_of, kh (kế hoạch năm), gn (đã giải ngân), pct (%), series:[{ky, pct, gn, kh}]}.

  python ingest_disbursement.py [--csv giai_ngan_ha_tang_2025_2026.csv] [--dry-run]
"""
import argparse
import csv
import os
import re
import unicodedata
from collections import defaultdict

from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

HERE = os.path.dirname(os.path.abspath(__file__))
DB = "dc_commodity"

# khoa_du_an (BTC) -> tid tracker (map tay cho ca chac chan). Con lai fuzzy theo ten.
DISB_MAP = {
    "duong_sat_toc_do_cao_tren_truc_bac_nam": 20,
    "duong_sat_lao_cai_ha_noi_hai_phong": 21,
    "vanh_dai_4_vung_thu_do_ha_noi": 56,
    "vanh_dai_3_tp_ho_chi_minh": 55,
    "khanh_hoa_buon_ma_thuot_gd1": 163,
    "bien_hoa_vung_tau_gd1": 24,
    "chau_doc_can_tho_soc_trang_gd1": 25,
    "gia_nghia_chon_thanh_ppp": 60,
    "cao_lanh_an_huu": 147,
    "dong_dang_tra_linh_giai_1": 62,
    "huu_nghi_chi_lang": 63,
    "dau_giay_tan_phu": 65,
    "tan_phu_bao_loc": 64,
    "bao_loc_lien_khuong": 37,
    "tp_hcm_moc_bai": 59,
    "my_an_cao_lanh": 148,
    "cho_moi_bac_kan": 131,
    "quy_nhon_pleiku": 244,
    "vinh_thanh_thuy": 42,
    "ben_luc_long_thanh": 93,
    "dau_tu_mo_rong_tp_hcm_long_thanh": 54,
    "duong_sat_do_thi_tp_ha_noi__tuyen_nhon_ga_ha_noi": 112,
    "duong_sat_do_thi_tp_ha_noi__tuyen_nam_thang_long_tran_hung_dao": 26,
    "duong_sat_do_thi_tp_ha_noi__dsdt_ha_noi_tuyen_so_5_van_cao_ngoc_khanh_lang_hoa_lac": 32,
    "duong_sat_do_thi_tp_ho_chi_minh__tuyen_ben_thanh_tham_luong": 61,
    "duong_sat_do_thi_tp_ho_chi_minh__tuyen_ben_thanh_suoi_tien": 90,
    "duong_sat_do_thi_tp_ho_chi_minh__tuyen_duong_sat_ben_thanh_can_gio": 9,
}


def _n(s):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", unicodedata.normalize("NFD", (s or "").lower())
                    .encode("ascii", "ignore").decode()).split())


def _num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _ky_key(ky):
    m = re.match(r"(\d+)T(\d{4})", ky or "")
    return (int(m.group(2)), int(m.group(1))) if m else (0, 0)     # (nam, thang) de sap xep tang dan


def run(csv_path, dry):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    # gom theo khoa_du_an cac dong cap 'group' hoac tuyen metro (item co 'tuyen_')
    want = defaultdict(dict)     # khoa -> {as_of: row}
    names = {}
    for r in rows:
        khoa, cap = r.get("khoa_du_an", ""), r.get("cap", "")
        if not khoa or khoa in ("tong_so", "tong_so_von_trong_nuoc", "tong_so_von_ngoai_nuoc"):
            continue
        is_metro = cap == "item" and ("tuyen_" in khoa or "van_cao" in khoa)
        if cap != "group" and not is_metro:
            continue
        want[khoa][r.get("ky", "")] = r          # gom chuoi theo KY (as_of rong toan bo do BOM)
        names[khoa] = r.get("ten_du_an", "")

    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB]
    tr = list(c["Infra_Project_Tracker"].find({"_key": "project"}, {"id": 1, "name": 1}))
    tr_norm = [(p["id"], set(_n(p["name"]).split())) for p in tr]

    def match_tid(khoa, name):
        if khoa in DISB_MAP:
            return DISB_MAP[khoa]
        toks = set(_n(name).split())
        best, bs = None, 0
        for tid, ttok in tr_norm:
            if not ttok:
                continue
            ov = len(toks & ttok) / max(1, len(ttok))
            if ov > bs:
                bs, best = ov, tid
        return best if bs >= 0.6 else None

    matched, unmatched = 0, []
    for khoa, byas in want.items():
        tid = match_tid(khoa, names[khoa])
        series = []
        for ky in sorted(byas, key=_ky_key):     # sap theo thoi gian: 2T2025 -> 7T2026
            r = byas[ky]
            pct, gn, kh = _num(r.get("gn_ty_le")), _num(r.get("gn_tong")), _num(r.get("kh_tong"))
            if pct is not None:
                series.append({"ky": ky, "pct": pct, "gn": gn, "kh": kh})
        if not series:
            continue
        if not tid:
            unmatched.append((khoa, names[khoa]))
            continue
        last = series[-1]
        c_doc = {"tid": tid, "name": names[khoa], "khoa": khoa, "as_of": last["ky"],
                 "kh": last["kh"], "gn": last["gn"], "pct": last["pct"],
                 "series": [{"ky": s["ky"], "pct": s["pct"], "gn": s["gn"]} for s in series]}
        matched += 1
        if not dry:
            c["Infra_Disbursement"].update_one({"tid": tid}, {"$set": c_doc}, upsert=True)

    print(f"Giải ngân: khớp {matched} dự án · chưa khớp {len(unmatched)}")
    for khoa, nm in unmatched[:20]:
        print(f"   ? {khoa[:40]:40} | {nm[:40]}")
    if dry:
        print("(dry-run — chưa ghi)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="giai_ngan_ha_tang_2025_2026.csv")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(os.path.join(HERE, a.csv), a.dry_run)
