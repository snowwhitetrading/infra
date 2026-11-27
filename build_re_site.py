# -*- coding: utf-8 -*-
"""
build_re_site.py — Dựng trang THEO DÕI DỰ ÁN BẤT ĐỘNG SẢN (bds/index.html) từ cơ sở dữ liệu dc_realestate.

Trang chị em của trang hạ tầng (cùng giao diện: lấy khối <style> của vn-infra-tracker.template.html), các tab:
  Tổng quan · Dự án (giá + tin rao) · Dòng tin · Bán hàng · Vệ tinh · Bản đồ

CHỈ dùng dữ liệu có nguồn công khai / công bố của doanh nghiệp:
  RE_Project_Price_History_Clean  giá rao bán, giá thuê theo dự án (batdongsan.com.vn)
  RE_Project_Listing_Counts       số tin rao bán đang hoạt động theo ngày
  RE_Company_Presales_Quarterly   doanh số bán hàng theo quý (công bố IR của doanh nghiệp)
  RE_News_Collection_*            tóm tắt tin thị trường hằng ngày / hằng tuần
KHÔNG đưa lên trang (trang công khai): mô hình định giá RNAV, giá mục tiêu, NPV, tên chuyên viên.

  python build_re_site.py --out dist/bds/index.html
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import unicodedata
from collections import defaultdict

from pymongo import MongoClient

from lib_db import mongo_uri

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "vn-re-tracker.template.html")
BASE_TPL = os.path.join(HERE, "vn-infra-tracker.template.html")
NQ, NLIST = 12, 26          # số quý giá giữ lại · số điểm tin rao (theo tuần)
# Cùng một dự án nhưng bảng giá và bảng tin rao đặt tên khác nhau → gộp về một tên (chỉ các cặp chắc chắn).
CANON = {"VHM Grand Park": "Vinhomes Grand Park", "VHM Smart City": "Vinhomes Smart City",
         "VHM OCP1": "Vinhomes Ocean Park Gia Lâm", "VHM Royal City": "Vinhomes Royal City", "Royal City": "Vinhomes Royal City",
         "Global City": "The Global City", "Akari City Nam Long": "Akari City",
         "Khu đô thị Vạn Phúc City": "Vạn Phúc City", "Vinhomes Wonder City": "Vinhomes Wonder City Đan Phượng"}
_canon = lambda n: CANON.get(n, n)


def _qkey(k):
    """'2026-Q3' → (2026,3) · '2026-10' → (2026,4). None nếu không đọc được."""
    m = re.match(r"(\d{4})-Q(\d)$", k or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"(\d{4})-(\d{2})$", k or "")
    if m:
        return int(m.group(1)), (int(m.group(2)) - 1) // 3 + 1
    return None


def price_series(R):
    """{dự án: {dev,ticker,city, sale:[[quý,avg,min,max]], rent:[[quý,avg]]}} — theo QUÝ.
    Có dòng quý thì dùng dòng quý; quý nào chỉ có số tháng thì lấy trung bình các tháng trong quý."""
    P = {}
    acc = defaultdict(lambda: {"q": {}, "m": defaultdict(list)})
    for x in R["RE_Project_Price_History_Clean"].find(
            {"price_avg": {"$ne": None}},
            {"_id": 0, "project": 1, "developer": 1, "ticker": 1, "city": 1, "deal_type": 1, "frequency": 1,
             "period_key": 1, "price_min": 1, "price_avg": 1, "price_max": 1}):
        qk = _qkey(x.get("period_key"))
        if not qk or not x.get("project") or x.get("deal_type") not in ("sale", "rent"):
            continue
        x["project"] = _canon(x["project"])
        p = P.setdefault(x["project"], {"dev": x.get("developer") or "", "ticker": x.get("ticker") or "", "city": x.get("city") or ""})
        for f, k in (("dev", "developer"), ("ticker", "ticker"), ("city", "city")):
            if not p[f] and x.get(k):
                p[f] = x[k]
        a = acc[(x["project"], x["deal_type"])]
        row = (x["price_avg"], x.get("price_min"), x.get("price_max"))
        if x.get("frequency") == "quarterly":
            a["q"][qk] = row
        else:
            a["m"][qk].append(row)
    for (name, deal), a in acc.items():
        ser = {}
        for qk, rows in a["m"].items():
            mins = [r[1] for r in rows if r[1] is not None]
            maxs = [r[2] for r in rows if r[2] is not None]
            ser[qk] = (sum(r[0] for r in rows) / len(rows), min(mins) if mins else None, max(maxs) if maxs else None)
        ser.update(a["q"])
        out = [[f"{y}-Q{q}", round(v[0], 1), v[1], v[2]] for (y, q), v in sorted(ser.items())][-NQ:]
        P[name][deal] = out if deal == "sale" else [[r[0], r[1]] for r in out]
    return P


def listing_series(R):
    """{dự án: {brand, prov, list:[[ngày, số tin]]}} — lấy mẫu theo tuần (điểm cuối luôn là ngày mới nhất)."""
    by = defaultdict(dict)
    meta = {}
    for x in R["RE_Project_Listing_Counts"].find(
            {"segment": "sale"}, {"_id": 0, "project": 1, "brand": 1, "province": 1, "snapshot_date": 1, "total_listings": 1}):
        if x.get("project") and x.get("snapshot_date") and x.get("total_listings") is not None:
            nm = _canon(x["project"])
            by[nm][x["snapshot_date"]] = x["total_listings"]
            meta.setdefault(nm, (x.get("brand") or "", x.get("province") or ""))
    out = {}
    for name, d in by.items():
        days = sorted(d)
        pick = days[::-1][::7][::-1]                     # mỗi 7 ngày, tính lùi từ ngày mới nhất
        out[name] = {"brand": meta[name][0], "prov": meta[name][1], "list": [[k, d[k]] for k in pick][-NLIST:]}
    return out


def presales(R):
    """Doanh số bán hàng: công ty theo quý {mã: [[kỳ, tỷ đồng, độ tin cậy]]} + dự án [{mã, dự án, kỳ, tỷ, tin cậy}]."""
    comp, proj = defaultdict(dict), []
    for x in R["RE_Company_Presales_Quarterly"].find(
            {"channel": "total", "basis": "quarterly", "value_vnd_bn": {"$ne": None}},
            {"_id": 0, "ticker": 1, "level": 1, "period_key": 1, "value_vnd_bn": 1, "confidence": 1, "project_name": 1}):
        if not x.get("ticker") or not re.match(r"\d{4}Q\d$", x.get("period_key") or ""):
            continue
        if x.get("level") == "company_total":
            comp[x["ticker"]][x["period_key"]] = [x["period_key"], round(x["value_vnd_bn"]), x.get("confidence") or ""]
        elif x.get("level") == "project" and x.get("project_name"):
            proj.append({"t": x["ticker"], "n": x["project_name"], "k": x["period_key"],
                         "v": round(x["value_vnd_bn"]), "c": x.get("confidence") or ""})
    return {t: [d[k] for k in sorted(d)][-12:] for t, d in comp.items()}, proj


def _texts(v, n=6):
    """Danh sách [{text, citations}] hoặc [str] → [{t, src:[{title,url,source}]}] gọn."""
    out = []
    for it in (v or [])[:n]:
        if isinstance(it, str):
            out.append({"t": it, "src": []})
        elif isinstance(it, dict):
            t = it.get("text") or it.get("theme") or it.get("description") or ""
            src = [{"title": (c.get("title") or "")[:140], "url": c.get("url") or "", "source": c.get("source") or ""}
                   for c in (it.get("citations") or [])[:3] if isinstance(c, dict) and c.get("url")]
            if t:
                out.append({"t": t, "src": src, "s": it.get("sentiment") or ""})
    return out


def news(R):
    daily = []
    for x in R["RE_News_Collection_Daily_Snapshot"].find(
            {"segment": {"$in": ["residential", "industrial", "construction"]}},
            {"_id": 0, "date": 1, "segment": 1, "headline": 1, "summary_narrative": 1, "sentiment": 1,
             "article_count": 1, "investment_signals": 1, "risk_events": 1}).sort("date", -1).limit(120):
        daily.append({"d": x.get("date"), "seg": x.get("segment"), "h": x.get("headline") or "",
                      "s": x.get("summary_narrative") or "", "tone": x.get("sentiment") or "", "n": x.get("article_count") or 0,
                      "sig": _texts(x.get("investment_signals"), 4), "risk": _texts(x.get("risk_events"), 3)})
    weekly = {}
    for seg in ("residential", "industrial", "construction"):
        w = R["RE_News_Collection_Weekly_Digest"].find_one({"segment": seg}, sort=[("week_of", -1)])
        if w:
            weekly[seg] = {"week": w.get("week_of"), "period": w.get("period") or "", "n": w.get("article_count") or 0,
                           "tone": w.get("sentiment") or "", "summary": w.get("summary") or "",
                           "price": _texts(w.get("price_signals")), "policy": _texts(w.get("policy_updates")),
                           "fin": _texts(w.get("financing_banking")), "gov": _texts(w.get("government_intention"))}
    return daily, weekly


# ---------------------------------------------------------------- vị trí + ảnh vệ tinh
def _n(s):
    t = unicodedata.normalize("NFD", (s or "").lower().replace("đ", "d")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", t)).strip()


_GEO_STOP = set("khu do thi du an chung cu can ho the kdt tai to hop moi phuc nha o va city".split())
# dải vĩ độ hợp lệ theo tỉnh/thành của dự án — chặn ghép nhầm sang dự án trùng tên ở vùng khác
_LAT_BAND = [(r"ha noi|hung yen|hai phong|bac ninh|quang ninh|vinh phuc|ha nam|thai nguyen", 20.2, 21.6),
             (r"hcm|ho chi minh|binh duong|vung tau|dong nai|long an|tay ninh", 10.2, 11.5),
             (r"da nang|quang nam", 15.4, 16.3), (r"khanh hoa", 11.6, 12.9)]


def _lat_ok(city, lat):
    c = _n(city)
    for rx, lo, hi in _LAT_BAND:
        if re.search(rx, c):
            return lo <= lat <= hi
    return True


def geo_match(client, projects):
    """Gắn toạ độ cho dự án theo tên trên map.cafeland.vn (Infra_RealEstate). Chỉ nhận khi tên dự án nằm trọn
    trong tên Cafeland (≥2 từ đặc trưng) và vĩ độ hợp với tỉnh của dự án; không khớp thì để trống, KHÔNG đoán."""
    caf = [(_n(x.get("title")), x) for x in client["dc_commodity"]["Infra_RealEstate"].find(
        {"lat": {"$ne": None}}, {"_id": 0, "title": 1, "lat": 1, "lng": 1})]
    n = 0
    for p in projects:
        k = _n(p["n"]); kt = set(k.split()) - _GEO_STOP
        best = None
        for t, x in caf:
            if not _lat_ok(p.get("city"), x["lat"]):
                continue
            if t == k:
                best = (99, x); break
            tt = set(t.split())
            if len(kt) >= 2 and kt <= tt:
                sc = 2 - 0.01 * len(tt)                 # tên Cafeland càng ngắn càng sát
                if not best or sc > best[0]:
                    best = (sc, x)
        if best:
            p["lat"], p["lng"] = round(best[1]["lat"], 5), round(best[1]["lng"], 5); n += 1
    return n


def townships(client):
    """Khu đô thị đối ứng đang theo dõi ở trang hạ tầng (kind=township + Khu đô thị Olympic): tên, chủ đầu tư,
    quy mô vốn, toạ độ (registry, lấy từ đúng dự án trên Cafeland) và ảnh vệ tinh theo tháng (manifest)."""
    db = client["dc_commodity"]
    reg = {d["tid"]: d for d in db["Infra_Projects_Registry"].find({"tid": {"$ne": None}}, {"_id": 0, "tid": 1, "id": 1, "lat": 1, "lon": 1})}
    sat = {}
    mf = os.path.join(HERE, "satellite_export", "manifest.csv")
    if os.path.exists(mf):
        for r in csv.DictReader(open(mf, encoding="utf-8")):
            sat.setdefault(r["site_key"], []).append({"m": r["month"], "d": r["capture_date"], "f": r["file"],
                                                      "c": float(r["aoi_cloud_pct"] or 0), "ok": r["status"] == "OK"})
    out = []
    for d in db["Infra_Project_Tracker"].find({"_key": "project", "$or": [{"kind": "township"}, {"id": 1280}]},
                                              {"_id": 0, "id": 1, "name": 1, "loc": 1, "developer_groups": 1, "tmdt": 1,
                                               "planWin": 1, "paceWhyLLM": 1, "paceStage": 1}):
        r = reg.get(d["id"], {})
        imgs = sorted(sat.get(r.get("id"), []), key=lambda x: x["m"])
        out.append({"id": d["id"], "n": d["name"], "loc": d.get("loc") or "", "dev": ", ".join(d.get("developer_groups") or []) or "Vingroup",
                    "tmdt": d.get("tmdt"), "win": [(d.get("planWin") or {}).get("from"), (d.get("planWin") or {}).get("to")],
                    "stage": d.get("paceStage") or "", "why": d.get("paceWhyLLM") or "",
                    "lat": r.get("lat"), "lng": r.get("lon"), "sat": imgs})
    out.sort(key=lambda x: -(x["tmdt"] or 0))
    return out


def build():
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    R = c["dc_realestate"]
    P, L = price_series(R), listing_series(R)
    names = sorted(set(P) | set(L))
    projects = []
    for nm in names:
        p, l = P.get(nm, {}), L.get(nm, {})
        projects.append({"n": nm, "dev": p.get("dev") or l.get("brand") or "", "ticker": p.get("ticker") or "",
                         "city": p.get("city") or l.get("prov") or "", "sale": p.get("sale") or [], "rent": p.get("rent") or [],
                         "list": l.get("list") or []})
    ngeo = geo_match(c, projects)
    towns = townships(c)
    comp, proj = presales(R)
    daily, weekly = news(R)
    # dự án hạ tầng đang theo dõi có khối BĐS → liên kết ngược (chỉ tên, không số mô hình)
    data = {"asof": dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).strftime("%d/%m/%Y %H:%M"),
            "projects": projects, "towns": towns, "presales": comp, "presalesProj": proj, "daily": daily, "weekly": weekly}
    print(f"Bất động sản: {len(projects)} dự án ({sum(1 for p in projects if p['sale'])} có giá bán, "
          f"{sum(1 for p in projects if p['list'])} có tin rao) · {len(comp)} doanh nghiệp có doanh số · "
          f"{len(daily)} bản tin ngày · {len(weekly)} bản tin tuần · {ngeo} dự án có toạ độ · "
          f"{len(towns)} khu đô thị ({sum(1 for t in towns if t['sat'])} có ảnh vệ tinh)")
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="vn-re-tracker.html")
    a = ap.parse_args()
    data = build()
    tpl = open(TEMPLATE, encoding="utf-8").read()
    base = open(BASE_TPL, encoding="utf-8").read()
    css = re.search(r"<style>(.*?)</style>", base, re.S).group(1)       # dùng chung giao diện với trang hạ tầng
    assert tpl.count("/*__BASE_CSS__*/") == 1 and tpl.count("const RE = {};") == 1
    out = tpl.replace("/*__BASE_CSS__*/", css, 1)
    out = out.replace("const RE = {};", "const RE = " + json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/") + ";", 1)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, "w", encoding="utf-8", newline="").write(out)
    print(f"Đã dựng: {a.out} ({len(out) // 1024} KB)")


if __name__ == "__main__":
    main()
