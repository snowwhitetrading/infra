# -*- coding: utf-8 -*-
"""
step1_scrape_news.py — Scrape tin 18 dự án TRỰC TIẾP từ 4 báo (KHÔNG qua dc_news).

Lý do: dc_news thu cafef theo mã cổ phiếu → tin dự án không gắn mã bị sót. Ở đây tự vào
thẳng 4 web, lọc theo TÊN dự án (alias) → ghi dc_news.project_news_raw (độc lập, đủ cả tin
chính sách/thời sự không mã). step2_newsflow.py đọc collection này.

Nguồn:
  cafef       — ô tìm kiếm theo tên dự án (server-rendered), lấy nhiều trang (có lịch sử)
  vietstock   — RSS feed + phân trang ChannelContentPage (vi mô / chính sách / BĐS)
  vneconomy   — RSS feed (dau_tu / dia_oc / dau_tu_ha_tang, có content:encoded)
  nguoiquansat— RSS feed (doanh nghiệp / tài chính)

Lọc: khớp alias trên (tiêu đề + mô tả + body nếu có). Dedup theo url.

  python step1_scrape_news.py                 # mặc định (RSS + 2 trang)
  python step1_scrape_news.py --pages 5       # sâu hơn (cafef search + vietstock phân trang)
  python step1_scrape_news.py --sources cafef vneconomy
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
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
ALIAS = build_alias_regex()

CAFEF = "https://cafef.vn"
CAFEF_ART = re.compile(r"-\d{12,}\.chn")
SEARCH_KEYWORDS = [
    "sân bay Gia Bình", "đường kết nối sân bay Gia Bình", "tái định cư sân bay Gia Bình",
    "khu đô thị Gia Bình", "Trung tâm hội nghị APEC", "dự án APEC Phú Quốc",
    "mở rộng sân bay Phú Quốc", "Bãi Đất Đỏ", "Núi Ông", "tàu điện Phú Quốc",
    "Khu liên hợp thể thao Rạch Chiếc", "đường sắt Bến Thành Cần Giờ", "metro Cần Giờ",
    "đường sắt Hà Nội Quảng Ninh", "VinSpeed", "đường sắt tốc độ cao Quảng Ninh",
]
VIETSTOCK = "https://vietstock.vn"
VIETSTOCK_RSS = ["/761/kinh-te/vi-mo.rss", "/143/chung-khoan/chinh-sach.rss",
                 "/4220/bat-dong-san/thi-truong-nha-dat.rss"]
VIETSTOCK_CHANNELS = {"vi_mo": 761, "chinh_sach": 143, "thi_truong_nha_dat": 4220}
VNECONOMY_RSS = ["https://vneconomy.vn/tin-moi.rss",          # GỘP mọi chuyên mục (chống sót do chủ đề)
                 "https://vneconomy.vn/tieu-diem.rss",
                 "https://vneconomy.vn/dau-tu.rss", "https://vneconomy.vn/dia-oc.rss",
                 "https://vneconomy.vn/dau-tu-ha-tang.rss", "https://vneconomy.vn/thi-truong.rss"]
NQS_RSS = ["https://nguoiquansat.vn/rss/trang-chu",           # GỘP trang chủ (chống sót do chủ đề)
           "https://nguoiquansat.vn/rss/doanh-nghiep", "https://nguoiquansat.vn/rss/tai-chinh-ngan-hang"]
CONTENT_NS = {"content": "http://purl.org/rss/1.0/modules/content/"}


def match_tids(text):
    """Trả list id số tracker khớp alias (rỗng nếu không dự án nào)."""
    return sorted({PID2TID[p] for p, rx in ALIAS.items() if p in PID2TID and rx.search(text)})


def rss_date(s):
    if not s:
        return ""
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (ValueError, TypeError):
        return (s or "")[:10]


async def get(client, url, **kw):
    r = await client.get(url, timeout=25, follow_redirects=True, headers=UA, **kw)
    r.raise_for_status()
    return r


# ── cafef: tìm kiếm theo tên dự án ───────────────────────────────────────────

def cafef_search_url(kw, page):
    if page <= 1:
        return f"{CAFEF}/tim-kiem.chn?keywords={quote(kw)}"
    return f"{CAFEF}/tim-kiem/trang-{page}.chn?keywords={quote(kw)}"


async def scrape_cafef(client, pages):
    # 1) gom url bài từ trang kết quả
    urls = {}
    for kw in SEARCH_KEYWORDS:
        for p in range(1, pages + 1):
            try:
                r = await get(client, cafef_search_url(kw, p))
            except Exception:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            found = False
            for a in soup.select("a[href]"):
                href = a.get("href", "")
                if "/du-lieu/" in href or not CAFEF_ART.search(href):
                    continue
                u = re.sub(r"\?.*$", "", href if href.startswith("http") else CAFEF + href)
                urls.setdefault(u, (a.get("title") or a.get_text(strip=True) or "").strip())
                found = True
            if not found:
                break
    # 2) fetch từng bài, khớp alias trên title+desc+body
    out = []
    for u, t in urls.items():
        try:
            r = await get(client, u)
        except Exception:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        h1 = soup.select_one("h1")
        title = h1.get_text(strip=True) if h1 else t
        desc_el = soup.select_one('meta[name="description"]')
        desc = desc_el["content"] if desc_el and desc_el.get("content") else ""
        body_el = soup.select_one(".detail-content")
        body = ""
        if body_el:
            for sel in ["script", "style", ".AdsByCF", ".relationnews", ".box-tinlienquan"]:
                for el in body_el.select(sel):
                    el.decompose()
            body = re.sub(r"\s+", " ", body_el.get_text(" ", strip=True))[:4000]
        pub_el = soup.select_one('meta[property="article:published_time"]')
        pub = pub_el["content"] if pub_el and pub_el.get("content") else ""
        tids = match_tids(f"{title} {desc} {body}")
        if tids:
            out.append({"url": u, "source": "cafef", "title": title, "description": desc,
                        "body": body, "date": (pub[:10] or ""), "projects": tids})
    return out


# ── vietstock: RSS + phân trang ChannelContentPage ───────────────────────────

def _vs_item(url, title, desc, date):
    tids = match_tids(f"{title} {desc}")
    return {"url": url, "source": "vietstock", "title": title, "description": desc,
            "body": "", "date": date, "projects": tids} if tids else None


async def scrape_vietstock(client, pages):
    out = []
    # RSS (gần đây)
    for path in VIETSTOCK_RSS:
        try:
            r = await get(client, VIETSTOCK + path)
            for it in ET.fromstring(r.text).findall(".//item"):
                d = _vs_item(it.findtext("link", "").strip().replace("http://", "https://", 1),
                             (it.findtext("title") or "").strip(),
                             re.sub("<[^>]+>", "", it.findtext("description") or "").strip(),
                             rss_date(it.findtext("pubDate", "")))
                if d:
                    out.append(d)
        except Exception:
            pass
    # phân trang (sâu hơn) — POST ChannelContentPage
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
                    url = f"{VIETSTOCK}{link}" if link.startswith("/") else link
                    dm = re.search(r"/(\d{4})/(\d{2})/", link)
                    desc_el = card.select_one("p.post-p")
                    d = _vs_item(url, (a.get("title") or a.get_text(strip=True)).strip(),
                                 desc_el.get_text(strip=True) if desc_el else "",
                                 f"{dm.group(1)}-{dm.group(2)}-01" if dm else "")
                    if d:
                        out.append(d)
            except Exception:
                break
    return out


# ── vneconomy: RSS (có content:encoded) ──────────────────────────────────────

async def scrape_vneconomy(client, pages):
    out = []
    for url in VNECONOMY_RSS:
        try:
            r = await get(client, url)
        except Exception:
            continue
        for it in ET.fromstring(r.text).findall(".//item"):
            title = (it.findtext("title") or "").strip()
            desc = re.sub("<[^>]+>", "", it.findtext("description") or "").strip()
            body = ""
            ce = it.findtext("content:encoded", "", CONTENT_NS)
            if ce:
                body = re.sub(r"\s+", " ", BeautifulSoup(ce, "html.parser").get_text(" ", strip=True))[:4000]
            tids = match_tids(f"{title} {desc} {body}")
            if tids:
                out.append({"url": (it.findtext("link") or "").strip(), "source": "vneconomy",
                            "title": title, "description": desc, "body": body,
                            "date": rss_date(it.findtext("pubDate", "")), "projects": tids})
    return out


# ── nguoiquansat: RSS ────────────────────────────────────────────────────────

async def scrape_nguoiquansat(client, pages):
    out = []
    for url in NQS_RSS:
        try:
            r = await get(client, url)
        except Exception:
            continue
        for it in ET.fromstring(r.text).findall(".//item"):
            title = (it.findtext("title") or "").strip()
            desc = re.sub("<[^>]+>", "", it.findtext("description") or "").strip()
            tids = match_tids(f"{title} {desc}")
            if tids:
                out.append({"url": (it.findtext("link") or "").strip(), "source": "nguoiquansat",
                            "title": title, "description": desc, "body": "",
                            "date": rss_date(it.findtext("pubDate", "")), "projects": tids})
    return out


SCRAPERS = {"cafef": scrape_cafef, "vietstock": scrape_vietstock,
            "vneconomy": scrape_vneconomy, "nguoiquansat": scrape_nguoiquansat}


async def run(pages, dry, sources):
    now = dt.datetime.now().isoformat()
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[SCRAPERS[s](client, pages) for s in sources])
    docs = {}
    for lst in results:
        for d in lst:
            if d["url"]:
                d["scraped_at"] = now
                docs.setdefault(d["url"], d)   # dedup theo url
    per = {}
    for d in docs.values():
        per[d["source"]] = per.get(d["source"], 0) + 1
    print("Khớp dự án theo nguồn:", per, "· tổng", len(docs))

    if dry:
        for d in sorted(docs.values(), key=lambda x: x["date"], reverse=True)[:30]:
            print(f"  {d['date'][:10]:10}  {d['source']:12}  {d['title'][:60]}")
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
    ap.add_argument("--pages", type=int, default=2, help="Số trang cafef-search / vietstock-phân-trang")
    ap.add_argument("--sources", nargs="*", default=list(SCRAPERS),
                    help=f"Nguồn (mặc định tất cả): {', '.join(SCRAPERS)}")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    asyncio.run(run(a.pages, a.dry_run, [s for s in a.sources if s in SCRAPERS]))
