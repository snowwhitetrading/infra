# -*- coding: utf-8 -*-
"""
prepare_pace_bundles.py — Gom TIN GẦN ĐÂY của mỗi dự án theo dõi thành bundle JSON
để AGENT ĐỌC-HIỂU đánh giá nhịp độ (vượt/chậm/đúng), thay cho regex chỉ-vài-indicator.

Gate: TIÊU ĐỀ chứa alias dự án (tránh gán nhầm) · bỏ nhiễu (kẹt xe/CSGT/tai nạn/mở bán).
Ra: pace_bundles.json = {tid: {name, prov, news:[{date, source, title, desc}], marks:[...]}}
  (tin sắp mới→cũ, tối đa MAXN bài; kèm mốc thi công/hoàn thành đã có để agent tham chiếu.)

  python prepare_pace_bundles.py [--months 10] [--maxn 30] [--out pace_bundles.json]
"""
import argparse
import json
import os
import re
from collections import defaultdict

from pymongo import MongoClient

from lib_db import mongo_uri
from lib_marks import RX_NOISE
from lib_projects import build_alias_regex, pid2tid

HERE = os.path.dirname(os.path.abspath(__file__))
DB = "dc_commodity"


# VỆ TINH và GIẢI NGÂN chỉ là bằng chứng khi tín hiệu RÕ và dự án ĐỦ LỚN — không rõ/không quan trọng thì KHÔNG đưa
# cho agent (tránh nhiễu). Cùng ngưỡng với trang (vn-infra-tracker.template.html: SAT_MIN_TMDT / DISB_MIN_KH).
SAT_MIN_TMDT = 10000      # tỷ đồng — dự án từ 10 nghìn tỷ mới xét ảnh vệ tinh
DISB_MIN_KH = 500         # tỷ đồng — kế hoạch vốn năm từ 500 tỷ mới xét giải ngân


def sat_usable(p):
    tm = p.get("tmdt") or p.get("tmdtLLM") or ((p.get("tuCo") or 0) + (p.get("huyDong") or 0))
    return bool(p.get("satObs")) and p.get("satTrend") not in (None, "", "không rõ")         and p.get("satConf") != "thấp" and (tm or 0) >= SAT_MIN_TMDT


def _minus_months(ym, k):
    y, m = int(ym[:4]), int(ym[5:7])
    t = y * 12 + (m - 1) - k
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def _ky_ym(ky):
    """'5T2026' -> '2026-05' để so với asof."""
    m = re.match(r"(\d+)T(\d{4})", ky or "")
    return f"{m.group(2)}-{int(m.group(1)):02d}" if m else ""


# Tin NÓI VỀ THI CÔNG/TIẾN ĐỘ được ưu tiên đưa cho agent (đây chỉ là bước CHỌN TIN, không phải chấm điểm —
# phán đoán vẫn do agent đọc-hiểu). 2 = có con số/mốc khối lượng · 1 = nói về thi công, mặt bằng, tiến độ · 0 = khác.
_RX_STRONG = re.compile(r"\d+([.,]\d+)?\s*%|sản lượng|khối lượng|hợp long|thông xe|cất nóc|thông hầm|khánh thành|"
                        r"nghiệm thu|chạy thử|bay hiệu chuẩn|đưa vào khai thác|hoàn thành", re.I)
_RX_WORK = re.compile(r"thi công|tiến độ|khởi công|tăng tốc|dồn lực|đẩy nhanh|về đích|nước rút|3 ca|mặt bằng|bàn giao|"
                      r"tái định cư|lắp đặt|thảm nhựa|đường băng|nhà ga|công trường|chậm|vướng|lùi|đình trệ|"
                      r"dừng thi công|giải ngân|gói thầu|nhà thầu", re.I)


def _rel(a):
    t = a["title"] + " " + a.get("desc", "")
    return 2 if _RX_STRONG.search(t) else (1 if _RX_WORK.search(t) else 0)


def pick_news(arts, maxn):
    """Chọn ≤maxn tin: bỏ trùng tiêu đề, TRẢI ĐỀU các tháng (tháng mới trước), trong mỗi tháng ưu tiên tin về thi công.
    Trước đây lấy maxn tin MỚI NHẤT → dự án nhiều tin chỉ còn tin của 2 tuần cuối, mất hết diễn biến các tháng trước."""
    seen, by_m = set(), defaultdict(list)
    for a in sorted(arts, key=lambda x: x["date"], reverse=True):
        k = a["title"][:80]
        if k in seen:
            continue
        seen.add(k)
        by_m[a["date"][:7]].append(a)
    months_ = sorted(by_m, reverse=True)
    for m in months_:
        by_m[m].sort(key=_rel, reverse=True)                     # sort ổn định: cùng mức ưu tiên thì giữ tin mới trước
    out, i = [], 0
    while len(out) < maxn and any(by_m[m] for m in months_):
        m = months_[i % len(months_)]
        if by_m[m]:
            out.append(by_m[m].pop(0))
        i += 1
    return sorted(out, key=lambda x: x["date"], reverse=True)


def run(months, maxn, out, only=None, asof=None, history=0):
    import datetime as dt
    only_set = None
    if only:
        with open(os.path.join(HERE, only), encoding="utf-8") as f:
            only_set = set(json.load(f))
        print(f"CHỈ chấm {len(only_set)} dự án trong {only} (nhóm lỗi thời)")
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    tr = c[DB]["Infra_Project_Tracker"]
    raw = c["dc_news"]["project_news_raw"]
    p2t = pid2tid()
    t2rx = defaultdict(list)
    for pid, r in build_alias_regex().items():
        if pid in p2t:
            t2rx[p2t[pid]].append(r)

    anchor = asof if asof else dt.date.today().strftime("%Y-%m")   # mốc "hiện tại" (hồi tố: hết tháng asof)
    cutoff = _minus_months(anchor, months)
    bundles = {}
    tr_meta = {p["id"]: p for p in tr.find({"_key": "project"},
               {"id": 1, "name": 1, "loc": 1, "status": 1, "phases": 1, "marks": 1, "items": 1,
                "paceProgress": 1, "satObs": 1, "satTrend": 1, "satPace": 1, "satStage": 1,
                "satLast": 1, "satConf": 1, "tmdt": 1, "tmdtLLM": 1, "tuCo": 1, "huyDong": 1})}
    disb = {d["tid"]: d for d in c[DB]["Infra_Disbursement"].find({}, {"_id": 0})}   # giải ngân BTC
    _kys = [_ky_ym(d.get("as_of")) for d in disb.values() if _ky_ym(d.get("as_of"))]
    last_ky = max(_kys) if _kys else ""                      # kỳ giải ngân mới nhất của cả danh mục
    news_by_tid = defaultdict(list)
    for d in raw.find({"projects": {"$ne": []}},
                      {"title": 1, "description": 1, "date": 1, "source": 1, "projects": 1}):
        ti = d.get("title") or ""
        date = (d.get("date") or "")[:10]
        if len(date) < 7 or date[:7] < cutoff or date[:7] > anchor:   # trong [cutoff, asof] — hồi tố không lấy tin TƯƠNG LAI
            continue
        if RX_NOISE.search(ti):                      # bỏ nhiễu
            continue
        for tid in d.get("projects", []):
            if tid in tr_meta and any(r.search(ti) for r in t2rx.get(tid, [])):
                news_by_tid[tid].append({"date": date, "source": d.get("source", "?"),
                                         "title": ti, "desc": (d.get("description") or "")[:300]})

    for tid, arts in news_by_tid.items():
        if only_set is not None and tid not in only_set:      # chỉ chấm nhóm lỗi thời
            continue
        uniq = pick_news(arts, maxn)
        p = tr_meta[tid]
        phz = [{"kind": ph.get("kind"), "from": ph.get("from"), "to": ph.get("to"),
                "state": ph.get("state")} for ph in p.get("phases", [])]
        dl = [m for m in p.get("marks", []) if m.get("tier") == "deadline"]
        b = {"name": p.get("name", ""), "loc": p.get("loc", ""),
             "status": p.get("status", ""),
             "phases": phz, "deadline": dl[-1] if dl else None,
             # hồi tố (asof): không đưa mức % hiện tại vào, tránh rò thông tin tương lai
             "prev_progress": None if asof else p.get("paceProgress"),
             # hạng mục đã curate → agent chấm % TỪNG hạng mục (giữ nguyên name để apply khớp lại)
             "items": [{"name": it.get("name", ""), "prog": (it.get("prog") or "")[:400],
                        "sched": [f"{ph.get('from') or '?'}→{ph.get('to') or '?'}" for ph in it.get("phases", [])
                                  if ph.get("from") or ph.get("to")]}
                       for it in p.get("items", []) if it.get("name")],
             # mốc đã curate có nguồn (cam kết/chỉ đạo/hợp đồng/hạn/đã xảy ra) → agent dựng MỐC KẾ HOẠCH THẬT
             "marks": [{"date": m.get("date"), "tier": m.get("tier"), "label": (m.get("label") or "")[:140]}
                       for m in p.get("marks", [])
                       if m.get("date") and m.get("tier") in ("contract", "directive", "company", "deadline", "actual")
                       and (not asof or str(m.get("date"))[:7] <= asof or m.get("tier") != "actual")][-18:],
             "news": uniq}
        if history:                                      # quỹ đạo: agent ước lượng lại % các tháng này CÙNG LÚC
            hm = [_minus_months(anchor, k) for k in range(history, 0, -1)]
            b["history_months"] = [m for m in hm if m >= cutoff]
        if tid in disb and (disb[tid].get("kh") or 0) >= DISB_MIN_KH                 and (asof or _ky_ym(disb[tid].get("as_of")) == last_ky):   # chỉ khi kế hoạch vốn đủ lớn + số liệu kỳ mới nhất
            d = disb[tid]
            ser = d.get("series", [])
            if asof:                                     # hồi tố: chỉ kỳ ≤ asof
                ser = [s for s in ser if _ky_ym(s.get("ky")) and _ky_ym(s.get("ky")) <= asof]
            b["disbursement"] = {"kh": d.get("kh"),
                                 "pct": (ser[-1].get("pct") if ser else None) if asof else d.get("pct"),
                                 "trend": [f"{s.get('ky')}:{s.get('pct')}%" for s in ser]}
            if not asof:
                b["disbursement"]["as_of"] = d.get("as_of")
        if sat_usable(p):                                # vệ tinh: chỉ dự án lớn có tín hiệu rõ
            b["satellite"] = {k: v for k, v in {
                "obs": p.get("satObs"), "trend": p.get("satTrend"),
                "pace": p.get("satPace"),            # nhịp gần đây: tăng tốc/đều/chậm lại/đứng yên
                "stage": p.get("satStage"),          # chặng thi công thấy trên ảnh mới nhất
                "last_image": p.get("satLast"), "conf": p.get("satConf")}.items() if v}
        bundles[str(tid)] = b

    with open(os.path.join(HERE, out), "w", encoding="utf-8") as f:
        json.dump(bundles, f, ensure_ascii=False, indent=1)
    n_news = sum(len(b["news"]) for b in bundles.values())
    print(f"Bundle: {len(bundles)} dự án · {n_news} tin (≤{months} tháng, ≤{maxn}/dự án) → {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=10)
    ap.add_argument("--maxn", type=int, default=30)
    ap.add_argument("--out", default="pace_bundles.json")
    ap.add_argument("--only", default=None, help="file JSON danh sách tid (chỉ chấm nhóm này, vd stale_pace.json)")
    ap.add_argument("--asof", default=None, help="HỒI TỐ: chỉ lấy tin/giải ngân ≤ tháng này (YYYY-MM)")
    ap.add_argument("--history", type=int, default=0,
                    help="ước lượng lại QUỸ ĐẠO % của N tháng liền trước tháng chấm (0 = không); nên ≤ --months")
    a = ap.parse_args()
    run(a.months, a.maxn, a.out, a.only, a.asof, a.history)
