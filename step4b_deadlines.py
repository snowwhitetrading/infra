# -*- coding: utf-8 -*-
"""
step4b_deadlines.py — HẠN + ĐÁNH GIÁ TIẾN ĐỘ lấy TỪ TIN (deterministic, có nguồn), KHÔNG để LLM bịa.

Quét dc_news.project_news_raw (gate: TIÊU ĐỀ chứa alias dự án → tránh gán nhầm). Với mỗi dự án:
  1) HẠN hoàn thành từ tin MỚI NHẤT → mark tier='deadline' (có src) + đặt phase build cuối .to.
  2) ĐÁNH GIÁ paceAuto (đúng/chậm/vượt) KẾT HỢP nhiều tín hiệu, ưu tiên bằng chứng mạnh:
     - QUÁ HẠN: hạn (có nguồn) đã qua mà chưa có mark hoàn thành → chậm.
     - HẠN BỊ LÙI: tin sau nêu hạn muộn hơn tin trước → chậm.
     - TIN NÓI RÕ: RX_DELAY→chậm · RX_AHEAD→vượt · RX_ONTRACK→đúng.
   Ghi paceAuto + paceWhy (lý do, có nguồn). Không đủ tín hiệu → paceAuto='' (web hiện 'đang/chưa thi công').
Idempotent, chạy hàng giờ trong CI.
"""
import datetime as dt
from collections import defaultdict

from pymongo import MongoClient

from lib_db import mongo_uri
from lib_marks import deadline_month, progress_pct, RX_DELAY, RX_AHEAD, RX_ONTRACK
from lib_projects import build_alias_regex, pid2tid

DB = "dc_commodity"


def _m2n(s):
    y, m = s.split("-")
    return int(y) * 12 + int(m)


def run():
    c = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    tr = c[DB]["Infra_Project_Tracker"]
    raw = c["dc_news"]["project_news_raw"]
    p2t = pid2tid()
    t2rx = defaultdict(list)
    for pid, r in build_alias_regex().items():
        if pid in p2t:
            t2rx[p2t[pid]].append(r)

    dstmts = defaultdict(list)          # tid -> [(article_date, deadline_month, source)]
    sig = defaultdict(lambda: {"delay": None, "ahead": None, "ontrack": None, "pct": None})
    for d in raw.find({"projects": {"$ne": []}},
                      {"title": 1, "description": 1, "date": 1, "source": 1, "projects": 1}):
        pub = (d.get("date") or "")[:7]
        ti = d.get("title") or ""
        if len(pub) < 7:
            continue
        blob = ti + " " + (d.get("description") or "")
        dl = deadline_month(blob, pub)
        adate = d.get("date", "")
        for tid in d.get("projects", []):
            if not any(r.search(ti) for r in t2rx.get(tid, [])):    # TIÊU ĐỀ về đúng dự án
                continue
            if dl:
                dstmts[tid].append((adate, dl, d.get("source", "?")))
            s = sig[tid]
            if RX_DELAY.search(blob) and (s["delay"] is None or adate > s["delay"][0]):
                s["delay"] = (adate, d.get("source", "?"))
            if RX_AHEAD.search(blob) and (s["ahead"] is None or adate > s["ahead"][0]):
                s["ahead"] = (adate, d.get("source", "?"))
            if RX_ONTRACK.search(blob) and (s["ontrack"] is None or adate > s["ontrack"][0]):
                s["ontrack"] = (adate, d.get("source", "?"))
            pc = progress_pct(blob)
            if pc is not None and (s["pct"] is None or adate > s["pct"][0]):
                s["pct"] = (adate, pc, d.get("source", "?"))    # % mới nhất theo tin

    today = dt.date.today().strftime("%Y-%m")
    tids = set(dstmts) | set(sig)
    n_dl = n_pace = 0
    for tid in tids:
        p = tr.find_one({"_key": "project", "id": tid})
        if not p:
            continue
        marks = [m for m in p.get("marks", []) if m.get("tier") != "deadline"]
        phases = p.get("phases", [])
        latest = None
        if dstmts.get(tid):
            adate, dl, src = sorted(dstmts[tid])[-1]                 # tin mới nhất nêu hạn
            latest = dl
            marks.append({"date": dl, "type": "ms", "tier": "deadline",
                          "label": "Hạn dự kiến hoàn thành (theo tin)", "src": f"{src} · {adate[:7]}"})
            builds = [ph for ph in phases if ph.get("kind") == "build"]
            if builds and dl > (builds[-1].get("from") or "0"):
                builds[-1]["to"] = dl
                if dl <= today:
                    builds[-1]["state"] = "ongoing"
            n_dl += 1

        # ── ĐÁNH GIÁ paceAuto (đa tín hiệu, ưu tiên bằng chứng mạnh) ──
        done_after = latest and any((m.get("date") or "") >= latest and
                                    (m.get("tier") == "actual" or m.get("type") == "done")
                                    for m in marks)
        overdue = latest and latest < today and not done_after
        ds_sorted = sorted(dstmts.get(tid, []))
        pushed = (len(ds_sorted) >= 2 and                                    # tin sau nêu hạn MUỘN hơn tin trước ≥3 tháng
                  _m2n(ds_sorted[-1][1]) - _m2n(ds_sorted[0][1]) >= 3)
        s = sig[tid]
        pace, why = "", ""
        if overdue:
            pace, why = "chậm tiến độ", f"Quá hạn {latest} (theo tin) mà chưa có tin xác nhận hoàn thành."
        elif pushed:
            pace, why = "chậm tiến độ", f"Hạn bị lùi (từ {ds_sorted[0][1]} sang {ds_sorted[-1][1]} theo tin)."
        elif s["delay"]:
            pace, why = "chậm tiến độ", f"Tin nêu chậm/lùi tiến độ (theo {s['delay'][1]} {s['delay'][0][:7]})."
        elif s["ahead"]:
            pace, why = "vượt tiến độ", f"Tin nêu về đích sớm/vượt tiến độ (theo {s['ahead'][1]} {s['ahead'][0][:7]})."
        elif latest and s["pct"] and [ph for ph in phases if ph.get("kind") == "build" and ph.get("from")]:
            # % đã đạt so với KỲ VỌNG theo timeline (chỉ khi có hạn nguồn để tính kỳ vọng)
            frm = min(ph["from"] for ph in phases if ph.get("kind") == "build" and ph.get("from"))
            span = _m2n(latest) - _m2n(frm)
            if span > 0:
                exp = max(0, min(100, round((_m2n(today) - _m2n(frm)) / span * 100)))
                pc = s["pct"][1]
                if pc < exp - 25:
                    pace, why = "chậm tiến độ", f"Mới đạt {pc}% trong khi kỳ vọng ~{exp}% theo mốc (hạn {latest}, theo {s['pct'][2]} {s['pct'][0][:7]})."
                elif pc > exp + 10:
                    pace, why = "vượt tiến độ", f"Đạt {pc}% vượt kỳ vọng ~{exp}% theo mốc (theo {s['pct'][2]} {s['pct'][0][:7]})."
                else:
                    pace, why = "đúng tiến độ", f"Đạt {pc}% bám sát kỳ vọng ~{exp}% theo mốc (theo {s['pct'][2]} {s['pct'][0][:7]})."
        elif s["ontrack"]:
            pace, why = "đúng tiến độ", f"Tin nêu bám sát/đúng tiến độ (theo {s['ontrack'][1]} {s['ontrack'][0][:7]})."

        upd = {"marks": marks, "phases": phases, "paceAuto": pace, "paceWhy": why}
        tr.update_one({"_key": "project", "id": tid}, {"$set": upd})
        if pace:
            n_pace += 1

    # dọn hạn + pace ở dự án KHÔNG còn tín hiệu
    tr.update_many({"_key": "project", "id": {"$nin": list(tids)}},
                   {"$set": {"paceAuto": "", "paceWhy": ""}, "$pull": {"marks": {"tier": "deadline"}}})
    print(f"Hạn từ tin: {n_dl} dự án · paceAuto (đánh giá máy): {n_pace} dự án")


if __name__ == "__main__":
    run()
