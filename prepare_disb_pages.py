# -*- coding: utf-8 -*-
"""
prepare_disb_pages.py — Render các trang PDF báo cáo giải ngân (Bộ Tài chính, Phụ lục III)
thành ảnh PNG để Claude ĐỌC-HIỂU trích số (PDF là ảnh scan, không có text layer).

Suy ky/năm/file_nguon từ tên file (vd '8M2026-pages.pdf' -> ky '8T2026', nam 2026).
Ra: <outdir>/manifest.json = {ky, nam, file_nguon, as_of_hint, pages:[đường dẫn png]}.

  python prepare_disb_pages.py disbursement_data/8M2026-pages.pdf --out _disb_pages
"""
import argparse
import json
import os
import re

import pymupdf   # PyMuPDF


def _ky_from_name(fn):
    """'8M2026-pages.pdf' -> ('8T2026', 2026). 'M' = tháng luỹ kế trong năm."""
    m = re.search(r"(\d{1,2})M(\d{4})", os.path.basename(fn))
    if not m:
        return "", None
    return f"{int(m.group(1))}T{m.group(2)}", int(m.group(2))


def run(pdf, outdir, dpi):
    os.makedirs(outdir, exist_ok=True)
    ky, nam = _ky_from_name(pdf)
    doc = pymupdf.open(pdf)
    pages = []
    for i in range(doc.page_count):
        pix = doc[i].get_pixmap(dpi=dpi)
        p = os.path.join(outdir, f"page_{i+1:02d}.png")
        pix.save(p)
        pages.append(p)
    manifest = {"ky": ky, "nam": nam, "file_nguon": os.path.basename(pdf),
                "pages": pages, "n_pages": len(pages)}
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"Render {len(pages)} trang (dpi {dpi}) · ky={ky} · {os.path.basename(pdf)} -> {outdir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", default="_disb_pages")
    ap.add_argument("--dpi", type=int, default=150)
    a = ap.parse_args()
    run(a.pdf, a.out, a.dpi)
