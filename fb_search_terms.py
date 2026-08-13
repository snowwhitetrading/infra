# -*- coding: utf-8 -*-
"""
fb_search_terms.py — In danh sách từ khóa search Facebook (tên dự án từ registry).

Dùng cho task lịch: Claude in Chrome mở facebook.com/search/posts/?q=<từ khóa> cho từng dòng.
Sắp theo tid tăng dần (dự án curated quan trọng lên trước), cắt --top để giới hạn thời gian.

  python fb_search_terms.py            # top 8
  python fb_search_terms.py --top 12
"""
import argparse
from lib_projects import load_registry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=8)
    n = ap.parse_args().top
    rows = [p for p in load_registry(force=True) if p.get("active", True) and p.get("tid")]
    rows.sort(key=lambda p: p["tid"])
    for p in rows[:n]:
        # tên ngắn gọn để search: alias đầu (thường là tên chính thức)
        term = (p.get("aliases") or [p.get("name", "")])[0]
        print(term)


if __name__ == "__main__":
    main()
