# -*- coding: utf-8 -*-
"""
step1_scrape_news.py — Scrape tin dự án TRỰC TIẾP từ nhiều báo (KHÔNG qua dc_news).

Khớp alias → tin dự án ĐÃ biết → dc_news.project_news_raw (step3_newsflow.py đọc).
KHÔNG khớp → dc_news.unmatched_raw (pool cho step2_radar soi dự án MỚI; TTL 14 ngày).
→ Quét 1 lần, dùng chung dòng tin cho cả 2 mục đích.

3 kiểu lấy:
  SEARCH (có lịch sử, bất kể chủ đề): cafef, vnexpress
  RSS (tin mới, có feed gộp chống sót chủ đề): vneconomy, nguoiquansat, vietnamnet,
       baodauthau, baoxaydung, baochinhphu
  VIETSTOCK: RSS + phân trang ChannelContentPage

  python step1_scrape_news.py                 # tất cả nguồn
  python step1_scrape_news.py --pages 5       # sâu hơn cho cafef/vnexpress/vietstock
  python step1_scrape_news.py --sources cafef vnexpress baodauthau
  python step1_scrape_news.py --dry-run
  # BACKFILL lịch sử (báo có kho lưu trữ: cafef/vnexpress/vietstock):
  python step1_scrape_news.py --sources cafef vnexpress vietstock --pages 12 --since 2020-01-01
"""
import argparse
import asyncio
import datetime as dt
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote, quote_plus

import httpx
from bs4 import BeautifulSoup
from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri
from lib_projects import build_alias_regex, pid2tid, search_keywords

DB, COLL = "dc_news", "project_news_raw"
CUTOFF = "2025-01-01"   # mặc định (chạy hằng giờ); backfill dùng --since để lùi sâu hơn
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
ALIAS = build_alias_regex()
PID2TID = pid2tid()
CONTENT_NS = {"content": "http://purl.org/rss/1.0/modules/content/"}

SEARCH_KEYWORDS = search_keywords()   # động theo registry (thêm dự án → tự search)

# GENERAL: quét thường xuyên bằng từ khoá loại-hình + sự-kiện (ít query, bắt tin mới + phát hiện dự án)
GENERAL_TERMS = [
    "cao tốc", "đường vành đai", "vành đai", "quốc lộ", "tỉnh lộ", "nút giao",
    "đường sắt tốc độ cao", "đường sắt đô thị", "metro", "tàu điện",
    "sân bay", "cảng hàng không", "cảng biển", "cảng", "cầu vượt sông", "hầm đường bộ",
    "đường ven biển", "khởi công dự án", "thông xe", "khánh thành", "động thổ",
    "phê duyệt dự án giao thông", "dự án hạ tầng giao thông",
]
INFRA_HINT = re.compile(r"cao tốc|đường sắt|metro|tàu điện|vành đai|sân bay|cảng|cầu|hầm|"
                        r"quốc lộ|tỉnh lộ|nút giao|đường ven biển|bến xe", re.IGNORECASE)

MODE = "project"                      # 'project' = search theo tên dự án; 'general' = từ khoá loại-hình
KEYWORDS = SEARCH_KEYWORDS            # run() đổi theo --mode

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
    "sggp": ["https://www.sggp.org.vn/rss/home.rss"],                     # TP.HCM: Cần Giờ, Rạch Chiếc
    "nld": ["https://nld.com.vn/rss/home.rss", "https://nld.com.vn/rss/thoi-su.rss"],
    "tinnhanhck": ["https://www.tinnhanhchungkhoan.vn/rss/home.rss"],
    "vietnambiz": ["https://vietnambiz.vn/tin-moi-nhat.rss"],
    "markettimes": ["https://markettimes.vn/rss/home.rss"],
    "congthuong": ["https://congthuong.vn/rss/thi-truong.rss", "https://congthuong.vn/rss/nang-luong.rss",
                   "https://congthuong.vn/rss/dia-phuong.rss"],       # Bộ Công Thương: điện/công nghiệp
    "nhandan": ["https://nhandan.vn/rss/kinhte-1017.rss"],            # Báo Nhân Dân
    "baogiaothong": ["https://www.baogiaothong.vn/rss/home.rss",
                     "https://www.baogiaothong.vn/rss/thoi-su-giao-thong.rss"],  # Báo Giao Thông
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


SEM = asyncio.Semaphore(10)   # trần request đồng thời (tránh nối đuôi → chạy lọt timeout)


async def get(client, url, **kw):
    async with SEM:
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
            # GIỮ cả tin không khớp (projects=[]) → run() tách sang pool cho radar
            out.append({"url": (it.findtext("link") or "").strip(), "source": source,
                        "title": title, "description": desc, "body": body[:1000],
                        "date": rss_date(it.findtext("pubDate", "")), "projects": tids})
    return out


# ── cafef search ─────────────────────────────────────────────────────────────

async def _cafef_search(client, kw, pages):
    """Trang tìm kiếm 1 keyword (phân trang tuần tự, dừng khi hết) → {url: title}."""
    found = {}
    for p in range(1, pages + 1):
        u = (f"{CAFEF}/tim-kiem.chn?keywords={quote(kw)}" if p <= 1
             else f"{CAFEF}/tim-kiem/trang-{p}.chn?keywords={quote(kw)}")
        try:
            r = await get(client, u)
        except Exception:
            break
        hit = False
        for a in BeautifulSoup(r.text, "html.parser").select("a[href]"):
            href = a.get("href", "")
            if "/du-lieu/" in href or not CAFEF_ART.search(href):
                continue
            url = re.sub(r"\?.*$", "", href if href.startswith("http") else CAFEF + href)
            found.setdefault(url, (a.get("title") or a.get_text(strip=True) or "").strip())
            hit = True
        if not hit:
            break
    return found


async def _cafef_article(client, u, t):
    try:
        r = await get(client, u)
    except Exception:
        return None
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
    if not tids:
        return None
    return {"url": u, "source": "cafef", "title": title, "description": desc,
            "body": body[:1000], "date": pub, "projects": tids}


async def _finish(client, source, cand):
    """{url:title} → docs. Bài khớp dự án (theo tiêu đề) → fetch lấy ngày/desc;
    (general) bài hạ tầng chưa khớp → title-only cho radar (unmatched_raw)."""
    docs = await asyncio.gather(*[_art_meta(client, source, u, t)
                                  for u, t in cand.items() if match_tids(t)])
    if MODE == "general":
        for u, t in cand.items():
            if not match_tids(t) and INFRA_HINT.search(t):
                docs.append({"url": u, "source": source, "title": t, "description": "",
                             "body": "", "date": "", "projects": []})
    return docs


async def scrape_cafef(client, pages):
    searches = await asyncio.gather(*[_cafef_search(client, kw, pages) for kw in KEYWORDS])
    urls = {}
    for d in searches:
        for u, t in d.items():
            urls.setdefault(u, t)
    if MODE == "general":                          # nhẹ: khớp theo tiêu đề, chỉ fetch bài khớp
        return await _finish(client, "cafef", urls)
    arts = await asyncio.gather(*[_cafef_article(client, u, t) for u, t in urls.items()])
    return [a for a in arts if a]


# ── vnexpress search (có phân trang → lịch sử) ───────────────────────────────

async def _vnx_search(client, kw, pages):
    cand = {}
    for p in range(1, pages + 1):
        base = f"https://timkiem.vnexpress.net/?q={quote(kw)}"
        try:
            r = await get(client, base if p <= 1 else base + f"&page={p}")
        except Exception:
            break
        hit = False
        for a in BeautifulSoup(r.text, "html.parser").select("a[href]"):
            href = a.get("href", "")
            if not VNX_ART.match(href):
                continue
            t = (a.get("title") or a.get_text(strip=True) or "").strip()
            if t:
                cand.setdefault(href, t)
                hit = True
        if not hit:
            break
    return cand


async def _vnx_article(client, u, t):
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
    return {"url": u, "source": "vnexpress", "title": t, "description": desc,
            "body": "", "date": date, "projects": match_tids(t)}


async def scrape_vnexpress(client, pages):
    searches = await asyncio.gather(*[_vnx_search(client, kw, pages) for kw in KEYWORDS])
    cand = {}
    for d in searches:
        for u, t in d.items():
            cand.setdefault(u, t)
    return await _finish(client, "vnexpress", cand)


# ── vietstock: RSS + phân trang ──────────────────────────────────────────────

def _vs(url, title, desc, date):
    return {"url": url, "source": "vietstock", "title": title, "description": desc,
            "body": "", "date": date, "projects": match_tids(f"{title} {desc}")}


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


# ── EPI platform search (Tuổi Trẻ, Thanh Niên): /tim-kiem.htm?keywords= ─────────
EPI_ART = re.compile(r"-\d{6,}\.htm")


async def _epi_search(client, host, kw, pages):
    cand = {}
    for p in range(1, pages + 1):
        u = f"https://{host}/tim-kiem.htm?keywords={quote(kw)}" + (f"&page={p}" if p > 1 else "")
        try:
            r = await get(client, u)
        except Exception:
            break
        before = len(cand)
        for a in BeautifulSoup(r.text, "html.parser").select("a[href]"):
            href = a.get("href", "")
            if not EPI_ART.search(href) or "tim-kiem" in href:
                continue
            t = (a.get("title") or a.get_text(strip=True) or "").strip()
            if t:
                cand.setdefault(href if href.startswith("http") else f"https://{host}{href}", t)
        if len(cand) == before:            # trang không thêm bài mới → dừng
            break
    return cand


async def _art_meta(client, source, u, t):
    """Fetch bài lấy ngày + mô tả (khớp theo tiêu đề trước để chỉ fetch bài liên quan)."""
    date, desc = "", ""
    try:
        r = await get(client, u)
        s = BeautifulSoup(r.text, "html.parser")
        pe = (s.select_one('meta[property="article:published_time"]')
              or s.select_one('meta[name="pubdate"]') or s.select_one('meta[itemprop="datePublished"]'))
        if pe and pe.get("content"):
            mm = re.search(r"\d{4}-\d{2}-\d{2}", pe["content"])
            date = mm.group(0) if mm else pe["content"][:10]
        de = s.select_one('meta[name="description"]')
        desc = de["content"] if de and de.get("content") else ""
    except Exception:
        pass
    return {"url": u, "source": source, "title": t, "description": desc,
            "body": "", "date": date, "projects": match_tids(t)}


async def scrape_epi(client, host, source, pages):
    searches = await asyncio.gather(*[_epi_search(client, host, kw, pages) for kw in KEYWORDS])
    cand = {}
    for d in searches:
        for u, t in d.items():
            cand.setdefault(u, t)
    return await _finish(client, source, cand)


# ── Dân Trí: /tim-kiem/{kw}.htm (chỉ trang 1; ngày lấy từ trang bài) ─────────────
async def _dantri_search(client, kw):
    try:
        r = await get(client, f"https://dantri.com.vn/tim-kiem/{quote_plus(kw)}.htm")
    except Exception:
        return {}
    cand = {}
    for a in BeautifulSoup(r.text, "html.parser").select("a[href]"):
        href = a.get("href", "")
        if not re.search(r"-\d{6,}\.htm", href) or "tim-kiem" in href:
            continue
        t = (a.get("title") or a.get_text(strip=True) or "").strip()
        if t:
            cand.setdefault(href if href.startswith("http") else f"https://dantri.com.vn{href}", t)
    return cand


async def scrape_dantri(client, pages):
    searches = await asyncio.gather(*[_dantri_search(client, kw) for kw in KEYWORDS])
    cand = {}
    for d in searches:
        for u, t in d.items():
            cand.setdefault(u, t)
    return await _finish(client, "dantri", cand)


# ── VietnamNet: /tim-kiem?q= (bài -\d+.html) ────────────────────────────────────
VNN_ART = re.compile(r"-\d{6,}\.html")


async def _vnn_search(client, kw, pages):
    cand = {}
    for p in range(1, pages + 1):
        u = f"https://vietnamnet.vn/tim-kiem?q={quote(kw)}" + (f"&page={p}" if p > 1 else "")
        try:
            r = await get(client, u)
        except Exception:
            break
        before = len(cand)
        for a in BeautifulSoup(r.text, "html.parser").select("a[href]"):
            href = a.get("href", "")
            if not VNN_ART.search(href) or "tim-kiem" in href:
                continue
            t = (a.get("title") or a.get_text(strip=True) or "").strip()
            if t:
                cand.setdefault(href if href.startswith("http") else f"https://vietnamnet.vn{href}", t)
        if len(cand) == before:
            break
    return cand


async def scrape_vnn(client, pages):
    searches = await asyncio.gather(*[_vnn_search(client, kw, pages) for kw in KEYWORDS])
    cand = {}
    for d in searches:
        for u, t in d.items():
            cand.setdefault(u, t)
    return await _finish(client, "vietnamnet", cand)


ALL_SOURCES = ["cafef", "vnexpress", "tuoitre", "thanhnien", "dantri", "vietstock"] + list(RSS_FEEDS)


async def scrape_one(client, source, pages):
    if source == "cafef":
        return await scrape_cafef(client, pages)
    if source == "vnexpress":
        return await scrape_vnexpress(client, pages)
    if source == "tuoitre":
        return await scrape_epi(client, "tuoitre.vn", "tuoitre", pages)
    if source == "thanhnien":
        return await scrape_epi(client, "thanhnien.vn", "thanhnien", pages)
    if source == "dantri":
        return await scrape_dantri(client, pages)
    if source == "vietstock":
        return await scrape_vietstock(client, pages)
    if source in RSS_FEEDS:
        out = await scrape_rss(client, source)          # RSS (tin mới)
        if source == "vietnamnet":                       # + search lịch sử
            out += await scrape_vnn(client, pages)
        if source == "vietnambiz":
            out += await scrape_epi(client, "vietnambiz.vn", "vietnambiz", pages)
        return out
    return []


def _save(db, lst, now, since):
    """Ghi 1 nguồn NGAY khi xong (timeout không mất phần đã quét). Trả (khớp, mới, chưa_khớp, mới)."""
    matched, unmatched = {}, {}
    for d in lst:
        u = d.get("url")
        if not u or (d.get("date") and d["date"][:10] < since):
            continue
        d["scraped_at"] = now
        (matched if d["projects"] else unmatched)[u] = d
    mn = un = 0
    if matched:
        mn = db[COLL].bulk_write(
            [UpdateOne({"url": d["url"]}, {"$setOnInsert": d}, upsert=True) for d in matched.values()]
        ).upserted_count
    if unmatched:
        ts = dt.datetime.now(dt.timezone.utc)
        un = db["unmatched_raw"].bulk_write(
            [UpdateOne({"url": d["url"]}, {"$setOnInsert": {
                "url": d["url"], "source": d["source"], "title": d["title"],
                "description": d.get("description", ""), "date": d.get("date", ""),
                "scraped_at": now, "_ts": ts}}, upsert=True) for d in unmatched.values()]
        ).upserted_count
    return len(matched), mn, len(unmatched), un


async def run(pages, dry, sources, since=CUTOFF, mode="project"):
    global MODE, KEYWORDS
    MODE = mode
    KEYWORDS = GENERAL_TERMS if mode == "general" else SEARCH_KEYWORDS
    print(f"MODE={mode} · {len(KEYWORDS)} keyword · nguồn: {', '.join(sources)}", flush=True)
    now = dt.datetime.now().isoformat()
    db = None
    if not dry:
        db = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB]
        db[COLL].create_index("url", unique=True)
        db[COLL].create_index("date")
        db[COLL].create_index("projects")
        db["unmatched_raw"].create_index("url", unique=True)
        db["unmatched_raw"].create_index("_ts", expireAfterSeconds=14 * 24 * 3600)
    tot = {"m": 0, "mn": 0, "u": 0, "un": 0}

    async def go(client, s):
        lst = await scrape_one(client, s, pages)
        if dry:
            return [d for d in lst if d.get("projects")
                    and not (d.get("date") and d["date"][:10] < since)]
        m, mn, u, un = _save(db, lst, now, since)   # ghi ngay nguồn này
        tot["m"] += m; tot["mn"] += mn; tot["u"] += u; tot["un"] += un
        print(f"  [{s:10}] khớp {m} (+{mn} mới) · chưa khớp {u} (+{un} mới)", flush=True)
        return []

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[go(client, s) for s in sources])

    if dry:
        docs = {}
        for lst in results:
            for d in lst:
                docs.setdefault(d["url"], d)
        print(f"[dry] khớp {len(docs)} bài:")
        for d in sorted(docs.values(), key=lambda x: x["date"], reverse=True)[:30]:
            print(f"  {d['date'][:10]:10}  {d['source']:12}  {d['title'][:58]}")
        return
    print(f"TỔNG: khớp {tot['m']} (+{tot['mn']} mới) · chưa khớp {tot['u']} (+{tot['un']} mới). "
          f"Chạy step3_newsflow.py để lên web.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=2, help="Số trang cho cafef/vnexpress/vietstock")
    ap.add_argument("--sources", nargs="*", default=ALL_SOURCES,
                    help=f"Nguồn (mặc định tất cả): {', '.join(ALL_SOURCES)}")
    ap.add_argument("--since", default=CUTOFF, help="Chỉ giữ tin từ ngày này (YYYY-MM-DD); backfill lùi sâu hơn")
    ap.add_argument("--mode", choices=["project", "general"], default="project",
                    help="project=search theo tên dự án (backfill) · general=từ khoá loại-hình (quét thường xuyên + radar)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    asyncio.run(run(a.pages, a.dry_run, [s for s in a.sources if s in ALL_SOURCES], a.since, a.mode))
