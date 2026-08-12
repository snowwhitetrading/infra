# -*- coding: utf-8 -*-
"""
seed_geo.py — Vẽ SẴN hình học bản đồ (line/area) cho các dự án chính.

Toạ độ GẦN ĐÚNG theo vị trí thực (line = hướng tuyến sơ bộ giữa các đầu tuyến;
area = ô khoanh vùng quanh điểm). Tinh chỉnh từng dự án sau bằng set_geo.py.

  python seed_geo.py            # ghi geo cho các id dưới đây
  python seed_geo.py --dry-run  # chỉ in, không ghi
"""
import argparse
from pymongo import MongoClient
from lib_db import mongo_uri

# ---- Đường & đường sắt: line nối các đầu tuyến (hướng tuyến sơ bộ) ----
LINES = {
    # cao tốc
    "cao_t_c_bi_n_h_a_v_ng_t_u": [[10.95, 106.82], [10.65, 107.02], [10.40, 107.08]],
    "cao_t_c_ch_u_c_c_n_th_s_c_tr_ng": [[10.70, 105.12], [10.03, 105.78], [9.60, 105.97]],
    "cao_t_c_pleiku_bu_n_ma_thu_t_gia_ngh_a": [[13.98, 108.00], [12.67, 108.05], [11.99, 107.69]],
    "expr_baoha_laichau": [[22.19, 104.35], [22.30, 103.90], [22.39, 103.46]],
    "expr_baoloc_lienkhuong": [[11.55, 107.81], [11.75, 108.37]],
    "expr_bung_vanninh": [[17.66, 106.47], [17.45, 106.62]],
    "expr_camlam_nhatrang": [[11.90, 109.13], [12.24, 109.19]],
    "expr_caobo_maison": [[20.22, 106.02], [20.18, 105.82]],
    "expr_hanoi_haiphong": [[20.98, 105.90], [20.93, 106.30], [20.86, 106.68]],
    "expr_lasson_hoalien": [[16.30, 107.72], [16.13, 108.13]],
    "expr_myhuan_cantho": [[10.28, 105.91], [10.03, 105.78]],
    "expr_ninhbinh_haiphong": [[20.25, 105.97], [20.60, 106.40], [20.86, 106.68]],
    "expr_phanthiet_daugiay": [[10.93, 108.10], [10.94, 107.15]],
    "expr_quangngai_nhatrang": [[15.12, 108.80], [13.77, 109.22], [12.24, 109.19]],
    "expr_vinh_thanhthuy": [[18.68, 105.68], [18.85, 105.20]],
    # vành đai (vòng)
    "ring4_hcmc": [[11.05, 106.60], [10.95, 106.45], [10.75, 106.45], [10.65, 106.65],
                   [10.80, 106.85], [11.00, 106.80], [11.05, 106.60]],
    "vanh_dai_5_thu_do": [[21.30, 105.60], [21.30, 106.10], [20.90, 106.30], [20.60, 106.00],
                          [20.75, 105.50], [21.10, 105.40], [21.30, 105.60]],
    # đường kết nối Gia Bình
    "road_giabinh_bacninh": [[21.05, 106.28], [21.10, 106.15], [21.18, 106.08]],
    "road_giabinh_hanoi": [[21.05, 106.28], [21.02, 106.10], [21.02, 105.90]],
    "bt1": [[21.06, 106.29], [21.05, 106.26]],
    # đường sắt / metro
    "duong_sat_thu_thiem_long_thanh": [[10.78, 106.72], [10.90, 106.85], [10.97, 106.98]],
    "hsr_bac_nam": [[21.03, 105.85], [18.68, 105.68], [16.06, 108.22], [12.24, 109.19], [10.80, 106.72]],
    "metro_hcm_binh_duong": [[10.87, 106.80], [11.00, 106.72], [11.05, 106.68]],
    "metro_nam_th_ng_long_tr_n_h_ng_o_h_n_i_t": [[21.08, 105.79], [21.05, 105.82], [21.02, 105.85]],
    "metro_s_4_tp_hcm": [[10.87, 106.65], [10.78, 106.70], [10.72, 106.72]],
    "metro_so_5_ha_noi": [[21.04, 105.81], [21.01, 105.65], [21.01, 105.52]],
    "ng_s_t_h_i_ph_ng_h_long_m_ng_c_i": [[20.86, 106.68], [20.95, 107.08], [21.53, 107.97]],
    "phu_quoc_tram": [[10.22, 103.96], [10.16, 103.99]],
    "rail_benthanh_cangio": [[10.77, 106.70], [10.60, 106.80], [10.41, 106.95]],
    "rail_hanoi_quangninh": [[21.03, 105.85], [21.00, 106.40], [20.95, 107.08]],
    "rail_laocai_hn_hp": [[22.48, 103.97], [21.03, 105.85], [20.86, 106.68]],
}

# ---- Sân bay / cảng / khu: ô khoanh vùng (bán kính độ quanh điểm) ----
AREAS = {  # id: bán kính (độ) ~ 0.01 ≈ 1.1km
    "airport_long_thanh": 0.030,
    "airport_tan_son_nhat": 0.012,
    "phu_quoc_airport": 0.015,
    "port_lach_huyen": 0.012,
    "port_lien_chieu": 0.012,
    "port_tien_sa": 0.010,
    "rach_chiec": 0.008,
    "apec_center": 0.010,
    "bai_dat_do": 0.012,
    "nui_ong": 0.012,
}


def box(lat, lon, r):
    return [[lat + r, lon - r], [lat + r, lon + r], [lat - r, lon + r], [lat - r, lon - r]]


def main():
    dry = argparse.ArgumentParser().add_argument  # noqa
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    dry = ap.parse_args().dry_run
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    reg = c["dc_commodity"]["Infra_Projects_Registry"]
    n_line = n_area = miss = 0
    for pid, coords in LINES.items():
        d = reg.find_one({"id": pid})
        if not d:
            print(f"  (bỏ) không thấy id={pid}")
            miss += 1
            continue
        n_line += 1
        print(f"  line  {pid:<40} {len(coords)} điểm")
        if not dry:
            reg.update_one({"id": pid}, {"$set": {"geo": {"type": "line", "coords": coords}}})
    for pid, r in AREAS.items():
        d = reg.find_one({"id": pid})
        if not d or d.get("lat") is None:
            print(f"  (bỏ) không thấy id/lat={pid}")
            miss += 1
            continue
        coords = box(d["lat"], d["lon"], r)
        n_area += 1
        print(f"  area  {pid:<40} r={r}")
        if not dry:
            reg.update_one({"id": pid}, {"$set": {"geo": {"type": "area", "coords": coords}}})
    tag = " (dry)" if dry else ""
    print(f"\nĐã vẽ{tag}: {n_line} đường/đường sắt + {n_area} vùng · bỏ {miss}. "
          f"Cầu/hầm/điểm phụ giữ nguyên chấm.")


if __name__ == "__main__":
    main()
