# -*- coding: utf-8 -*-
"""
fetch_disb_report.py — TỰ ĐỘNG tải báo cáo giải ngân chi tiết theo DỰ ÁN (ngành GTVT + đường sắt)
mới nhất từ API mof.gov.vn, lưu vào disbursement_data/ và in đường dẫn PDF ra stdout.

API (curl/urllib được, không cần browser):
  GET  /api/articlecategory/getbyslug?slug=bo-tai-chinh/du-lieu-va-thong-ke-tai-chinh  -> categoryId
  POST /api/article/reads?offset=0&limit=N  body {"categoryId": id}                    -> list bài
  GET  /api/article/getbyslug?slug=<slug>                                               -> có link media-article/*.pdf
  GET  /api/mediafile/<path>.pdf                                                        -> tải PDF

Lọc: báo cáo CHI TIẾT per-dự-án GTVT (title có 'giải ngân vốn đầu tư công' + ('quan trọng quốc gia'
hoặc 'giao thông' hoặc '<N> tháng'); BỎ 'báo cáo nhanh theo tuần', 'mục tiêu quốc gia', 'giai đoạn').

  python fetch_disb_report.py [--out disbursement_data] [--limit 40]
Ra stdout: "<pdf_path>\t<ky>\t<title>"  (rỗng nếu không có báo cáo phù hợp)
"""
import argparse
import json
import os
import re
import sys
import urllib.request

BASE = "https://www.mof.gov.vn"
CAT_SLUG = "bo-tai-chinh/du-lieu-va-thong-ke-tai-chinh"
UA = "Mozilla/5.0 (compatible; infra-disb/1.0)"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _post(url, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _is_detail_transport(title):
    t = title.lower()
    if "giải ngân" not in t and "giai ngan" not in t:
        return False
    if any(x in t for x in ("báo cáo nhanh theo tuần", "mục tiêu quốc gia", "giai đoạn 20")):
        return False
    return ("quan trọng quốc gia" in t or "giao thông" in t
            or re.search(r"\d+\s*tháng", t) is not None)


def _period(title):
    """Số 'X tháng' trong title để chọn kỳ MỚI nhất; không có thì -1."""
    m = re.search(r"(\d+)\s*tháng", title.lower())
    return int(m.group(1)) if m else -1


def run(outdir, limit):
    cat = json.loads(_get(f"{BASE}/api/articlecategory/getbyslug?slug={CAT_SLUG}"))
    cid = cat["data"]["id"]
    arts = json.loads(_post(f"{BASE}/api/article/reads?offset=0&limit={limit}", {"categoryId": cid}))
    items = arts.get("data") or []
    cand = [a for a in items if _is_detail_transport(a.get("title", ""))]
    if not cand:
        print("", end=""); sys.stderr.write("Không thấy báo cáo chi tiết GTVT trong danh mục.\n"); return
    # chọn kỳ cao nhất (X tháng lớn nhất), hoà thì theo publicationTime mới nhất
    cand.sort(key=lambda a: (_period(a.get("title", "")), a.get("publicationTime", "")), reverse=True)
    best = cand[0]
    slug, title = best["slug"], best.get("title", "")
    art = json.loads(_get(f"{BASE}/api/article/getbyslug?slug={slug}"))
    m = re.search(r"media-article/[a-f0-9-]+/[a-f0-9-]+\.pdf", json.dumps(art, ensure_ascii=False), re.I)
    if not m:
        sys.stderr.write(f"Báo cáo '{title[:50]}' không có PDF đính kèm.\n"); return
    pdf_url = f"{BASE}/api/mediafile/{m.group(0)}"
    nper = _period(title)
    y = (re.search(r"năm\s*(\d{4})", title) or [None, "2026"])[1]
    ky = f"{nper}T{y}" if nper > 0 else f"PUB{best.get('publicationTime','')[:7]}"
    os.makedirs(outdir, exist_ok=True)
    pdf_path = os.path.join(outdir, f"auto_{ky}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(_get(pdf_url))
    print(f"{pdf_path}\t{ky}\t{title}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="disbursement_data")
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()
    run(a.out, a.limit)
