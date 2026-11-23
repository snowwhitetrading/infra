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
        r = client.get(GNEWS.format(q=quote(q)), timeout=12, follow_redirects=True, headers=UA)
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
    """Quét theo từng dự án, GHI DẦN mỗi --flush dự án (bị cắt giữa chừng không mất phần đã làm), dừng gọn khi hết
    --budget-min, và lượt sau chạy TIẾP từ dự án lâu chưa quét nhất (cờ gnews_at) — chịu được máy chủ chậm/bị giới hạn."""
    t0 = time.time()
    reg = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)["dc_commodity"]["Infra_Projects_Registry"]
    projects = [p for p in reg.find({"active": True}, {"tid": 1, "name": 1, "aliases": 1, "gnews_at": 1, "gnews_hist": 1})
                .sort("tid", 1) if p.get("tid")]
    if a.limit:
        projects = projects[:a.limit]
    windows = _windows(a.history) if a.history else [(None, None)]
    # --auto: tin GẦN ĐÂY cho mọi dự án + BACKFILL LỊCH SỬ cho dự án chưa từng quét (cờ gnews_hist),
    # tối đa --max-new dự án mỗi lượt (dự án mới thêm được ưu tiên: tid lớn trước) → không cần ai nhớ chạy tay.
    fresh, hist_windows = set(), []
    if a.auto:
        todo = sorted((p["tid"] for p in projects if not p.get("gnews_hist")), reverse=True)
        fresh = set(todo[:a.max_new])
        hist_windows = _windows(a.auto)
        print(f"auto: {len(fresh)} dự án quét lịch sử từ {a.auto} · còn {len(todo) - len(fresh)} dự án chờ lượt sau", flush=True)
    # thứ tự: dự án cần quét lịch sử trước (mới nhất trước), rồi dự án LÂU CHƯA QUÉT nhất → lượt bị cắt thì lượt sau làm tiếp
    projects.sort(key=lambda p: (p["tid"] not in fresh, p.get("gnews_at") or "", -p["tid"] if p["tid"] in fresh else p["tid"]))
    print(f"{len(projects)} dự án × {len(windows)} cửa sổ" + (f" (history từ {a.history})" if a.history else " (gần đây)"), flush=True)

    col = None
    have = {}                              # tiêu đề đã có trong kho → _id (khử trùng: cùng bài, khác link)
    if not a.dry_run:
        col = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB][COLL]
        col.create_index("url", unique=True)
        since = (a.auto or a.history or (dt.date.today() - dt.timedelta(days=400)).isoformat()[:7]) + "-01"
        for x in col.find({"date": {"$gte": since}}, {"title": 1, "not_projects": 1}):
            have.setdefault(_tkey(x.get("title")), (x["_id"], set(x.get("not_projects") or [])))

    docs, by_title, dirty = {}, {}, set()   # dirty = url cần ghi ở lần flush tới
    ncall = nfail = streak = nnew = ndup = nproj = 0
    scanned, failed = [], set()            # dự án đã quét trong lô hiện tại · dự án có truy vấn KHÔNG được trả lời

    def flush():
        nonlocal nnew, ndup
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        if col is not None:
            ops = []
            for u in dirty:
                d = docs[u]
                hit = have.get(_tkey(d["title"]))
                if hit is not None:          # bài đã có trong kho → chỉ gắn thêm dự án, không tạo bản thứ hai
                    _id, banned = hit        # banned = dự án đã bị soát là KHÔNG liên quan bài này (clean_news_noise.py)
                    add = [t for t in d["projects"] if t not in banned]
                    ndup += 1
                    if add:
                        ops.append(UpdateOne({"_id": _id}, {"$addToSet": {"projects": {"$each": add}}}))
                else:
                    ops.append(UpdateOne({"url": u}, {
                        "$setOnInsert": {**{k: v for k, v in d.items() if k != "projects"}, "scraped_at": now},
                        "$addToSet": {"projects": {"$each": d["projects"]}}}, upsert=True))
            if ops:
                nnew += col.bulk_write(ops, ordered=False).upserted_count
            ok = [t for t in scanned if t not in failed]     # chỉ đánh dấu dự án mà MỌI truy vấn đều được trả lời
            if ok:
                reg.update_many({"tid": {"$in": ok}}, {"$set": {"gnews_at": now}})
                okh = [t for t in ok if t in fresh]
                if okh:
                    reg.update_many({"tid": {"$in": okh}}, {"$set": {"gnews_hist": now}})
        dirty.clear()
        scanned.clear()

    stop = ""
    with httpx.Client(headers=UA, follow_redirects=True) as cl:
        for i, p in enumerate(projects, 1):
            if a.budget_min and time.time() - t0 > a.budget_min * 60:
                stop = f"hết ngân sách {a.budget_min} phút"
                break
            if streak >= 15:
                stop = "15 truy vấn liên tiếp không được trả lời (nhiều khả năng bị chặn)"
                break
            for alias in _pick_aliases(p.get("aliases") or [p["name"]]):
                for after, before in (windows + hist_windows if p["tid"] in fresh else windows):
                    res = query_gnews(cl, alias, after, before)
                    ncall += 1
                    if res is None:
                        nfail += 1
                        streak += 1
                        failed.add(p["tid"])
                        res = []
                    else:
                        streak = 0
                    for r in res:
                        if not r["url"] or not _relevant(alias, r["title"]):
                            continue
                        k = _tkey(r["title"])
                        d0 = by_title.get(k)          # cùng tiêu đề từ link khác → gộp, không tạo bài mới
                        if d0 is None:
                            d0 = by_title[k] = docs[r["url"]] = {**r, "projects": []}
                        if p["tid"] not in d0["projects"]:
                            d0["projects"].append(p["tid"])
                            dirty.add(d0["url"])
                    time.sleep(0.15)
            scanned.append(p["tid"])
            nproj += 1
            if i % a.flush == 0 or i == len(projects):
                flush()
                print(f"  {i}/{len(projects)} dự án · {len(docs)} tin · {ncall} truy vấn ({nfail} hỏng) · "
                      f"{int(time.time() - t0)}s", flush=True)
    flush()

    print(f"Tổng tin gnews: {len(docs)} · {nproj}/{len(projects)} dự án · {ncall} truy vấn, {nfail} không được trả lời · "
          f"{int(time.time() - t0)}s", flush=True)
    if stop:
        print(f"::warning::Dừng sớm: {stop}. Đã quét {nproj}/{len(projects)} dự án; lượt sau chạy tiếp phần còn lại.", flush=True)
    if ncall and nfail / ncall > 0.2:
        print(f"::warning::Google News không trả lời {nfail}/{ncall} truy vấn — có thể đang bị giới hạn/chặn.", flush=True)
    if a.dry_run:
        for d in list(docs.values())[:15]:
            print(f"  {d['date']:10} {d['source'][:22]:22} {d['title'][:52]}")
        return
    print(f"Ghi mới {nnew} tin vào {DB}.{COLL} · {ndup} lượt gắn thêm dự án vào bài đã có trong kho "
          f"(tổng {col.estimated_document_count()}).", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", metavar="YYYY-MM", help="Backfill lịch sử từ tháng này (cửa sổ 3 tháng)")
    ap.add_argument("--auto", metavar="YYYY-MM", help="Gần đây cho mọi dự án + lịch sử (từ tháng này) cho dự án chưa từng quét")
    ap.add_argument("--max-new", type=int, default=40, help="--auto: số dự án quét lịch sử tối đa mỗi lượt")
    ap.add_argument("--flush", type=int, default=20, help="ghi vào kho sau mỗi N dự án")
    ap.add_argument("--budget-min", type=float, default=0, help="dừng gọn sau N phút (0 = không giới hạn); lượt sau chạy tiếp")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    run(ap.parse_args())
