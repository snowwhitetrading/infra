# -*- coding: utf-8 -*-
"""
fetch_cafeland.py — Lấy TẤT CẢ data từ map.cafeland.vn (API mở, không cần auth).

Endpoint → collection (dc_commodity):
  /ha-tang/get-list-line?getIdProvince=N → ~111 tuyến hạ tầng TOÀN QUỐC (polyline) → Cafeland_Lines
  /get-duan?page=N        → ~5.220 dự án BĐS                    → Infra_RealEstate
  /get-kcn?page=N         → ~760 khu/cụm CN                     → Infra_IndustrialPark
  /start-map?getIdProvince=N (listPointToUrl) → 189 điểm nổi bật (sân bay/cầu/nút giao) → Cafeland_Points
Lưu: cafeland_*.jsonl (soi tay) + dc_commodity.<collection> (dùng/match).

  python fetch_cafeland.py                          # lấy tất cả
  python fetch_cafeland.py --what lines
  python fetch_cafeland.py --what realestate industrialpark
  python fetch_cafeland.py --dry-run
"""
import argparse
import datetime as dt
import json
import os
import re
import time

import httpx
from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

BASE = "https://map.cafeland.vn"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"}
HERE = os.path.dirname(os.path.abspath(__file__))
DB = "dc_commodity"


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _get(client, path, tries=3):
    for i in range(tries):
        try:
            r = client.get(f"{BASE}/{path}", timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(1.5)
    return None


def fetch_lines(client):
    """Quét MỌI tỉnh qua ?getIdProvince=N (1..119) → gom tuyến toàn quốc (dedup theo id)."""
    seen = {}
    for pid in range(1, 120):
        j = _get(client, f"ha-tang/get-list-line?getIdProvince={pid}")
        for x in (j or {}).get("result", []):
            cid = x.get("id")
            if cid in seen:
                continue
            try:
                coors = json.loads(x.get("coors") or "[]")
            except (ValueError, TypeError):
                coors = []
            coords = [[_f(c.get("lat")), _f(c.get("lng"))] for c in coors
                      if _f(c.get("lat")) and _f(c.get("lng"))]
            content, detail = _content_detail(x.get("content"))   # tách trường chủ đầu tư/vốn/tiến độ
            seen[cid] = {"caf_id": cid, "title": (x.get("title") or "").strip(),
                         "slug": x.get("slug"), "lat": _f(x.get("lat")), "lng": _f(x.get("lng")),
                         "line_type": x.get("line_type"), "line_color": x.get("line_color"),
                         "line_status": x.get("line_status"), "type": x.get("type"),
                         "province_id": x.get("province_id"), "getIdProvince": pid,
                         "content": content, "detail": detail,
                         "n_points": len(coords), "coords": coords}
        time.sleep(0.05)
    return list(seen.values()), "Cafeland_Lines", "caf_id"


def _content_detail(raw):
    """content HTML thô → (text sạch, detail{nhãn:giá trị}). Bắt cặp 'Nhãn: giá trị' theo dòng
    (Vị trí/Quy mô/Tổng mức đầu tư/Nhu cầu sử dụng đất/Chủ đầu tư/Tổng chiều dài/Điểm đầu...)."""
    t = re.sub(r"</(p|li|div)>|<br\s*/?>", "\n", raw or "")
    t = re.sub("<[^>]+>", " ", t).replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", ln).strip(" •-·") for ln in t.split("\n")]
    lines = [ln for ln in lines if len(ln) > 1]
    detail = {}
    for ln in lines:
        m = re.match(r"([A-ZĐÀ-Ỹ][^:]{1,44}?):\s*(.+)", ln)
        if m and len(m.group(2)) > 1:
            detail[m.group(1).strip()] = m.group(2).strip()
    return " · ".join(lines), detail


def _parse_point_content(html):
    """Từ page điểm (pin_id) → tách JSON nhúng lấy content rồi _content_detail."""
    i = html.find('"content":')
    if i < 0:
        return "", {}
    try:
        val, _ = json.JSONDecoder().raw_decode(html[i + len('"content":'):].lstrip())
    except Exception:
        return "", {}
    return _content_detail(val)


def fetch_points(client):
    """Quét ĐIỂM nổi bật (start-map.listPointToUrl) theo từng tỉnh → gồm SÂN BAY, cầu, nút giao,
    cảng, KCN, dự án... Sau đó fetch page mỗi điểm để lấy CONTENT chi tiết (vốn/diện tích/tiến độ)."""
    seen = {}
    for pid in range(1, 120):
        j = _get(client, f"start-map?getIdProvince={pid}")
        for p in ((j or {}).get("result") or {}).get("listPointToUrl", []):
            i = p.get("id")
            if i in seen:
                continue
            seen[i] = {"caf_id": i, "title": (p.get("title") or "").strip(),
                       "slug": p.get("slug"), "url": p.get("url"),
                       "lat": _f(p.get("lat")), "lng": _f(p.get("lng")),
                       "getIdProvince": pid, "content": "", "detail": {}}
        time.sleep(0.04)
    # enrich: fetch trang chi tiết từng điểm → content + detail (đã tách trường)
    pts = list(seen.values())
    for n, d in enumerate(pts, 1):
        if d.get("url"):
            try:
                r = client.get(d["url"], timeout=30)
                d["content"], d["detail"] = _parse_point_content(r.text)
            except Exception:
                pass
        if n % 40 == 0 or n == len(pts):
            print(f"    points detail: {n}/{len(pts)}")
        time.sleep(0.1)
    return pts, "Cafeland_Points", "caf_id"


def _paginate(client, path, extract, label):
    """Lấy hết trang: extract(json)->list. Đọc totalPage ở trang 1."""
    first = _get(client, f"{path}?page=1")
    if not first:
        return []
    total = int(first.get("totalPage") or 1)
    out = list(extract(first))
    for pg in range(2, total + 1):
        j = _get(client, f"{path}?page={pg}")
        if j:
            out.extend(extract(j))
        if pg % 20 == 0 or pg == total:
            print(f"    {label}: {pg}/{total} trang · {len(out)} bản ghi")
        time.sleep(0.25)
    return out


def fetch_duan(client):
    def ex(j):
        r = []
        for x in (j.get("result") or {}).get("list_duan", []):
            r.append({"caf_id": x.get("duan_id"), "title": (x.get("title") or "").strip(),
                      "alias": x.get("alias"), "address": x.get("address"),
                      "lat": _f(x.get("lat")), "lng": _f(x.get("lng")),
                      "type_name": x.get("type_name"), "status_name": x.get("status_name"),
                      "price_min": x.get("price_min"), "price_m2": x.get("price_m2"),
                      "company": x.get("company_name"), "url": x.get("urlduan"),
                      "city": x.get("city_name"), "district": x.get("district_name"),
                      "ward": x.get("ward_name")})
        return r
    return _paginate(client, "get-duan", ex, "realestate"), "Infra_RealEstate", "caf_id"


def fetch_kcn(client):
    def ex(j):
        r = []
        for x in (j.get("result") or []):
            r.append({"caf_id": x.get("id"), "title": (x.get("title") or "").strip(),
                      "alias": x.get("alias"), "address": x.get("addressfull") or x.get("address"),
                      "lat": _f(x.get("lat")), "lng": _f(x.get("lng")),
                      "acreage": x.get("acreage"), "occupancy": x.get("occupancy"),
                      "price": x.get("price"), "status": x.get("icon_status_name"),
                      "url": x.get("url")})
        return r
    return _paginate(client, "get-kcn", ex, "industrialpark"), "Infra_IndustrialPark", "caf_id"


JOBS = {"lines": fetch_lines, "points": fetch_points,
        "realestate": fetch_duan, "industrialpark": fetch_kcn}


def run(what, dry):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    mc = None if dry else MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    with httpx.Client(headers=UA, follow_redirects=True) as client:
        for name in what:
            print(f"\n== {name} ==")
            docs, coll, key = JOBS[name](client)
            print(f"  Tổng {len(docs)} bản ghi.")
            if dry or not docs:
                continue
            # JSONL: mỗi bản ghi 1 dòng — dễ đọc/grep, mở nhẹ hơn 1 mảng khổng lồ
            with open(os.path.join(HERE, f"cafeland_{name}.jsonl"), "w", encoding="utf-8") as f:
                for d in docs:
                    f.write(json.dumps(d, ensure_ascii=False) + "\n")
            col = mc[DB][coll]
            col.create_index(key, unique=True)
            col.bulk_write([UpdateOne({key: d[key]}, {"$set": {**d, "fetched_at": now}},
                                      upsert=True) for d in docs if d.get(key) is not None])
            print(f"  → cafeland_{name}.jsonl + {DB}.{coll} ({col.estimated_document_count()} doc)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--what", nargs="*", default=list(JOBS),
                    choices=list(JOBS), help="loại data (mặc định: tất cả)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(a.what, a.dry_run)
