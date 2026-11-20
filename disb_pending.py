# -*- coding: utf-8 -*-
"""
disb_pending.py — In ra các PDF giải ngân trong disbursement_data/ có KỲ CHƯA nằm trong CSV
(mỗi kỳ 1 dòng, ưu tiên bản '-pages.pdf'). Dùng cho workflow lặp xử lý kỳ mới.

  python disb_pending.py
"""
import csv
import glob
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "giai_ngan_ha_tang_2025_2026.csv")
PDFDIR = os.path.join(HERE, "disbursement_data")


def _ky(fn):
    m = re.search(r"(\d{1,2})M(\d{4})", os.path.basename(fn))
    return f"{int(m.group(1))}T{m.group(2)}" if m else ""


def main():
    have = set()
    if os.path.exists(CSV):
        have = {r.get("ky", "") for r in csv.DictReader(open(CSV, encoding="utf-8-sig"))}
    by_ky = {}                                   # ky -> path (ưu tiên -pages)
    for p in sorted(glob.glob(os.path.join(PDFDIR, "*.pdf"))):
        k = _ky(p)
        if not k or k in have:
            continue
        if k not in by_ky or p.endswith("-pages.pdf"):
            by_ky[k] = p
    for k in sorted(by_ky, key=lambda x: (int(x.split("T")[1]), int(x.split("T")[0]))):
        print(by_ky[k])


if __name__ == "__main__":
    main()
