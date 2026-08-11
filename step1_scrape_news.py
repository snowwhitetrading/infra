# -*- coding: utf-8 -*-
"""
step1_scrape_news.py — Scrape tin 18 dự án TRỰC TIẾP từ nhiều báo (KHÔNG qua dc_news).

Lọc theo TÊN dự án (alias) → ghi dc_news.project_news_raw. step2_newsflow.py đọc collection này.

3 kiểu lấy:
  SEARCH (có lịch sử, bất kể chủ đề): cafef, vnexpress
  RSS (tin mới, có feed gộp chống sót chủ đề): vneconomy, nguoiquansat, vietnamnet,
       baodauthau, baoxaydung, baochinhphu
  VIETSTOCK: RSS + phân trang ChannelContentPage

  python step1_scrape_news.py                 # tất cả nguồn
  python step1_scrape_news.py --pages 5       # sâu hơn cho cafef/vnexpress/vietstock
  python step1_scrape_news.py --sources cafef vnexpress baodauthau
  python step1_scrape_news.py --dry-run
"""
import argparse
import asyncio
import datetime as dt
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri
from lib_projects import build_alias_regex
from lib_marks import PID2TID

DB, COLL = "dc_news", "project_news_raw"
CUTOFF = "2025-01-01"   # chỉ giữ tin từ đầu 2025 trở đi (tránh tin quá cũ)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
ALIAS = build_alias_regex()
CONTENT_NS = {"content": "http://purl.org/rss/1.0/modules/content/"}

SEARCH_KEYWORDS = [
    "sân bay Gia Bình", "đường kết nối sân bay Gia Bình", "tái định cư sân bay Gia Bình",
    "khu đô thị Gia Bình", "Trung tâm hội nghị APEC", "dự án APEC Phú Quốc",
    "mở rộng sân bay Phú Quốc", "Bãi Đất Đỏ", "Núi Ông", "tàu điện Phú Quốc",
    "Khu liên hợp thể thao Rạch Chiếc", "đường sắt Bến Thành Cần Giờ", "metro Cần Giờ",
    "đường sắt Hà Nội Quảng Ninh", "VinSpeed", "đường sắt tốc độ cao Quảng Ninh",
]

# ── RSS sources: {source: [feed urls]} ───────────────────────────────────────
RSS_FEEDS = {
    "vneconomy": ["https://vneconomy.vn/tin-moi.rss", "https://vneconomy.vn/tieu-diem.rss",
                  "https://vneconomy.vn/dau-tu.rss", "https://vneconomy.vn/dia-oc.rss",
                  "https://vneconomy.vn/dau-tu-ha-tang.rss", "https://vneconomy.vn/thi-truong.rss"],
    "nguoiquansat": ["https://nguoiquansat.vn/rss/trang-chu", "https://nguoiquansat.vn/rss/doanh-nghiep",
                     "https://nguoiquansat.vn/rss/tai-chinh-ngan-hang"],
    "vietnamnet": ["https://vietnamnet.vn/rss/kinh-doanh.rss", "https://vietnamnet.vn/rss/thoi-su.rss"],
    "baodauthau": ["https://baodauthau.vn/rss/home.rss", "https://baodauthau.vn/rss/dau-tu.rss"],
    "baoxaydung": ["https://baoxaydung.com.vn/rss/home.rss"],
    "baochinhphu": ["https://baochinhphu.vn/kinh-te.rss", "https://baochinhphu.vn/chinh-tri.rss",
                    "https://baochinhphu.vn/chinh-sach-va-cuoc-song.rss"],
}

# ── SEARCH sources ───────────────────────────────────────────────────────────
CAFEF = "https://cafef.vn"
CAFEF_ART = re.compile(r"-\d{12,}\.chn")
VNX_ART = re.compile(r"^https://vnexpress\.net/[a-z0-9-]+-\d{6,}\.html")

# ── VIETSTOCK ────────────────────────────────────────────────────────────────
VIETSTOCK = "https://vietstock.vn"
VIETSTOCK_RSS = ["/761/kinh-te/vi-mo.rss", "/143/chung-khoan/chinh-sach.rss",
                 "/4220/bat-dong-san/thi-truong-nha-dat.rss"]
VIETSTOCK_CHANNELS = {"vi_mo": 761, "chinh_sach": 143, "thi_truong_nha_dat": 4220}


def match_tids(text):
    return sorted({PID2TID[p] for p, rx in ALIAS.items() if p in PID2TID and rx.search(text)})


def rss_date(s):
    if not s:
        return ""
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (ValueError, TypeError):
        pass
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)   # baochinhphu M/d/yyyy
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return (s or "")[:10]


async def get(client, url, **kw):
    r = await client.get(url, timeout=25, follow_redirects=True, headers=UA, **kw)
    r.raise_for_status()
    return r


# ── generic RSS ──────────────────────────────────────────────────────────────

async def scrape_rss(client, source):
    out = []
    for u in RSS_FEEDS[source]:
        try:
            r = await get(client, u)
            items = ET.fromstring(r.text).findall(".//item")
        except Exception:
            continue
        for it in items:
            title = (it.findtext("title") or "").strip()
            desc = re.sub("<[^>]+>", "", it.findtext("description") or "").strip()
            body = ""
            ce = it.findtext("content:encoded", "", CONTENT_NS)
            if ce:
                body = re.sub(r"\s+", " ", BeautifulSoup(ce, "html.parser").get_text(" ", strip=True))[:2000]
            tids = match_tids(f"{title} {desc} {body}")
            if tids:
                out.append({"url": (it.findtext("link") or "").strip(), "source": source,
                            "title": title, "description": desc, "body": body[:1000],
                            "date": rss_date(it.findtext("pubDate", "")), "projects": tids})
    return out


# ── cafef search ─────────────────────────────────────────────────────────────

async def scrape_cafef(client, pages):
    urls = {}
    for kw in SEARCH_KEYWORDS:
        for p in range(1, pages + 1):
            u = (f"{CAFEF}/tim-kiem.chn?keywords={quote(kw)}" if p <= 1
                 else f"{CAFEF}/tim-kiem/trang-{p}.chn?keywords={quote(kw)}")
            try:
                r = await get(client, u)
            except Exception:
                break
            found = False
            for a in BeautifulSoup(r.text, "html.parser").select("a[href]"):
                href = a.get("href", "")
                if "/du-lieu/" in href or not CAFEF_ART.search(href):
                    continue
                url = re.sub(r"\?.*$", "", href if href.startswith("http") else CAFEF + href)
                urls.setdefault(url, (a.get("title") or a.get_text(strip=True) or "").strip())
                found = True
            if not found:
                break
    out = []
    for u, t in urls.items():
        try:
            r = await get(client, u)
        except Exception:
            continue
        s = BeautifulSoup(r.text, "html.parser")
        h1 = s.select_one("h1")
        title = h1.get_text(strip=True) if h1 else t
        de = s.select_one('meta[name="description"]')
        desc = de["content"] if de and de.get("content") else ""
        body_el = s.select_one(".detail-content")
        body = ""
        if body_el:
            for sel in ["script", "style", ".AdsByCF", ".relationnews", ".box-tinlienquan"]:
                for el in body_el.select(sel):
                    el.decompose()
            body = re.sub(r"\s+", " ", body_el.get_text(" ", strip=True))[:2000]
        pe = s.select_one('meta[property="article:published_time"]')
        pub = pe["content"][:10] if pe and pe.get("content") else ""
        tids = match_tids(f"{title} {desc} {body}")
        if tids:
            out.append({"url": u, "source": "cafef", "title": title, "description": desc,
                        "body": body[:1000], "date": pub, "projects": tids})
    return out


# ── vnexpress search (có phân trang → lịch sử) ───────────────────────────────

async def scrape_vnexpress(client, pages):
    cand = {}
    for kw in SEARCH_KEYWORDS:
        for p in range(1, pages + 1):
            base = f"https://timkiem.vnexpress.net/?q={quote(kw)}"
            try:
                r = await get(client, base if p <= 1 else base + f"&page={p}")
            except Exception:
                break
            found = False
            for a in BeautifulSoup(r.text, "html.parser").select("a[href]"):
                href = a.get("href", "")
                if not VNX_ART.match(href):
                    continue
                t = (a.get("title") or a.get_text(strip=True) or "").strip()
                if t:
                    cand.setdefault(href, t)
                    found = True
            if not found:
                break
    # khớp trên tiêu đề trước → chỉ fetch bài khớp để lấy ngày
    out = []
    for u, t in cand.items():
        if not match_tids(t):
            continue
        date, desc = "", ""
        try:
            r = await get(client, u)
            s = BeautifulSoup(r.text, "html.parser")
            pe = (s.select_one('meta[property="article:published_time"]')
                  or s.select_one('meta[name="pubdate"]')
                  or s.select_one('meta[itemprop="datePublished"]'))
            if pe and pe.get("content"):
                mm = re.search(r"\d{4}-\d{2}-\d{2}", pe["content"])
                date = mm.group(0) if mm else pe["content"][:10]
            de = s.select_one('meta[name="description"]')
            desc = de["content"] if de and de.get("content") else ""
        except Exception:
            pass
        out.append({"url": u, "source": "vnexpress", "title": t, "description": desc,
                    "body": "", "date": date, "projects": match_tids(t)})
    return out


# ── vietstock: RSS + phân trang ──────────────────────────────────────────────

def _vs(url, title, desc, date):
    tids = match_tids(f"{title} {desc}")
    return {"url": url, "source": "vietstock", "title": title, "description": desc,
            "body": "", "date": date, "projects": tids} if tids else None


async def scrape_vietstock(client, pages):
    out = []
    for path in VIETSTOCK_RSS:
        try:
            r = await get(client, VIETSTOCK + path)
            for it in ET.fromstring(r.text).findall(".//item"):
                d = _vs(it.findtext("link", "").strip().replace("http://", "https://", 1),
                        (it.findtext("title") or "").strip(),
                        re.sub("<[^>]+>", "", it.findtext("description") or "").strip(),
                        rss_date(it.findtext("pubDate", "")))
                if d:
                    out.append(d)
        except Exception:
            pass
    for ch_id in VIETSTOCK_CHANNELS.values():
        for pg in range(1, pages + 1):
            try:
                r = await client.post(f"{VIETSTOCK}/StartPage/ChannelContentPage",
                                      data={"channelID": ch_id, "page": pg}, timeout=25, headers=UA)
                cards = BeautifulSoup(r.text, "html.parser").select(".channelContent")
                if not cards:
                    break
                for card in cards:
                    a = card.select_one("h4 a.fontbold")
                    if not a:
                        continue
                    link = a.get("href", "")
                    dm = re.search(r"/(\d{4})/(\d{2})/", link)
                    de = card.select_one("p.post-p")
                    d = _vs(f"{VIETSTOCK}{link}" if link.startswith("/") else link,
                            (a.get("title") or a.get_text(strip=True)).strip(),
                            de.get_text(strip=True) if de else "",
                            f"{dm.group(1)}-{dm.group(2)}-01" if dm else "")
                    if d:
                        out.append(d)
            except Exception:
                break
    return out


ALL_SOURCES = ["cafef", "vnexpress", "vietstock"] + list(RSS_FEEDS)


async def scrape_one(client, source, pages):
    if source == "cafef":
        return await scrape_cafef(client, pages)
    if source == "vnexpress":
        return await scrape_vnexpress(client, pages)
    if source == "vietstock":
        return await scrape_vietstock(client, pages)
    if source in RSS_FEEDS:
        return await scrape_rss(client, source)
    return []


async def run(pages, dry, sources):
    now = dt.datetime.now().isoformat()
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[scrape_one(client, s, pages) for s in sources])
    docs = {}
    for lst in results:
        for d in lst:
            if not d.get("url"):
                continue
            if d.get("date") and d["date"][:10] < CUTOFF:   # bỏ tin trước 2025
                continue
            d["scraped_at"] = now
            docs.setdefault(d["url"], d)
    per = {}
    for d in docs.values():
        per[d["source"]] = per.get(d["source"], 0) + 1
    print("Khớp dự án theo nguồn:", per, "· tổng", len(docs))

    if dry:
        for d in sorted(docs.values(), key=lambda x: x["date"], reverse=True)[:30]:
            print(f"  {d['date'][:10]:10}  {d['source']:12}  {d['title'][:58]}")
        return

    coll = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB][COLL]
    coll.create_index("url", unique=True)
    coll.create_index("date")
    coll.create_index("projects")
    ops = [UpdateOne({"url": d["url"]}, {"$setOnInsert": d}, upsert=True) for d in docs.values()]
    if ops:
        res = coll.bulk_write(ops)
        print(f"Ghi mới {res.upserted_count} bài vào {DB}.{COLL} "
              f"(tổng {coll.estimated_document_count()}). Chạy step2_newsflow.py để lên web.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=2, help="Số trang cho cafef/vnexpress/vietstock")
    ap.add_argument("--sources", nargs="*", default=ALL_SOURCES,
                    help=f"Nguồn (mặc định tất cả): {', '.join(ALL_SOURCES)}")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    asyncio.run(run(a.pages, a.dry_run, [s for s in a.sources if s in ALL_SOURCES]))
