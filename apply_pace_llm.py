# -*- coding: utf-8 -*-
"""
apply_pace_llm.py — Ghi kết quả AGENT ĐỌC-HIỂU (pace_verdicts.json) vào Infra_Project_Tracker:
paceLLM/paceWhyLLM/paceConf + deadlineLLM + tmdtLLM + ownerLLM (đều có nguồn, có validate).

% HOÀN THÀNH theo THANG VÒNG ĐỜI CHUẨN (xem pace_agent_prompt.txt): progress = 5A + 10G + 80C + 5F.
Agent trả "parts" {A,G,C,F} → script TÍNH LẠI progress từ parts (ép đúng công thức, mọi dự án cùng thang),
lưu paceParts + paceProgressBasis; % TỪNG HẠNG MỤC ghi vào items[i].pct (khớp theo tên, chỉ tăng).

verdicts JSON = {tid: {pace, why, src, confidence, deadline, deadline_src, tmdt, tmdt_src, owner, owner_src}}
Field nào rỗng/null/không hợp lệ → BỎ QUA (không ghi) → step5 tự dùng deterministic fallback.

  python apply_pace_llm.py [--in pace_verdicts.json]
"""
import argparse
import datetime as dt
import json
import os
import re

from pymongo import MongoClient, UpdateOne

from lib_db import mongo_uri

HERE = os.path.dirname(os.path.abspath(__file__))
DB = "dc_commodity"
_PACE = {"vượt tiến độ", "chậm tiến độ", "đúng tiến độ", ""}
_YM = re.compile(r"^\d{4}-\d{2}$")


def _score_of(v, pace):
    """Điểm nhịp độ -5..+5 (0 = đúng tiến độ). Ưu tiên 'score' do agent cho; thiếu thì suy từ nhãn."""
    s = v.get("score")
    try:
        s = int(round(float(s)))
        return max(-5, min(5, s))
    except (TypeError, ValueError):
        pass
    if "vượt" in pace:
        return 2
    if "chậm" in pace:
        return -3
    if "đúng" in pace:
        return 0
    return None


W_PARTS = {"A": 5, "G": 10, "C": 80, "F": 5}     # trọng số thang vòng đời chuẩn (tổng = 100)
_BASIS = {"reported", "items", "milestone"}
_STAGE = {"chưa thực hiện", "đang thực hiện", "đã hoàn thành"}   # giai đoạn dự án — tách khỏi nhịp độ
_BLOCK = {"mặt bằng", "vốn", "vật liệu", "thủ tục", "nhà thầu", "kỹ thuật", "khác"}   # nhóm vướng mắc (Tổng quan tổng hợp)


def _clean_pct(x):
    try:
        if x is None or str(x).strip() == "":
            return None
        return max(0, min(100, int(round(float(x)))))
    except (ValueError, TypeError):
        return None


def parts_of(v):
    """parts hợp lệ → {A,G,C,F} đã kẹp 0..1 (làm tròn 2 số lẻ); thiếu/sai → None."""
    pt = v.get("parts")
    if not isinstance(pt, dict):
        return None
    out = {}
    for k in W_PARTS:
        try:
            out[k] = round(max(0.0, min(1.0, float(pt.get(k)))), 2)
        except (TypeError, ValueError):
            return None
    return out


def progress_of(v):
    """(progress, parts). Có parts → TÍNH LẠI theo công thức; không có → dùng 'progress' agent trả."""
    pt = parts_of(v)
    if pt is not None:
        return int(round(sum(W_PARTS[k] * pt[k] for k in W_PARTS))), pt
    return _clean_pct(v.get("progress")), None


def _nk(s):
    return " ".join(str(s or "").split()).lower()


def item_pcts(items, v_items):
    """Khớp hạng mục theo TÊN → [(index, pct, basis)]. % hạng mục chỉ tăng (giữ mức cũ nếu agent trả thấp hơn)."""
    by = {_nk(x.get("name")): x for x in (v_items or []) if isinstance(x, dict) and x.get("name")}
    out = []
    for i, it in enumerate(items or []):
        x = by.get(_nk(it.get("name")))
        if not x:
            continue
        pct = _clean_pct(x.get("pct"))
        if pct is None:
            continue
        old = it.get("pct")
        if isinstance(old, (int, float)) and pct <= old:
            continue                                   # không giảm; bằng thì khỏi ghi
        b = (x.get("basis") or "").strip()
        out.append((i, pct, b if b in _BASIS else ""))
    return out


def run(inp, month=None, hist_only=False):
    with open(os.path.join(HERE, inp), encoding="utf-8") as f:
        verdicts = json.load(f)
    db = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)[DB]
    tr = db["Infra_Project_Tracker"]
    hist = db["Infra_Pace_History"]
    month = month or dt.date.today().strftime("%Y-%m")   # hồi tố: ghi vào tháng được chỉ định
    ops, hops, bad = [], [], 0
    n_dl = n_tmdt = n_own = n_sc = n_it = 0
    cur_items = {d["id"]: d.get("items", []) for d in tr.find(
        {"_key": "project", "id": {"$in": [int(t) for t in verdicts]}}, {"id": 1, "items": 1})}
    for tid, v in verdicts.items():
        pace = (v.get("pace") or "").strip()
        if pace not in _PACE:
            bad += 1
            continue
        st = {"paceLLM": pace, "paceConf": v.get("confidence", "")}
        why = (v.get("why") or "").strip()
        src = (v.get("src") or "").strip()
        st["paceWhyLLM"] = why                           # câu tổng hợp đa chiều — KHÔNG gắn tên báo vào lý do
        st["paceSrcLLM"] = src                           # nguồn lưu riêng
        unset = {}
        bl = v.get("blockers")
        if isinstance(bl, list):                         # nhóm vướng mắc hệ thống (enum cố định)
            st["paceBlockers"] = [b for b in dict.fromkeys(str(x).strip().lower() for x in bl) if b in _BLOCK]
        else:
            unset["paceBlockers"] = ""

        score = _score_of(v, pace)                        # điểm -5..+5, snapshot theo tháng
        prog, parts = progress_of(v)                      # % hoàn thành 0..100 (thang vòng đời chuẩn)
        stage = (v.get("stage") or "").strip()            # giai đoạn: chưa thực hiện / đang thực hiện / đã hoàn thành
        stage = stage if stage in _STAGE else ""
        if parts is not None and parts["F"] >= 1 and prog == 100:
            stage = "đã hoàn thành"                       # F = 1 và đủ 100% ⇒ đã hoàn thành
        if stage == "đã hoàn thành":                      # đã hoàn thành ⇒ LUÔN 100% (không để lệch giữa giai đoạn và %)
            prog, parts = 100, {k: 1.0 for k in W_PARTS}
        elif stage and prog is not None and prog >= 100:
            prog = 99                                     # agent nói CHƯA hoàn thành thì chưa thể 100%
        if stage in ("đã hoàn thành", "chưa thực hiện"):  # hai giai đoạn này KHÔNG chấm nhịp độ
            pace = ""
            st["paceLLM"] = ""
            score = None
        dm = (v.get("done_month") or "").strip()
        if stage:
            st["paceStage"] = stage
        else:
            unset["paceStage"] = ""
        if stage == "đã hoàn thành" and _YM.match(dm):
            st["paceDoneMonth"] = dm
        else:
            unset["paceDoneMonth"] = ""
        basis = (v.get("progress_basis") or "").strip()
        basis = basis if basis in _BASIS else ""
        if prog is not None:
            st["paceProgress"] = prog
        else:
            unset["paceProgress"] = ""
        if prog is not None and parts is not None:        # thành phần A/G/C/F + cách xác định → hiện ở trang
            st["paceParts"] = parts
            st["paceProgressBasis"] = basis
        else:
            unset["paceParts"] = ""; unset["paceProgressBasis"] = ""
        its = item_pcts(cur_items.get(int(tid)), v.get("items"))   # % từng hạng mục (khớp theo tên)
        for i, ipct, ib in its:
            st[f"items.{i}.pct"] = ipct
            st[f"items.{i}.pctBasis"] = ib
            st[f"items.{i}.pctAt"] = month
        n_it += len(its)
        if score is not None:                             # paceScore CHỈ ở $set HOẶC $unset (tránh conflict Mongo)
            st["paceScore"] = score
            n_sc += 1
        else:
            unset["paceScore"] = ""
        if score is not None or prog is not None:         # snapshot lịch sử nếu có điểm hoặc %
            hdoc = {"tid": int(tid), "month": month, "pace": pace, "why": why, "src": src,
                    "at": dt.datetime.now(dt.UTC).isoformat()}
            if score is not None:
                hdoc["score"] = score
            if prog is not None:
                hdoc["progress"] = prog
                if stage:
                    hdoc["stage"] = stage
                if parts is not None:
                    hdoc["parts"] = parts
                    hdoc["basis"] = basis
            hops.append(UpdateOne({"tid": int(tid), "month": month}, {"$set": hdoc}, upsert=True))

        dl = (v.get("deadline") or "").strip()
        if _YM.match(dl):
            st["deadlineLLM"] = dl
            st["deadlineLLMSrc"] = (v.get("deadline_src") or "").strip()
            n_dl += 1
        else:
            unset["deadlineLLM"] = ""; unset["deadlineLLMSrc"] = ""

        tm = v.get("tmdt")
        try:
            tm = int(tm) if tm is not None and str(tm).strip() != "" else None
        except (ValueError, TypeError):
            tm = None
        if tm is not None and 50 <= tm <= 5_000_000:
            st["tmdtLLM"] = tm
            st["tmdtLLMSrc"] = (v.get("tmdt_src") or "").strip()
            n_tmdt += 1
        else:
            unset["tmdtLLM"] = ""; unset["tmdtLLMSrc"] = ""

        own = (v.get("owner") or "").strip()
        if own:
            st["ownerLLM"] = own
            st["ownerLLMSrc"] = (v.get("owner_src") or "").strip()
            n_own += 1
        else:
            unset["ownerLLM"] = ""; unset["ownerLLMSrc"] = ""

        if not hist_only:               # hồi tố: CHỈ ghi lịch sử, KHÔNG đè verdict hiện tại trên tracker
            upd = {"$set": st}
            if unset:
                upd["$unset"] = unset   # rỗng → xoá field cũ để không dùng nhầm giá trị lần trước
            ops.append(UpdateOne({"_key": "project", "id": int(tid)}, upd))

    # hồi tố: ghi lịch sử kể cả khi không đụng tracker
    if hist_only:
        if hops:
            hr = hist.bulk_write(hops)
            print(f"Hồi tố {month}: ghi {hr.upserted_count + hr.modified_count} điểm · bỏ (pace sai): {bad}")
        else:
            print(f"Hồi tố {month}: không có điểm hợp lệ")
        return
    if ops:
        r = tr.bulk_write(ops)
        from collections import Counter
        dist = Counter((v.get("pace") or "(chưa đủ căn cứ)") for v in verdicts.values())
        print(f"Ghi LLM: {r.modified_count} dự án · pace {dict(dist)}")
        print(f"  có hạn: {n_dl} · có TMĐT: {n_tmdt} · có chủ đầu tư: {n_own} · có điểm: {n_sc} · % hạng mục: {n_it} · bỏ (pace sai): {bad}")
        if hops:
            hr = hist.bulk_write(hops)
            print(f"  lịch sử {month}: ghi {hr.upserted_count + hr.modified_count} điểm → Infra_Pace_History")
    else:
        print("Không có verdict hợp lệ.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="pace_verdicts.json")
    ap.add_argument("--month", default=None, help="HỒI TỐ: ghi điểm vào tháng này (YYYY-MM)")
    ap.add_argument("--hist-only", action="store_true", help="HỒI TỐ: chỉ ghi lịch sử, không đè verdict hiện tại")
    a = ap.parse_args()
    run(a.inp, a.month, a.hist_only)
