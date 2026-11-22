# -*- coding: utf-8 -*-
"""
fetch_disb_report.py — TỰ ĐỘNG tìm + tải báo cáo giải ngân vốn đầu tư công MỚI NHẤT CHƯA NẠP
(báo cáo Bộ Tài chính "Về tình hình phân bổ, giải ngân vốn đầu tư công X tháng năm YYYY", có phụ lục
giải ngân theo TỪNG DỰ ÁN ngành GTVT + đường sắt), lưu vào disbursement_data/ và in ra stdout.

ĐA NGUỒN — cùng một báo cáo được đăng ở nhiều nơi, nơi nào có trước thì dùng:
  1) vanban.chinhphu.vn  (Cổng văn bản Chính phủ — thường đăng SỚM NHẤT, vài ngày sau khi ban hành)
       GET  /he-thong-van-ban?classid=0&mode=1          -> form tìm kiếm (ASP.NET postback)
       POST cùng URL + txtSearchKeyword                  -> bảng kết quả (docid, số ký hiệu, ngày, trích yếu)
       GET  /?pageid=27160&docid=<id>                    -> link https://datafiles.chinhphu.vn/...pdf
  2) mof.gov.vn          (mục "Dữ liệu và thống kê tài chính" — có khi đăng chậm hoặc bỏ sót kỳ)
       GET  /api/articlecategory/getbyslug?slug=...      -> categoryId
       POST /api/article/reads  body {"categoryId": id}  -> list bài
       GET  /api/article/getbyslug?slug=<slug>           -> link media-article/*.pdf

NHẬN DIỆN theo TIÊU ĐỀ (không theo vị trí trong danh sách): "phân bổ, giải ngân ... đầu tư công X tháng năm YYYY".
  -> kỳ = (YYYY, X). So sánh theo CẢ NĂM lẫn THÁNG (trước đây chỉ so X nên "13 tháng năm 2025" thắng
     "7 tháng năm 2026" và bị nạp nhầm thành kỳ 1T2026).
  -> BỎ: báo cáo nhanh theo tuần, CTMTQG, giai đoạn trung hạn, và X > 12 (báo cáo khoá sổ năm trước).
CHỈ lấy kỳ MỚI HƠN kỳ đã có trong Mongo (Infra_Disbursement); nhiều kỳ mới -> lấy kỳ CŨ NHẤT trước
(chạy lại sẽ lấy kỳ kế tiếp) để chuỗi không bị thủng.

  python fetch_disb_report.py [--out disbursement_data] [--list] [--check] [--ignore-db]
Ra stdout (1 dòng, tab): "<pdf_path>\t<ky>\t<title>\t<url>\t<source>"   — rỗng nếu không có kỳ mới.
  --check : chỉ in "<ky>\t<title>\t<source>" của kỳ mới (KHÔNG tải PDF) — cổng chặn rẻ cho CI.
  --list  : in mọi ứng viên tìm thấy ở các nguồn (để kiểm tra).
"""
import argparse
import html
import http.cookiejar
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
KEYWORD = "phân bổ, giải ngân vốn đầu tư công"

# "…phân bổ, giải ngân [kế hoạch] [vốn] đầu tư công 10 tháng năm 2026…"
RX_PERIOD = re.compile(
    r"phân\s*b[ổố]\s*,?\s*(?:và\s*)?giải\s*ngân\s*(?:kế\s*hoạch\s*)?(?:vốn\s*)?đầu\s*tư\s*công\s*"
    r"(\d{1,2})\s*tháng\s*(?:đầu\s*)?năm\s*(\d{4})")
SKIP = ("báo cáo nhanh", "theo tuần", "mục tiêu quốc gia", "giai đoạn", "trung hạn")


def _norm(s):
    return " ".join(unicodedata.normalize("NFC", html.unescape(s or "")).lower().split())


def period_of(title):
    """(năm, số tháng) nếu tiêu đề là báo cáo định kỳ 'X tháng năm YYYY'; không phải -> None."""
    t = _norm(title)
    if any(x in t for x in SKIP):
        return None
    m = RX_PERIOD.search(t)
    if not m:
        return None
    months, year = int(m.group(1)), int(m.group(2))
    if not (1 <= months <= 12) or not (2020 <= year <= 2100):
        return None
    return (year, months)


def _req(url, data=None, headers=None, opener=None, timeout=90):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with (opener.open(req, timeout=timeout) if opener else urllib.request.urlopen(req, timeout=timeout)) as r:
        return r.read()


# ------------------------------------------------------------------ nguồn 1: vanban.chinhphu.vn
CP = "https://vanban.chinhphu.vn"
CP_LIST = CP + "/he-thong-van-ban?classid=0&mode=1"


def src_chinhphu():
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    page = _req(CP_LIST, opener=op).decode("utf-8", "replace")
    form = {}
    for m in re.finditer(r"<input\b[^>]*>", page):                 # giữ nguyên field ẩn (__VIEWSTATE…)
        tag = m.group(0)
        n = re.search(r'name="([^"]+)"', tag)
        ty = (re.search(r'type="([^"]+)"', tag) or [None, "text"])[1]
        if not n or ty in ("submit", "button", "image", "checkbox", "radio"):
            continue
        v = re.search(r'value="([^"]*)"', tag)
        form[n.group(1)] = html.unescape(v.group(1)) if v else ""
    for sm in re.finditer(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', page, re.S):
        sel = (re.search(r'<option[^>]*selected[^>]*value="([^"]*)"', sm.group(2))
               or re.search(r'<option[^>]*value="([^"]*)"', sm.group(2)))
        form[sm.group(1)] = html.unescape(sel.group(1)) if sel else ""
    kw = next((k for k in form if k.endswith("$txtSearchKeyword")), None)
    if not kw:
        raise RuntimeError("không thấy ô tìm kiếm trên vanban.chinhphu.vn (trang đổi cấu trúc?)")
    form[kw] = KEYWORD
    form[kw.rsplit("$", 1)[0] + "$btnSearch"] = "Tìm kiếm"
    res = _req(CP_LIST, data=urllib.parse.urlencode(form).encode(), opener=op,
               headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": CP_LIST}
               ).decode("utf-8", "replace")
    out = []
    for docid, code, date, title in re.findall(
            r"docid=(\d+)[^']*'>\s*<span class=\"code\">([^<]*)</span>\s*<span class=\"issue-v2\">([^<]*)</span>"
            r".*?<span class=\"substract\">([^<]*)</span>", res, re.S):
        title = html.unescape(title).strip()
        per = period_of(title)
        if per:
            out.append({"period": per, "title": f"{code.strip()} ({date.strip()}): {title}", "source": "vanban.chinhphu.vn",
                        "page": f"{CP}/?pageid=27160&docid={docid}", "_docid": docid})
    return out


def pdf_chinhphu(c):
    page = _req(c["page"]).decode("utf-8", "replace")
    m = re.search(r"https://datafiles\.chinhphu\.vn/[^\"'<> ]+\.pdf", page, re.I)
    return m.group(0) if m else None


# ------------------------------------------------------------------ nguồn 2: mof.gov.vn
MOF = "https://www.mof.gov.vn"
MOF_SLUG = "bo-tai-chinh/du-lieu-va-thong-ke-tai-chinh"


def src_mof(limit=80):
    cat = json.loads(_req(f"{MOF}/api/articlecategory/getbyslug?slug={MOF_SLUG}"))
    arts = json.loads(_req(f"{MOF}/api/article/reads?offset=0&limit={limit}",
                           data=json.dumps({"categoryId": cat["data"]["id"]}).encode(),
                           headers={"Content-Type": "application/json"}))
    out = []
    for a in arts.get("data") or []:
        per = period_of(a.get("title", ""))
        if per:
            out.append({"period": per, "title": a.get("title", "").strip(), "source": "mof.gov.vn",
                        "page": f"{MOF}/api/article/getbyslug?slug={a['slug']}"})
    return out


def pdf_mof(c):
    art = _req(c["page"]).decode("utf-8", "replace")
    m = re.search(r"media-article/[a-f0-9-]+/[a-f0-9-]+\.pdf", art, re.I)
    return f"{MOF}/api/mediafile/{m.group(0)}" if m else None


SOURCES = [("vanban.chinhphu.vn", src_chinhphu, pdf_chinhphu), ("mof.gov.vn", src_mof, pdf_mof)]
PDF_OF = {name: fn for name, _, fn in SOURCES}


def candidates():
    """Mọi báo cáo định kỳ tìm thấy, từ MỌI nguồn. Một nguồn lỗi không làm hỏng nguồn khác."""
    out = []
    for name, lister, _ in SOURCES:
        try:
            found = lister()
            sys.stderr.write(f"[{name}] {len(found)} báo cáo định kỳ\n")
            out += found
        except Exception as e:                                       # noqa: BLE001 — nguồn nào chết thì bỏ qua
            sys.stderr.write(f"[{name}] LỖI: {str(e)[:160]}\n")
    return out


def have_period():
    """Kỳ mới nhất ĐÃ có trong Mongo → (năm, tháng). Lỗi Mongo → raise (không tải bừa)."""
    from pymongo import MongoClient
    from lib_db import mongo_uri
    best = (0, 0)
    col = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)["dc_commodity"]["Infra_Disbursement"]
    for d in col.find({}, {"series.ky": 1}):
        for s in d.get("series", []):
            m = re.match(r"(\d+)T(\d{4})", s.get("ky") or "")
            if m:
                best = max(best, (int(m.group(2)), int(m.group(1))))
    return best


def run(outdir, do_list, check, ignore_db):
    cands = candidates()
    if do_list:
        for c in sorted(cands, key=lambda c: c["period"], reverse=True):
            sys.stderr.write(f"  {c['period'][1]:>2}T{c['period'][0]} · {c['source']:<20} · {c['title'][:110]}\n")
    have = (0, 0) if ignore_db else have_period()
    sys.stderr.write(f"Kỳ mới nhất đã nạp: {have[1]}T{have[0]}\n")
    new = sorted({c["period"] for c in cands if c["period"] > have})
    if not new:
        sys.stderr.write("Không có báo cáo kỳ mới hơn ở nguồn nào.\n")
        return
    per = new[-1] if ignore_db else new[0]                           # nhiều kỳ mới → kỳ CŨ NHẤT trước
    ky = f"{per[1]}T{per[0]}"
    group = [c for c in cands if c["period"] == per]                 # cùng kỳ ở nhiều nguồn (theo thứ tự SOURCES)
    if check:
        print(f"{ky}\t{group[0]['title']}\t{', '.join(c['source'] for c in group)}")
        return
    os.makedirs(outdir, exist_ok=True)
    pdf_path = os.path.join(outdir, f"auto_{ky}.pdf")
    for c in group:                                                  # nguồn đầu lỗi/thiếu PDF → thử nguồn kế
        try:
            url = PDF_OF[c["source"]](c)
            if not url:
                sys.stderr.write(f"[{c['source']}] '{c['title'][:60]}' không có PDF đính kèm.\n")
                continue
            data = _req(url, timeout=600)
            if data[:5] != b"%PDF-":
                sys.stderr.write(f"[{c['source']}] tệp tải về không phải PDF.\n")
                continue
            with open(pdf_path, "wb") as f:
                f.write(data)
            print(f"{pdf_path}\t{ky}\t{c['title']}\t{url}\t{c['source']}")
            return
        except Exception as e:                                       # noqa: BLE001
            sys.stderr.write(f"[{c['source']}] tải lỗi: {str(e)[:160]}\n")
    sys.stderr.write(f"Có kỳ mới {ky} nhưng không nguồn nào tải được PDF.\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="disbursement_data")
    ap.add_argument("--list", action="store_true", help="in mọi ứng viên ở các nguồn (stderr)")
    ap.add_argument("--check", action="store_true", help="chỉ báo có kỳ mới hay không, không tải PDF")
    ap.add_argument("--ignore-db", action="store_true", help="không so với Mongo — lấy kỳ mới nhất tìm thấy")
    a = ap.parse_args()
    run(a.out, a.list, a.check, a.ignore_db)
