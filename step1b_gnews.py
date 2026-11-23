# -*- coding: utf-8 -*-
"""
step1b_gnews.py — Scrape tin dự án qua GOOGLE NEWS RSS (gom MỌI báo VN, gồm báo địa phương 34 tỉnh).

Tìm theo TÊN dự án (alias) trong registry → mỗi query trả tin từ nhiều nguồn (Tuổi Trẻ, Lao Động,
Hànộimới, báo tỉnh…). Hỗ trợ HISTORICAL qua cửa sổ thời gian (after:/before:) để dựng DB tiến độ.
Ghi dc_news.project_news_raw (source='gnews:<báo>', projects=[tid]).

  python step1b_gnews.py                    # tin gần đây, tất cả dự án
  python step1b_gnews.py --history 2025-01  # backfill từ 2025-01 tới nay (cửa sổ 3 tháng)
  python step1b_gnews.py --limit 5 --dry-run
"""
import argparse
import datetime as dt
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx
from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

DB, COLL = "dc_news", "project_news_raw"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"}
GNEWS = "https://news.google.com/rss/search?q={q}&hl=vi&gl=VN&ceid=VN:vi"


# Cách viết khác nhau của CÙNG một thứ → quy về 1 token trước khi so (áp cho cả bí danh lẫn tiêu đề)
_SYN = [(r"cang hang khong quoc te|cang hang khong|san bay quoc te|san bay", "sanbay"),
        (r"duong sat do thi|tau dien ngam|metro", "metro"),
        (r"duong cao toc|cao toc", "caotoc"),
        (r"quoc lo\s*|\bql\.?\s*(?=\d)", "ql "),
        (r"duong vanh dai|vanh dai|\bvd\s*(?=\d)", "vanhdai "),
        (r"tp\.?\s?hcm|tphcm|tp\.?\s?ho chi minh|thanh pho ho chi minh", "hcm"),
        (r"tp\.?\s?ha noi|thanh pho ha noi", "ha noi"),
        (r"nha may thuy dien|\bnmtd\b", "thuydien"),
        (r"nha may nhiet dien|\bnmnd\b", "nhietdien")]
# từ đệm / từ chỉ hành động chung, không bắt buộc có trong tiêu đề
_STOP = {"du", "an", "tuyen", "doan", "cua", "khau", "va", "tai", "the", "cac", "nang", "cap"}


def _norm(s):
    t = re.sub(r"[^\w\s.]", " ", (s or "").lower().replace("đ", "d"))     # gạch nối/en-dash → khoảng trắng (không dính chữ)
    t = unicodedata.normalize("NFD", t).encode("ascii", "ignore").decode()
    for pat, to in _SYN:
        t = re.sub(pat, to, t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", t)).strip()


def _toks(s):
    """Token của chuỗi đã chuẩn hoá — GIỮ số và từ ngắn ('số 5', 'Hà Nội'): chính chúng phân biệt các dự án."""
    return set(t for t in _norm(s).split() if t not in _STOP)


def _relevant(alias, title):
    """Bài có thực sự về dự án? TIÊU ĐỀ phải chứa ĐỦ mọi token của bí danh (thứ tự tự do, nên vẫn bắt được
    'Hồ Tràm - Long Thành' viết ngược). Trước đây chỉ cần 60% token trên tiêu đề+mô tả → ~8% bài gắn nhầm
    (vd 'sân bay Nội Bài' nhận bài sân bay Gia Bình; 'cảng hàng không Rạch Giá' nhận bài hội chợ ở Rạch Giá)."""
    at = _toks(alias)
    return bool(at) and at <= _toks(title)


def _tkey(title):
    """Khoá khử trùng theo tiêu đề (cùng bài, khác link gnews / link báo)."""
    return re.sub(r"\W+", " ", (title or "").lower()).strip()


VN_TZ = dt.timezone(dt.timedelta(hours=7))


def _date(s):
    """pubDate Google News -> ngày VN 'YYYY-MM-DD' (đồng bộ tz với _dt để không lệch ngày)."""
    try:
        return parsedate_to_datetime(s).astimezone(VN_TZ).date().isoformat()
    except (ValueError, TypeError):
        return ""


def _dt(s):
    """pubDate Google News (GMT) -> giờ đăng VN 'YYYY-MM-DDTHH:MM' (rỗng nếu không parse được)."""
    try:
        return parsedate_to_datetime(s).astimezone(VN_TZ).strftime("%Y-%m-%dT%H:%M")
    except (ValueError, TypeError):
        return ""


def _windows(start):
    """Cửa sổ 3 tháng từ start (YYYY-MM) tới nay → [(after,before)]."""
    y, m = int(start[:4]), int(start[5:7])
    today = dt.date.today()
    out = []
    cur = dt.date(y, m, 1)
    while cur < today:
        nxt = cur + dt.timedelta(days=92)
        out.append((cur.isoformat(), min(nxt, today).isoformat()))
        cur = nxt
    return out


def _pick_aliases(aliases, n=2):
    """Alias để tìm trên Google News (tìm CỤM CHÍNH XÁC trong ngoặc kép).
    Tên đầy đủ kèm ngoặc đơn kiểu 'Metro số 3 TP.HCM (An Hạ - Hiệp Bình Phước)' không báo nào viết y như vậy
    → 0 kết quả. Ưu tiên alias NGẮN, không ngoặc, ≥3 từ (đủ đặc trưng); lấy tối đa n alias."""
    clean, seen = [], set()
    for a in sorted({a.strip() for a in aliases if a and "(" not in a}, key=lambda a: (len(a.split()), len(a))):
        k = _norm(a)                       # 'A - B', 'A – B', 'A-B', 'TP.HCM'/'TP. HCM' là MỘT bí danh → chỉ hỏi 1 lần
        if k and k not in seen:
            seen.add(k)
            clean.append(a)
    good = [a for a in clean if len(a.split()) >= 3]
    pick = (good or clean)[:n]
    return pick or [re.sub(r"\s*\(.*?\)", "", max(aliases, key=len)).strip()]


def query_gnews(client, alias, after=None, before=None):
    q = f'"{alias}"'
    if after:
        q += f" after:{after} before:{before}"
    try:
        r = client.get(GNEWS.format(q=quote(q)), timeout=25, follow_redirects=True, headers=UA)
        items = ET.fromstring(r.text).findall(".//item")
    except Exception:
        return None                       # None = Google không trả lời (lỗi / bị chặn) — khác với [] (không có bài)
    out = []
    for it in items:
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "gnews").strip()
        # bỏ đuôi " - Nguồn" trong title của Google News
        title = re.sub(r"\s*-\s*" + re.escape(src) + r"\s*$", "", title).strip()
        pd = it.findtext("pubDate", "")
        out.append({"title": title, "source": "gnews:" + src,
                    "url": (it.findtext("link") or "").strip(),
                    "description": re.sub("<[^>]+>", " ", it.findtext("description") or "")[:400],
                    "date": _date(pd), "dt": _dt(pd)})
    return out


def run(a):
    reg = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)["dc_commodity"]["Infra_Projects_Registry"]
    projects = [p for p in reg.find({"active": True}, {"tid": 1, "name": 1, "aliases": 1}).sort("tid", 1)
                if p.get("tid")]
    if a.limit:
        projects = projects[:a.limit]
    windows = _windows(a.history) if a.history else [(None, None)]
    # --auto: tin GẦN ĐÂY cho mọi dự án + BACKFILL LỊCH SỬ cho dự án chưa từng quét (cờ gnews_hist),
    # tối đa --max-new dự án mỗi lượt (dự án mới thêm được ưu tiên: tid lớn trước) → không cần ai nhớ chạy tay.
    fresh, hist_windows = set(), []
    if a.auto:
        done = {d["tid"] for d in reg.find({"gnews_hist": {"$exists": True}}, {"tid": 1}) if d.get("tid")}
        fresh = set(sorted((p["tid"] for p in projects if p["tid"] not in done), reverse=True)[:a.max_new])
        hist_windows = _windows(a.auto)
        print(f"auto: {len(fresh)} dự án quét lịch sử từ {a.auto} · còn {len(projects) - len(done) - len(fresh)} dự án chờ lượt sau")
    print(f"{len(projects)} dự án × {len(windows)} cửa sổ" + (f" (history từ {a.history})" if a.history else " (gần đây)"))

    docs, by_title = {}, {}
    ncall = nfail = 0
    failed = set()                        # dự án có truy vấn KHÔNG được trả lời → chưa coi là đã quét lịch sử
    with httpx.Client(headers=UA, follow_redirects=True) as cl:
        for i, p in enumerate(projects, 1):
            for alias in _pick_aliases(p.get("aliases") or [p["name"]]):
                for after, before in (windows + hist_windows if p["tid"] in fresh else windows):
                    res = query_gnews(cl, alias, after, before)
                    ncall += 1
                    if res is None:
                        nfail += 1
                        failed.add(p["tid"])
                        res = []
                    for r in res:
                        if not r["url"] or not _relevant(alias, r["title"]):
                            continue
                        k = _tkey(r["title"])
                        d0 = by_title.get(k)          # cùng tiêu đề từ link khác → gộp, không tạo bài mới
                        if d0 is None:
                            d0 = by_title[k] = docs[r["url"]] = {**r, "projects": []}
                        if p["tid"] not in d0["projects"]:
                            d0["projects"].append(p["tid"])
                    time.sleep(0.15)
            if i % 25 == 0 or i == len(projects):
                print(f"  {i}/{len(projects)} dự án · {len(docs)} tin (unique)")

    print(f"Tổng tin gnews: {len(docs)} · {ncall} truy vấn, {nfail} không được trả lời")
    if ncall and nfail / ncall > 0.2:
        print(f"::warning::Google News không trả lời {nfail}/{ncall} truy vấn — có thể đang bị giới hạn/chặn.")
    if a.dry_run:
        for d in list(docs.values())[:15]:
            print(f"  {d['date']:10} {d['source'][:22]:22} {d['title'][:52]}")
        return
    col = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB][COLL]
    col.create_index("url", unique=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    # bài đã có trong kho (cùng link) vẫn được GẮN THÊM dự án ($addToSet) — trước đây chỉ $setOnInsert nên bỏ sót
    # Khử trùng với KHO theo tiêu đề: bài đã có (do quét trực tiếp báo, hoặc gnews lượt trước với link khác)
    # → chỉ GẮN THÊM dự án vào bài sẵn có, không tạo bản thứ hai.
    dates = [d["date"] for d in docs.values() if d.get("date")]
    have = {}
    if dates:
        for x in col.find({"date": {"$gte": min(dates)}}, {"title": 1}):
            have.setdefault(_tkey(x.get("title")), x["_id"])
    ops, ndup = [], 0
    for d in docs.values():
        _id = have.get(_tkey(d["title"]))
        if _id is not None:
            ndup += 1
            ops.append(UpdateOne({"_id": _id}, {"$addToSet": {"projects": {"$each": d["projects"]}}}))
        else:
            ops.append(UpdateOne({"url": d["url"]},
                                 {"$setOnInsert": {**{k: v for k, v in d.items() if k != "projects"}, "scraped_at": now},
                                  "$addToSet": {"projects": {"$each": d["projects"]}}}, upsert=True))
    if ops:
        res = col.bulk_write(ops, ordered=False)
        print(f"Ghi mới {res.upserted_count} tin vào {DB}.{COLL} · {ndup} bài đã có trong kho chỉ gắn thêm dự án "
              f"(tổng {col.estimated_document_count()}).")
    ok = fresh - failed                   # chỉ đánh dấu dự án mà MỌI truy vấn đều được Google trả lời
    if ok:
        reg.update_many({"tid": {"$in": list(ok)}}, {"$set": {"gnews_hist": now}})
    if fresh:
        print(f"Đánh dấu {len(ok)}/{len(fresh)} dự án đã quét lịch sử"
              + (f" · {len(fresh - ok)} dự án quét lại lượt sau" if fresh - ok else "") + ".")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", metavar="YYYY-MM", help="Backfill lịch sử từ tháng này (cửa sổ 3 tháng)")
    ap.add_argument("--auto", metavar="YYYY-MM", help="Gần đây cho mọi dự án + lịch sử (từ tháng này) cho dự án chưa từng quét")
    ap.add_argument("--max-new", type=int, default=40, help="--auto: số dự án quét lịch sử tối đa mỗi lượt")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    run(ap.parse_args())
