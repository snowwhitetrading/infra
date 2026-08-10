# -*- coding: utf-8 -*-
"""
scrape_project_news.py — Tìm tin về 18 dự án hạ tầng TRỰC TIẾP trên cafef.vn theo TÊN DỰ ÁN.

Bắt cả tin CHÍNH SÁCH/THỜI SỰ mà scraper-theo-mã-cổ-phiếu bỏ sót
(vd "Đẩy nhanh tiến độ các dự án phục vụ APEC 2027" — không gắn mã CK nên không được thu).

Cơ chế: dùng ô tìm kiếm cafef (server-rendered) theo bộ keyword tên dự án → lấy link bài →
fetch nội dung → chỉ giữ bài KHỚP alias dự án → upsert vào collection RIÊNG
dc_news.cafef_project_news (KHÔNG đụng collection cafef_raw_ticker_news của vnnews).
Collection này đã được thêm vào SOURCE_COLLECTIONS nên pipeline infra tự đọc.

  python scrape_project_news.py                 # 2 trang mỗi keyword, ghi DB
  python scrape_project_news.py --pages 3
  python scrape_project_news.py --dry-run       # chỉ in, không ghi

Nguồn khác (vietstock/vneconomy/nguoiquansat): search không trả kết quả qua HTTP đơn giản
(JS/Cloudflare) — nhưng đã có RSS scraper riêng đổ vào dc_news, pipeline đã đọc.
"""
import argparse
import asyncio
import datetime as dt
import re
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from pymongo import MongoClient, UpdateOne

from infra_db import mongo_uri
from update_project_news import PROJECTS, build_alias_regex

DB, COLL = "dc_news", "cafef_project_news"
BASE = "https://cafef.vn"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}

# Keyword tìm kiếm (high-signal, mỗi cái phủ ≥1 dự án). Chi tiết dự án match lại bằng alias.
SEARCH_KEYWORDS = [
    "sân bay Gia Bình", "đường kết nối sân bay Gia Bình", "tái định cư sân bay Gia Bình",
    "khu đô thị Gia Bình", "Trung tâm hội nghị APEC", "dự án APEC Phú Quốc",
    "mở rộng sân bay Phú Quốc", "Bãi Đất Đỏ", "Núi Ông", "tàu điện Phú Quốc",
    "Khu liên hợp thể thao Rạch Chiếc", "đường sắt Bến Thành Cần Giờ", "metro Cần Giờ",
    "đường sắt Hà Nội Quảng Ninh", "VinSpeed", "đường sắt tốc độ cao Quảng Ninh",
]

ART_RE = re.compile(r"-\d{12,}\.chn")   # link bài thật (id ≥12 chữ số)


def search_url(kw, page):
    if page <= 1:
        return f"{BASE}/tim-kiem.chn?keywords={quote(kw)}"
    return f"{BASE}/tim-kiem/trang-{page}.chn?keywords={quote(kw)}"


async def get(client, url):
    r = await client.get(url, timeout=25, follow_redirects=True)
    r.raise_for_status()
    return r


def parse_search(html):
    """Trả [(url, title)] các bài trong trang kết quả."""
    soup = BeautifulSoup(html, "html.parser")
    seen = {}
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "/du-lieu/" in href or not ART_RE.search(href):
            continue
        url = href if href.startswith("http") else BASE + href
        url = re.sub(r"\?.*$", "", url)
        title = (a.get("title") or a.get_text(strip=True) or "").strip()
        if url not in seen and title:
            seen[url] = title
    return list(seen.items())


def extract_article(html):
    soup = BeautifulSoup(html, "html.parser")
    def meta(sel, attr="content"):
        el = soup.select_one(sel)
        return el.get(attr, "") if el and el.get(attr) else ""
    h1 = soup.select_one("h1")
    body_el = soup.select_one(".detail-content")
    body = ""
    if body_el:
        for sel in ["script", "style", ".AdsByCF", ".relationnews",
                    ".box-tinlienquan", ".chisochungkhoan", ".tindnd", "figcaption"]:
            for el in body_el.select(sel):
                el.decompose()
        body = re.sub(r"\s+", " ", body_el.get_text(" ", strip=True))
    tickers = []
    for a in soup.select("a.link-inline-content"):
        m = re.search(r"/du-lieu/\w+/(\w+)-", a.get("href", ""))
        if m:
            tickers.append(m.group(1).upper())
    return {
        "title": h1.get_text(strip=True) if h1 else "",
        "description": meta('meta[name="description"]'),
        "published": meta('meta[property="article:published_time"]'),
        "author": meta('meta[property="article:author"]'),
        "keywords": meta('meta[name="keywords"]'),
        "body": body,
        "tickers": sorted(set(tickers)),
    }


async def run(pages, dry, keywords):
    client_db = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    coll = client_db[DB][COLL]
    alias_rx = build_alias_regex()

    urls = {}   # url -> title (từ trang search)
    async with httpx.AsyncClient(headers=UA) as client:
        # 1) Khám phá link từ tìm kiếm
        for kw in keywords:
            for p in range(1, pages + 1):
                try:
                    r = await get(client, search_url(kw, p))
                except Exception as e:
                    print(f"  [search] '{kw}' trang {p} lỗi: {e}")
                    break
                items = parse_search(r.text)
                if not items:
                    break
                for u, t in items:
                    urls.setdefault(u, t)
            print(f"  [search] '{kw}': tổng {len(urls)} url tích luỹ")
        print(f"Tìm thấy {len(urls)} url bài.")

        # 2) Bỏ url đã có trong DB
        existing = {d["url"] for d in coll.find({"url": {"$in": list(urls)}}, {"url": 1})}
        new = {u: t for u, t in urls.items() if u not in existing}
        print(f"Mới (chưa có trong DB): {len(new)}")
        if not new:
            return

        # 3) Fetch nội dung + lọc khớp dự án
        now = dt.datetime.now().isoformat()
        sem = asyncio.Semaphore(8)

        async def one(u, t):
            async with sem:
                try:
                    r = await get(client, u)
                except Exception:
                    return None
                art = extract_article(r.text)
                text = f"{art['title']} {art['description']} {art['body']} {art['keywords']}"
                matched = [pid for pid, rx in alias_rx.items() if rx.search(text)]
                if not matched:
                    return None   # kết quả tìm kiếm nhưng không thực sự về dự án nào
                pub = art["published"] or ""
                return {
                    "url": u, "source": "cafef", "feeds": ["project_search"],
                    "article_type": "editorial",
                    "title": art["title"] or t,
                    "body": art["body"], "description": art["description"],
                    "published": pub, "date": (pub[:19] if pub else now),
                    "author": art["author"], "keywords": art["keywords"],
                    "tickers": art["tickers"], "mentioned_tickers": art["tickers"],
                    "projects_matched": matched, "scraped_at": now,
                }

        results = await asyncio.gather(*[one(u, t) for u, t in new.items()])
    docs = [d for d in results if d]
    print(f"Khớp ≥1 dự án: {len(docs)} bài")

    if dry:
        for d in sorted(docs, key=lambda x: x["date"], reverse=True)[:25]:
            print(f"  {d['date'][:10]}  {','.join(d['projects_matched']):20s}  {d['title'][:66]}")
        return
    if docs:
        ops = [UpdateOne({"url": d["url"]}, {"$setOnInsert": d}, upsert=True) for d in docs]
        res = coll.bulk_write(ops)
        print(f"Ghi mới {res.upserted_count} bài vào {DB}.{COLL} (feeds=project_search). "
              f"Chạy newsflow_ingest.py + build để lên web.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=2, help="Số trang mỗi keyword (mặc định 2)")
    ap.add_argument("--keywords", nargs="*", help="Ghi đè bộ keyword tìm kiếm")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    asyncio.run(run(a.pages, a.dry_run, a.keywords or SEARCH_KEYWORDS))
