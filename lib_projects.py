#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lib_projects.py
======================

Cập nhật tin tức cho danh mục dự án do Vingroup / Sungroup / BIM đề xuất
(theo công văn NHNN 53xx/HNN-TD, file 3214.pdf), tạo bản digest theo ĐÚNG
format của hai collection có sẵn:

    dc_realestate.RE_News_Collection_Weekly_Digest   (weekly)
    dc_realestate.RE_News_Collection_Daily_Snapshot  (daily)

Nguồn dữ liệu thô (dc_news):
    cafef_raw_ticker_news, vneconomy_raw_news,
    nguoiquansat_raw_news, vietstock_raw_news

Kết quả được ghi vào collection RIÊNG (không lẫn với digest thị trường BĐS):
    dc_realestate.RE_Project_News_Weekly_Digest
    dc_realestate.RE_Project_News_Daily_Snapshot

Phần narrative (summary / evidence_narrative / topic narratives ...) được
sinh bằng Anthropic Messages API — giống các document gốc. Cần đặt biến môi
trường ANTHROPIC_API_KEY.

Cách dùng
---------
    # tạo cả weekly + daily cho ngày hôm nay
    python lib_projects.py --mode both

    # chỉ daily cho một ngày cụ thể
    python lib_projects.py --mode daily --date 2026-07-30

    # weekly cho tuần chứa ngày chỉ định
    python lib_projects.py --mode weekly --date 2026-07-25

    # xem trước, không ghi DB (in JSON ra stdout / file)
    python lib_projects.py --mode both --dry-run

    # chỉ ghép tin, KHÔNG gọi LLM (điền narrative theo quy tắc)
    python lib_projects.py --mode both --no-llm
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request
import urllib.error
from collections import Counter, defaultdict

try:
    from pymongo import MongoClient, UpdateOne
except ImportError:
    sys.exit("Thiếu thư viện pymongo. Cài: pip install pymongo dnspython")

# --------------------------------------------------------------------------- #
# Cấu hình
# --------------------------------------------------------------------------- #

from lib_db import mongo_uri
MONGO_URI = mongo_uri()

NEWS_DB = "dc_news"          # nguồn tin thô
RE_DB = "dc_realestate"      # nơi chứa format mẫu RE_News_Collection_* (chỉ tham chiếu)
# Database đích. Mặc định dc_commodity (user danvu đã có quyền readWrite).
# Lưu ý: tài khoản danvu KHÔNG tạo được database mới (vd dc_infra) — cần admin
# cấp quyền readWrite trước, sau đó đổi bằng: INFRA_OUT_DB=dc_infra
OUT_DB = os.environ.get("INFRA_OUT_DB", "dc_commodity")

# collection nguồn -> tên field chứa ngày đăng (ưu tiên), có fallback bên dưới
SOURCE_COLLECTIONS = {
    "cafef_raw_ticker_news": "date",
    "cafef_project_news": "date",      # step1_scrape_news.py (tìm theo tên dự án)
    "vneconomy_raw_news": "pub_date",
    "nguoiquansat_raw_news": "pub_date",
    "vietstock_raw_news": "pub_date",
}
DATE_FALLBACKS = ["pub_date", "published", "date", "modified", "scraped_at"]

# collection đích cho INFRA — đặt song song với bộ RE_News_Collection_*
OUT_WEEKLY = "Infra_News_Collection_Weekly_Digest"
OUT_DAILY = "Infra_News_Collection_Daily_Snapshot"

SEGMENT = "vsb_projects"  # Vingroup - Sungroup - BIM proposed projects

DEFAULT_MODEL = os.environ.get("PROJECT_NEWS_MODEL", "claude-sonnet-4-6")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

MAX_ARTICLES_IN_PROMPT = 140  # giới hạn để kiểm soát token
BODY_SNIPPET_CHARS = 700

# --------------------------------------------------------------------------- #
# Danh mục dự án (trích từ file PDF 3214 — Phụ lục I & II)
# Mỗi dự án: id, name, nhóm, chủ đầu tư đề xuất, địa phương, và danh sách
# 'aliases' (cụm từ dùng để dò tin trong title/body/description/tags).
# --------------------------------------------------------------------------- #

PROJECTS = [
    # --- Nhóm I: các dự án phục vụ hội nghị APEC (Sun Group - Phú Quốc) ---
    {
        "id": "apec_center",
        "name": "Trung tâm tổ chức hội nghị APEC",
        "group": "APEC/Phú Quốc",
        "developer": "Sun Group",
        "location": "Phú Quốc",
        "aliases": ["Trung tâm tổ chức hội nghị APEC", "trung tâm hội nghị APEC",
                    "hội nghị APEC", "APEC 2027"],
    },
    {
        "id": "phu_quoc_airport",
        "name": "Đầu tư mở rộng Cảng hàng không quốc tế Phú Quốc",
        "group": "APEC/Phú Quốc",
        "developer": "Sun Group",
        "location": "Phú Quốc",
        "aliases": ["Cảng hàng không quốc tế Phú Quốc", "sân bay Phú Quốc",
                    "sân bay quốc tế Phú Quốc", "mở rộng sân bay Phú Quốc",
                    "CHKQT Phú Quốc"],
    },
    {
        "id": "bai_dat_do",
        "name": "Khu đô thị hỗn hợp - Bãi Đất Đỏ",
        "group": "APEC/Phú Quốc",
        "developer": "Sun Group",
        "location": "Phú Quốc",
        "aliases": ["Bãi Đất Đỏ"],
    },
    {
        "id": "nui_ong",
        "name": "Khu đô thị hỗn hợp du lịch sinh thái Núi Ông Quắn",
        "group": "APEC/Phú Quốc",
        "developer": "Sun Group",
        "location": "Phú Quốc",
        "aliases": ["Núi Ông Quắn", "Núi Ông", "du lịch sinh thái Núi Ông"],
    },
    {
        "id": "phu_quoc_tram",
        "name": "Tuyến tàu điện đô thị (Phú Quốc) - đoạn 1",
        "group": "APEC/Phú Quốc",
        "developer": "Sun Group",
        "location": "Phú Quốc",
        "aliases": ["tàu điện đô thị Phú Quốc", "tuyến tàu điện Phú Quốc",
                    "tàu điện Phú Quốc", "metro Phú Quốc"],
    },
    # --- Nhóm II: dự án PPP ---
    {
        "id": "rach_chiec",
        "name": "Khu Liên hợp Thể dục thể thao Quốc gia Rạch Chiếc",
        "group": "PPP",
        "developer": "Vingroup",
        "location": "TP.HCM",
        "aliases": ["Rạch Chiếc", "Liên hợp Thể dục thể thao Quốc gia Rạch Chiếc",
                    "khu thể thao Rạch Chiếc"],
    },
    # --- Nhóm III: đường kết nối sân bay Gia Bình & đường sắt tốc độ cao ---
    {
        "id": "road_giabinh_hanoi",
        "name": "Tuyến đường nối sân bay Gia Bình - Hà Nội (đoạn qua TP Hà Nội)",
        "group": "Đường kết nối / Đường sắt",
        "developer": "Vingroup",
        "location": "Hà Nội",
        "aliases": ["đường nối sân bay Gia Bình", "tuyến đường Gia Bình",
                    "kết nối sân bay Gia Bình", "đường Gia Bình - Hà Nội"],
    },
    {
        "id": "road_giabinh_bacninh",
        "name": "Tuyến đường kết nối sân bay Gia Bình - Hà Nội (đoạn qua Bắc Ninh, Km9+000-Km27+766)",
        "group": "Đường kết nối / Đường sắt",
        "developer": "Vingroup",
        "location": "Bắc Ninh",
        "aliases": ["đường kết nối sân bay Gia Bình", "Gia Bình Bắc Ninh",
                    "Km9+000", "Km27+766"],
    },
    {
        "id": "rail_benthanh_cangio",
        "name": "Đường sắt Bến Thành - Cần Giờ",
        "group": "Đường kết nối / Đường sắt",
        "developer": "Vingroup",
        "location": "TP.HCM",
        "aliases": ["Bến Thành - Cần Giờ", "Bến Thành – Cần Giờ",
                    "đường sắt Cần Giờ", "metro Cần Giờ", "tuyến Bến Thành Cần Giờ"],
    },
    {
        "id": "rail_hanoi_quangninh",
        "name": "Đường sắt Hà Nội - Quảng Ninh",
        "group": "Đường kết nối / Đường sắt",
        "developer": "Vingroup",
        "location": "Hà Nội - Quảng Ninh",
        "aliases": ["Hà Nội - Quảng Ninh", "Hà Nội – Quảng Ninh",
                    "đường sắt Hà Nội Quảng Ninh", "đường sắt Quảng Ninh"],
    },
    # --- Nhóm IV: Cảng HKQT Gia Bình & các dự án BT ---
    {
        "id": "giabinh_airport",
        "name": "Cảng hàng không quốc tế Gia Bình",
        "group": "Sân bay Gia Bình",
        "developer": "Vingroup",
        "location": "Bắc Ninh",
        "aliases": ["Cảng hàng không quốc tế Gia Bình", "sân bay Gia Bình",
                    "CHKQT Gia Bình", "sân bay quốc tế Gia Bình"],
    },
    {
        "id": "atc_tower",
        "name": "Đài kiểm soát không lưu ATC (Gia Bình)",
        "group": "Sân bay Gia Bình",
        "developer": "Vingroup",
        "location": "Bắc Ninh",
        "aliases": ["Đài kiểm soát không lưu", "đài kiểm soát không lưu Gia Bình",
                    "ATC Gia Bình"],
    },
    {
        "id": "bt1",
        "name": "BT1 - Đường kết nối sân bay Gia Bình",
        "group": "Sân bay Gia Bình (BT)",
        "developer": "Vingroup",
        "location": "Bắc Ninh",
        "aliases": ["BT1 Gia Bình", "đường kết nối sân bay Gia Bình"],
    },
    {
        "id": "bt2",
        "name": "BT2 - Khu tái định cư (Gia Bình)",
        "group": "Sân bay Gia Bình (BT)",
        "developer": "Vingroup",
        "location": "Bắc Ninh",
        "aliases": ["BT2 Gia Bình", "tái định cư Gia Bình", "khu tái định cư Gia Bình"],
    },
    {
        "id": "bt3",
        "name": "BT3 - Công trình hoàn trả hạ tầng (Gia Bình)",
        "group": "Sân bay Gia Bình (BT)",
        "developer": "Vingroup",
        "location": "Bắc Ninh",
        "aliases": ["BT3 Gia Bình", "hoàn trả hạ tầng Gia Bình",
                    "công trình hoàn trả Gia Bình"],
    },
    {
        "id": "bt1_doiung",
        "name": "Dự án đối ứng BT1 (Gia Bình)",
        "group": "Sân bay Gia Bình (BT)",
        "developer": "Vingroup",
        "location": "Bắc Ninh",
        "aliases": ["đối ứng BT1", "dự án đối ứng BT1 Gia Bình"],
    },
    {
        "id": "bt2_doiung",
        "name": "Dự án đối ứng BT2 (Gia Bình)",
        "group": "Sân bay Gia Bình (BT)",
        "developer": "Vingroup",
        "location": "Bắc Ninh",
        "aliases": ["đối ứng BT2", "dự án đối ứng BT2 Gia Bình"],
    },
    {
        "id": "bt3_doiung",
        "name": "Dự án đối ứng BT3 (Gia Bình)",
        "group": "Sân bay Gia Bình (BT)",
        "developer": "Vingroup",
        "location": "Bắc Ninh",
        "aliases": ["đối ứng BT3", "dự án đối ứng BT3 Gia Bình"],
    },
]

# Bối cảnh chung dùng để hỗ trợ dò tin cấp cao (không phải điều kiện bắt buộc)
CONTEXT_TERMS = ["Gia Bình", "Phú Quốc", "Cần Giờ", "Rạch Chiếc", "Vingroup",
                 "Sun Group", "Sungroup", "BIM", "APEC"]


# --------------------------------------------------------------------------- #
# Tiện ích ngày tháng
# --------------------------------------------------------------------------- #

def parse_dt(value):
    """Cố gắng parse nhiều định dạng ngày khác nhau -> datetime naive (UTC)."""
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("&#x2B;", "+")
    # ISO 8601 (có/không timezone)
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo:
            d = d.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return d
    except ValueError:
        pass
    # Định dạng kiểu Mỹ: 7/30/2026 3:34:01 PM
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def doc_date(doc, primary_field):
    for field in [primary_field] + DATE_FALLBACKS:
        d = parse_dt(doc.get(field))
        if d:
            return d
    return None


# --------------------------------------------------------------------------- #
# Dò & so khớp tin với dự án
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Registry động: đọc danh mục dự án từ DB (thêm dự án = thêm doc, không sửa code)
# Fallback về PROJECTS hardcode nếu registry rỗng/không kết nối được.
# --------------------------------------------------------------------------- #
REGISTRY_DB, REGISTRY_COLL = "dc_commodity", "Infra_Projects_Registry"
_REG_CACHE = None
# tid dự phòng cho 18 dự án gốc (khi registry chưa có)
_FALLBACK_TID = {
    "apec_center": 1, "phu_quoc_airport": 2, "bai_dat_do": 3, "nui_ong": 4,
    "phu_quoc_tram": 5, "rach_chiec": 6, "road_giabinh_hanoi": 7,
    "road_giabinh_bacninh": 8, "rail_benthanh_cangio": 9, "rail_hanoi_quangninh": 10,
    "giabinh_airport": 11, "atc_tower": 12, "bt1": 13, "bt2": 14, "bt3": 15,
    "bt1_doiung": 16, "bt2_doiung": 17, "bt3_doiung": 18,
}


def load_registry(force=False):
    """Danh mục dự án đang theo dõi: [{id, tid, name, group, aliases}] từ DB (cache)."""
    global _REG_CACHE
    if _REG_CACHE is not None and not force:
        return _REG_CACHE
    try:
        from pymongo import MongoClient
        c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
        docs = list(c[REGISTRY_DB][REGISTRY_COLL].find({"active": True}))
        if docs:
            _REG_CACHE = [{"id": d["id"], "tid": d.get("tid"), "name": d.get("name", ""),
                           "group": d.get("group", ""), "aliases": d.get("aliases", [])}
                          for d in docs if d.get("aliases")]
            return _REG_CACHE
    except Exception:
        pass
    _REG_CACHE = [{"id": p["id"], "tid": _FALLBACK_TID.get(p["id"]), "name": p["name"],
                   "group": p.get("group", ""), "aliases": p["aliases"]} for p in PROJECTS]
    return _REG_CACHE


def pid2tid():
    """map project_id -> tid (id số) từ registry."""
    return {p["id"]: p["tid"] for p in load_registry() if p.get("tid")}


def project_names_by_tid():
    """map tid -> tên dự án (cho hiển thị newsflow, gồm cả dự án mới ngoài Gantt)."""
    return {p["tid"]: p["name"] for p in load_registry() if p.get("tid")}


def build_alias_regex():
    """Trả về {project_id: compiled_regex} dò bất kỳ alias nào của dự án (từ registry)."""
    out = {}
    for p in load_registry():
        parts = sorted({re.escape(a) for a in p["aliases"]}, key=len, reverse=True)
        if parts:
            out[p["id"]] = re.compile("|".join(parts), re.IGNORECASE)
    return out


def article_text(doc):
    return " \n".join(str(doc.get(f, "") or "") for f in
                      ("title", "description", "sapo", "body")) + " " + \
        " ".join(str(t) for t in (doc.get("tags") or []))


def fetch_matched_articles(client, start, end):
    """
    Lấy các bài trong [start, end) khớp ít nhất 1 dự án.
    Trả về (articles, by_project_counts).
    articles: list dict đã chuẩn hoá, gán article_id A001...
    """
    alias_rx = build_alias_regex()
    # Bộ lọc thô ở tầng Mongo: chứa ít nhất 1 context term -> giảm dữ liệu kéo về
    context_rx = re.compile("|".join(re.escape(t) for t in CONTEXT_TERMS), re.IGNORECASE)

    seen_urls = set()
    collected = []
    for coll_name, date_field in SOURCE_COLLECTIONS.items():
        coll = client[NEWS_DB][coll_name]
        query = {"$or": [{"title": context_rx}, {"body": context_rx},
                         {"description": context_rx}, {"tags": context_rx}]}
        for doc in coll.find(query):
            d = doc_date(doc, date_field)
            if d is None or not (start <= d < end):
                continue
            txt = article_text(doc)
            matched = [pid for pid, rx in alias_rx.items() if rx.search(txt)]
            if not matched:
                continue
            url = doc.get("url") or str(doc.get("_id"))
            if url in seen_urls:
                continue
            seen_urls.add(url)
            collected.append({
                "url": url,
                "title": (doc.get("title") or doc.get("headline") or "").strip(),
                "source": doc.get("source") or coll_name.split("_")[0],
                "published_at": d.isoformat(),
                "published_sort": d,
                "body": (doc.get("body") or doc.get("description") or
                         doc.get("sapo") or "").strip(),
                "tickers": doc.get("tickers") or doc.get("mentioned_tickers") or [],
                "projects": matched,
            })

    # Mới nhất trước
    collected.sort(key=lambda a: a["published_sort"], reverse=True)

    by_project = Counter()
    for a in collected:
        for pid in a["projects"]:
            by_project[pid] += 1

    # Gán article_id ổn định theo thứ tự
    for i, a in enumerate(collected, 1):
        a["article_id"] = f"A{i:03d}"
        a.pop("published_sort", None)

    return collected, by_project


def render_articles_for_prompt(articles):
    """Chuỗi gọn để đưa vào prompt LLM."""
    id2project = {p["id"]: p["name"] for p in PROJECTS}
    lines = []
    for a in articles[:MAX_ARTICLES_IN_PROMPT]:
        projs = ", ".join(id2project.get(p, p) for p in a["projects"])
        body = re.sub(r"\s+", " ", a["body"])[:BODY_SNIPPET_CHARS]
        lines.append(
            f"[{a['article_id']}] ({a['source']}, {a['published_at']}) "
            f"DỰ ÁN: {projs}\n"
            f"  Tiêu đề: {a['title']}\n"
            f"  Nội dung: {body}\n"
            f"  URL: {a['url']}"
        )
    return "\n\n".join(lines)


def citation_index(articles):
    return {a["article_id"]: {
        "article_id": a["article_id"], "source": a["source"],
        "published_at": a["published_at"], "url": a["url"], "title": a["title"],
    } for a in articles}


# --------------------------------------------------------------------------- #
# Gọi Anthropic Messages API (qua urllib, không cần SDK)
# --------------------------------------------------------------------------- #

def call_anthropic(system, user, model, max_tokens=8000):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Chưa có ANTHROPIC_API_KEY. Đặt biến môi trường hoặc chạy --no-llm.")
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Anthropic API lỗi {e.code}: {e.read().decode('utf-8')[:500]}")
    text = "".join(b.get("text", "") for b in data.get("content", []))
    return text


def extract_json(text):
    """Bóc khối JSON đầu tiên từ output model (kể cả khi có rào ```)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Không tìm thấy JSON trong output model.")
    return json.loads(text[start:end + 1])


# --------------------------------------------------------------------------- #
# Sinh WEEKLY digest
# --------------------------------------------------------------------------- #

WEEKLY_SYSTEM = """Bạn là chuyên gia phân tích bất động sản & hạ tầng Việt Nam.
Nhiệm vụ: tổng hợp tin tức trong tuần về DANH MỤC DỰ ÁN do Vingroup / Sun Group /
BIM đề xuất (sân bay Gia Bình, APEC Phú Quốc, đường sắt Bến Thành-Cần Giờ,
Hà Nội-Quảng Ninh, khu thể thao Rạch Chiếc, các dự án BT...) thành một bản digest.

Trả về DUY NHẤT một JSON hợp lệ (không thêm chữ nào ngoài JSON) với schema:
{
  "summary": "đoạn tổng quan tuần (tiếng Việt, 4-8 câu)",
  "sentiment": "positive|negative|neutral|mixed",
  "tone_shift": "more_positive|more_negative|unchanged",
  "project_status": [
    {
      "project_id": "<mã dự án trong danh mục được cấp>",
      "stage": "giai đoạn hiện tại — một trong: đề xuất | chủ trương đầu tư | quy hoạch/phê duyệt | giải phóng mặt bằng | đấu thầu | khởi công | thi công | hoàn thành",
      "progress": "advancing (tiến lên so kỳ trước) | steady (không đổi) | stalled (chững) | new (mới)",
      "latest_update": "diễn biến mới nhất của dự án trong kỳ (1-2 câu)",
      "vs_previous": "so với kỳ trước ra sao (1 câu); nếu không có baseline ghi 'chưa có dữ liệu kỳ trước'",
      "events": [{"date": "YYYY-MM-DD", "stage": "...", "summary": "...", "article_id": "A0xx"}]
    }
  ]
}
Quy tắc:
- 'summary' nêu rõ tiến triển của từng dự án khi có (tên dự án cụ thể).
- 'project_status': mỗi dự án có tin trong kỳ tạo 1 mục; 'project_id' chỉ dùng mã có
  trong danh mục; 'events.article_id' chỉ dùng mã bài được cấp; sắp dự án tiến triển rõ lên trước.
- Tuyệt đối không bịa số liệu ngoài dữ liệu.
"""

# Các array citation của weekly (nếu có). Đã bỏ theo yêu cầu: policy_updates,
# government_intention, financing_banking, price_signals.
WEEKLY_CITATION_FIELDS = []


def build_weekly(articles, by_project, period, prev_summary, prev_status=None):
    baseline = ("\n\nTÓM TẮT TUẦN TRƯỚC (để xác định tone_shift):\n" + prev_summary) \
        if prev_summary else "\n\n(Không có baseline tuần trước.)"
    if prev_status:
        baseline += ("\n\nTRẠNG THÁI DỰ ÁN TUẦN TRƯỚC (để so 'vs_previous'/'progress'):\n"
                     + json.dumps(prev_status, ensure_ascii=False))
    user = (
        f"Khoảng thời gian: {period}. Số bài: {len(articles)}.\n"
        f"Dữ liệu bài báo:\n\n{render_articles_for_prompt(articles)}"
        f"{baseline}\n\nHãy tạo JSON digest theo schema."
    )
    text = call_anthropic(WEEKLY_SYSTEM, user, CURRENT_MODEL)
    obj = extract_json(text)
    obj = expand_citations(obj, articles, WEEKLY_CITATION_FIELDS)
    return enrich_project_status(obj, articles)


def expand_citations(obj, articles, list_fields):
    """Chuyển citations dạng ['A001'] thành object citation đầy đủ."""
    cidx = citation_index(articles)
    for field in list_fields:
        for item in obj.get(field, []) or []:
            cits = item.get("citations", []) or []
            item["citations"] = [cidx[c] for c in cits if c in cidx]
    return obj


def enrich_project_status(obj, articles):
    """Bồi thông tin cho mảng project_status: gắn tên/CĐT/địa phương từ danh mục,
    đếm số bài, và bung article_id trong events thành nguồn/url/tiêu đề."""
    id2p = {p["id"]: p for p in PROJECTS}
    art_by_id = {a["article_id"]: a for a in articles}
    cnt = Counter()
    for a in articles:
        for pid in a["projects"]:
            cnt[pid] += 1
    for item in obj.get("project_status", []) or []:
        p = id2p.get(item.get("project_id"))
        if p:
            item["project_name"] = p["name"]
            item["developer"] = p["developer"]
            item["location"] = p["location"]
        item["article_count"] = cnt.get(item.get("project_id"), 0)
        for ev in item.get("events", []) or []:
            a = art_by_id.get(ev.get("article_id"))
            if a:
                ev.setdefault("source", a["source"])
                ev.setdefault("url", a["url"])
                ev.setdefault("published_at", a["published_at"])
                ev.setdefault("title", a["title"])
    return obj


def slim_prev_status(prev_doc):
    """Rút gọn project_status kỳ trước để cấp làm baseline cho 'vs_previous'/'progress'."""
    if not prev_doc:
        return None
    return [{"project_id": x.get("project_id"), "stage": x.get("stage"),
             "latest_update": x.get("latest_update")}
            for x in (prev_doc.get("project_status") or [])]


# --------------------------------------------------------------------------- #
# Sinh DAILY snapshot
# --------------------------------------------------------------------------- #

DAILY_TOPICS = ["developer_moves", "financing_credit", "locations",
                "policy", "price_transactions", "project_launches"]

DAILY_SYSTEM = """Bạn là chuyên gia phân tích bất động sản & hạ tầng Việt Nam.
Nhiệm vụ: tạo BẢN CHỤP TIN TỨC TRONG NGÀY về danh mục dự án Vingroup / Sun Group /
BIM (sân bay Gia Bình, APEC Phú Quốc, đường sắt Bến Thành-Cần Giờ, Hà Nội-Quảng Ninh,
khu thể thao Rạch Chiếc, các dự án BT...).

Trả về DUY NHẤT một JSON hợp lệ theo schema (mọi text tiếng Việt):
{
  "headline": "một câu tiêu đề nêu trạng thái dòng tin trong ngày",
  "summary_narrative": "đoạn tường thuật 4-8 câu",
  "sentiment": "positive|negative|neutral|mixed",
  "tone_shift_vs_yesterday": "more_positive|more_negative|unchanged",
  "risk_events": [{"text": "...", "article_ids": ["A001"]}],
  "topics": {
    "<topic>": {"sentiment": "...", "narrative": "...",
                "items": [{"text": "...", "article_ids": ["A001"]}]}
  },
  "project_status": [
    {
      "project_id": "<mã dự án trong danh mục được cấp>",
      "stage": "đề xuất | chủ trương đầu tư | quy hoạch/phê duyệt | giải phóng mặt bằng | đấu thầu | khởi công | thi công | hoàn thành",
      "progress": "advancing | steady | stalled | new",
      "latest_update": "diễn biến mới nhất trong ngày (1-2 câu)",
      "vs_previous": "so với hôm trước ra sao (1 câu); nếu không có baseline ghi 'chưa có dữ liệu kỳ trước'",
      "events": [{"date": "YYYY-MM-DD", "stage": "...", "summary": "...", "article_id": "A0xx"}]
    }
  ]
}
Các topic hợp lệ: developer_moves, financing_credit, locations, policy,
price_transactions, project_launches. Chỉ đưa topic có tin.
'project_status': mỗi dự án có tin trong ngày tạo 1 mục; 'project_id' chỉ dùng mã trong
danh mục; article_ids/article_id chỉ dùng mã bài được cấp. Không bịa số liệu.
"""


def build_daily(articles, by_project, date_str, prev_narrative, prev_status=None):
    baseline = ("\n\nTÓM TẮT HÔM QUA:\n" + prev_narrative) if prev_narrative \
        else "\n\n(Không có baseline ngày trước.)"
    if prev_status:
        baseline += ("\n\nTRẠNG THÁI DỰ ÁN HÔM QUA (để so 'vs_previous'/'progress'):\n"
                     + json.dumps(prev_status, ensure_ascii=False))
    user = (
        f"Ngày: {date_str}. Số bài trong ngày: {len(articles)}.\n"
        f"Dữ liệu bài báo:\n\n{render_articles_for_prompt(articles)}"
        f"{baseline}\n\nHãy tạo JSON snapshot theo schema."
    )
    text = call_anthropic(DAILY_SYSTEM, user, CURRENT_MODEL)
    obj = extract_json(text)
    obj = enrich_daily(obj, articles)
    return enrich_project_status(obj, articles)


def enrich_daily(obj, articles):
    """Bổ sung url/source/published_at cho items trong topics + list topics_present.
    Dùng chung cho cả đường LLM API lẫn đường ingest từ Claude Code."""
    art_by_id = {a["article_id"]: a for a in articles}
    for tname, tblock in (obj.get("topics") or {}).items():
        for item in tblock.get("items", []) or []:
            ids = item.get("article_ids", []) or []
            first = next((art_by_id[i] for i in ids if i in art_by_id), None)
            if first:
                item.setdefault("url", first["url"])
                item.setdefault("source", first["source"])
                item.setdefault("published_at", first["published_at"])
    obj["topics_present"] = list((obj.get("topics") or {}).keys())
    return obj


# --------------------------------------------------------------------------- #
# Chế độ KHÔNG dùng LLM (ghép tin theo quy tắc) — dự phòng
# --------------------------------------------------------------------------- #

def deterministic_weekly(articles, by_project, period):
    grouped = defaultdict(list)
    for a in articles:
        for pid in a["projects"]:
            grouped[pid].append(a)
    return {
        "summary": f"Tổng hợp {len(articles)} tin về {len(grouped)} dự án trong {period} "
                   f"(chế độ ghép tự động, chưa qua LLM).",
        "sentiment": "mixed", "tone_shift": "unchanged",
        "project_status": deterministic_project_status(articles),
    }


def deterministic_project_status(articles):
    """project_status theo quy tắc: gom bài theo dự án, events = vài tin mới nhất.
    Không suy luận được 'stage'/'progress' nên để nhãn trung tính."""
    id2p = {p["id"]: p for p in PROJECTS}
    grouped = defaultdict(list)
    for a in articles:
        for pid in a["projects"]:
            grouped[pid].append(a)
    out = []
    for pid, arts in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        p = id2p.get(pid, {})
        out.append({
            "project_id": pid, "project_name": p.get("name", pid),
            "developer": p.get("developer"), "location": p.get("location"),
            "stage": "(chưa phân loại)", "progress": "steady",
            "latest_update": arts[0]["title"],
            "vs_previous": "chế độ ghép tự động — không so sánh",
            "article_count": len(arts),
            "events": [{"date": a["published_at"][:10], "stage": "(chưa phân loại)",
                        "summary": a["title"], "article_id": a["article_id"],
                        "source": a["source"], "url": a["url"],
                        "published_at": a["published_at"], "title": a["title"]}
                       for a in arts[:5]],
        })
    return out


def deterministic_daily(articles, by_project, date_str):
    id2name = {p["id"]: p["name"] for p in PROJECTS}
    items = []
    for a in articles[:30]:
        items.append({"text": a["title"], "article_ids": [a["article_id"]],
                      "url": a["url"], "source": a["source"],
                      "published_at": a["published_at"]})
    topics = {"project_launches": {"sentiment": "mixed", "narrative":
              "Danh sách tin trong ngày (chế độ ghép tự động).", "items": items}} \
        if items else {}
    return {
        "headline": f"{len(articles)} tin về danh mục dự án trong ngày {date_str}.",
        "summary_narrative": "Bản ghép tự động, chưa qua LLM.",
        "sentiment": "mixed", "tone_shift_vs_yesterday": "unchanged",
        "risk_events": [],
        "topics": topics, "topics_present": list(topics.keys()),
        "project_status": deterministic_project_status(articles),
    }


# --------------------------------------------------------------------------- #
# Ghép metadata + ghi DB
# --------------------------------------------------------------------------- #

def iso_week_of(date):
    y, w, _ = date.isocalendar()
    return f"{y}-W{w:02d}"


def projects_tracked_meta(by_project):
    id2 = {p["id"]: p for p in PROJECTS}
    return [{"id": pid, "name": id2[pid]["name"], "developer": id2[pid]["developer"],
             "location": id2[pid]["location"], "article_count": cnt}
            for pid, cnt in by_project.most_common()]


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat()


def upsert(client, coll_name, key_filter, doc, dry_run):
    if dry_run:
        return "dry-run"
    res = client[OUT_DB][coll_name].update_one(key_filter, {"$set": doc}, upsert=True)
    return "upserted" if res.upserted_id else "updated"


# --------------------------------------------------------------------------- #
# Export / Ingest — dùng khi sinh narrative bằng Claude Code (gói Max), không
# cần ANTHROPIC_API_KEY.
#   Bước 1:  python lib_projects.py --export bundle.json --date ...
#   Bước 2:  Claude Code đọc bundle.json, viết 'digest' cho weekly/daily
#   Bước 3:  python lib_projects.py --ingest bundle.json
# --------------------------------------------------------------------------- #

EXPORT_BODY_CHARS = 1200


def export_bundle(client, anchor_date, mode, path):
    """Lọc bài + đóng gói ra JSON để Claude Code viết digest. KHÔNG gọi AI/ghi DB."""
    bundle = {"anchor_date": str(anchor_date), "segment": SEGMENT, "modes": {}}

    if mode in ("weekly", "both"):
        end = dt.datetime.combine(anchor_date, dt.time.min) + dt.timedelta(days=1)
        start = end - dt.timedelta(days=7)
        arts, byp = fetch_matched_articles(client, start, end)
        prev = client[OUT_DB][OUT_WEEKLY].find_one({"segment": SEGMENT},
                                                   sort=[("week_of", -1)])
        bundle["modes"]["weekly"] = {
            "week_of": iso_week_of(anchor_date),
            "period": f"{start.date()} đến {(end - dt.timedelta(days=1)).date()}",
            "article_count": len(arts),
            "projects_tracked": projects_tracked_meta(byp),
            "prev_summary": prev.get("summary") if prev else None,
            "prev_project_status": slim_prev_status(prev),
            "articles": _slim_articles(arts),
            "schema_hint": WEEKLY_SYSTEM,
            "digest": None,  # <-- Claude Code điền vào đây
        }
        print(f"[export] weekly: {len(arts)} bài / {len(byp)} dự án")

    if mode in ("daily", "both"):
        start = dt.datetime.combine(anchor_date, dt.time.min)
        end = start + dt.timedelta(days=1)
        arts, byp = fetch_matched_articles(client, start, end)
        prev = client[OUT_DB][OUT_DAILY].find_one(
            {"segment": SEGMENT, "date": {"$lt": str(anchor_date)}},
            sort=[("date", -1)])
        bundle["modes"]["daily"] = {
            "date": str(anchor_date),
            "article_count": len(arts),
            "projects_tracked": projects_tracked_meta(byp),
            "prev_summary_narrative": prev.get("summary_narrative") if prev else None,
            "prev_project_status": slim_prev_status(prev),
            "articles": _slim_articles(arts),
            "schema_hint": DAILY_SYSTEM,
            "digest": None,  # <-- Claude Code điền vào đây
        }
        print(f"[export] daily: {len(arts)} bài / {len(byp)} dự án")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2, default=str)
    print(f"[export] Đã ghi bundle: {path}")
    print("        -> Claude Code hãy điền field 'digest' cho mỗi mode rồi chạy --ingest.")


def _slim_articles(articles):
    out = []
    for a in articles:
        out.append({
            "article_id": a["article_id"], "title": a["title"],
            "source": a["source"], "published_at": a["published_at"],
            "url": a["url"], "tickers": a.get("tickers", []),
            "projects": a["projects"],
            "body": re.sub(r"\s+", " ", a["body"])[:EXPORT_BODY_CHARS],
        })
    return out


def ingest_bundle(client, path, model_label, dry_run):
    """Đọc bundle đã có 'digest' (do Claude Code viết) -> gắn metadata -> ghi DB."""
    with open(path, "r", encoding="utf-8") as f:
        bundle = json.load(f)
    modes = bundle.get("modes", {})

    if "weekly" in modes and modes["weekly"].get("digest"):
        b = modes["weekly"]
        articles = b["articles"]
        digest = expand_citations(b["digest"], articles, WEEKLY_CITATION_FIELDS)
        digest = enrich_project_status(digest, articles)
        doc = {
            "segment": SEGMENT, "week_of": b["week_of"], "period": b["period"],
            "article_count": b["article_count"], "generated_at": now_iso(),
            "model": model_label, "has_baseline": bool(b.get("prev_summary")),
            "projects_tracked": b["projects_tracked"], **digest,
        }
        status = upsert(client, OUT_WEEKLY, {"segment": SEGMENT,
                        "week_of": b["week_of"]}, doc, dry_run)
        print(f"[ingest] weekly {status} -> {OUT_DB}.{OUT_WEEKLY}")

    if "daily" in modes and modes["daily"].get("digest"):
        b = modes["daily"]
        articles = b["articles"]
        digest = enrich_daily(b["digest"], articles)
        digest = enrich_project_status(digest, articles)
        doc = {
            "date": b["date"], "segment": SEGMENT, "type": "daily",
            "article_count": b["article_count"], "generated_at": now_iso() + "Z",
            "model": model_label, "projects_tracked": b["projects_tracked"], **digest,
        }
        status = upsert(client, OUT_DAILY, {"segment": SEGMENT,
                        "date": b["date"]}, doc, dry_run)
        print(f"[ingest] daily {status} -> {OUT_DB}.{OUT_DAILY}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

CURRENT_MODEL = DEFAULT_MODEL


def run_weekly(client, anchor_date, use_llm, dry_run):
    end = dt.datetime.combine(anchor_date, dt.time.min) + dt.timedelta(days=1)
    start = end - dt.timedelta(days=7)
    period = f"{start.date()} đến {(end - dt.timedelta(days=1)).date()}"
    week_of = iso_week_of(anchor_date)
    print(f"[weekly] {period}  (week_of={week_of})")

    articles, by_project = fetch_matched_articles(client, start, end)
    print(f"[weekly] khớp {len(articles)} bài / {len(by_project)} dự án")

    prev = client[OUT_DB][OUT_WEEKLY].find_one(
        {"segment": SEGMENT}, sort=[("week_of", -1)])
    prev_summary = prev.get("summary") if prev else None

    if use_llm and articles:
        body = build_weekly(articles, by_project, period, prev_summary,
                            slim_prev_status(prev))
    else:
        body = deterministic_weekly(articles, by_project, period)

    doc = {
        "segment": SEGMENT, "week_of": week_of, "period": period,
        "article_count": len(articles),
        "generated_at": now_iso(),
        "model": CURRENT_MODEL if (use_llm and articles) else "deterministic",
        "has_baseline": bool(prev_summary),
        "projects_tracked": projects_tracked_meta(by_project),
        **body,
    }
    status = upsert(client, OUT_WEEKLY, {"segment": SEGMENT, "week_of": week_of},
                    doc, dry_run)
    print(f"[weekly] {status} -> {OUT_DB}.{OUT_WEEKLY}")
    return doc


def run_daily(client, anchor_date, use_llm, dry_run):
    start = dt.datetime.combine(anchor_date, dt.time.min)
    end = start + dt.timedelta(days=1)
    date_str = str(anchor_date)
    print(f"[daily] {date_str}")

    articles, by_project = fetch_matched_articles(client, start, end)
    print(f"[daily] khớp {len(articles)} bài / {len(by_project)} dự án")

    prev = client[OUT_DB][OUT_DAILY].find_one(
        {"segment": SEGMENT, "date": {"$lt": date_str}}, sort=[("date", -1)])
    prev_narr = prev.get("summary_narrative") if prev else None

    if use_llm and articles:
        body = build_daily(articles, by_project, date_str, prev_narr,
                           slim_prev_status(prev))
    else:
        body = deterministic_daily(articles, by_project, date_str)

    doc = {
        "date": date_str, "segment": SEGMENT, "type": "daily",
        "article_count": len(articles),
        "generated_at": now_iso() + "Z",
        "model": CURRENT_MODEL if (use_llm and articles) else "deterministic",
        "projects_tracked": projects_tracked_meta(by_project),
        **body,
    }
    status = upsert(client, OUT_DAILY, {"segment": SEGMENT, "date": date_str},
                    doc, dry_run)
    print(f"[daily] {status} -> {OUT_DB}.{OUT_DAILY}")
    return doc


def main():
    global CURRENT_MODEL
    ap = argparse.ArgumentParser(description="Cập nhật news các dự án (VSB) theo format RE_News_Collection.")
    ap.add_argument("--mode", choices=["weekly", "daily", "both"], default="both")
    ap.add_argument("--date", help="YYYY-MM-DD (mặc định: hôm nay)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--no-llm", action="store_true", help="Không gọi LLM, ghép theo quy tắc")
    ap.add_argument("--dry-run", action="store_true", help="Không ghi DB, xuất JSON ra file")
    ap.add_argument("--export", metavar="FILE",
                    help="Chỉ lọc bài + xuất bundle JSON (để Claude Code viết digest)")
    ap.add_argument("--ingest", metavar="FILE",
                    help="Đọc bundle đã có digest (do Claude Code viết) rồi ghi DB")
    ap.add_argument("--model-label", default="claude-code (max)",
                    help="Giá trị field 'model' khi ingest")
    args = ap.parse_args()

    CURRENT_MODEL = args.model
    anchor = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    use_llm = not args.no_llm

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=20000)
    client.admin.command("ping")

    # --- Đường Claude Code (gói Max): export -> [Claude viết] -> ingest ---
    if args.export:
        print(f"Kết nối MongoDB OK. anchor_date={anchor}  (EXPORT)")
        export_bundle(client, anchor, args.mode, args.export)
        return
    if args.ingest:
        print(f"Kết nối MongoDB OK.  (INGEST)")
        ingest_bundle(client, args.ingest, args.model_label, args.dry_run)
        return

    print(f"Kết nối MongoDB OK. anchor_date={anchor}  llm={use_llm}  dry_run={args.dry_run}")

    out = {}
    if args.mode in ("weekly", "both"):
        out["weekly"] = run_weekly(client, anchor, use_llm, args.dry_run)
    if args.mode in ("daily", "both"):
        out["daily"] = run_daily(client, anchor, use_llm, args.dry_run)

    if args.dry_run:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            f"project_news_preview_{anchor}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2, default=str)
        print(f"[dry-run] Đã ghi JSON xem trước: {path}")


if __name__ == "__main__":
    main()
