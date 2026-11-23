# -*- coding: utf-8 -*-
"""
overview_commentary.py — NHẬN XÉT TỔNG QUAN + BẢNG CHÍNH SÁCH do Claude viết, TỰ CẬP NHẬT.

Vì sao: câu nhận xét dựng bằng công thức chỉ "đọc số"; muốn có nhận định (vì sao, hệ quả, cần theo dõi gì)
thì phải để Claude đọc-hiểu số liệu tổng hợp của CẢ DANH MỤC rồi viết. Tin lẻ của từng dự án không được dùng
làm nội dung chính.

Luồng (2 lần gọi `claude -p`, dùng gói Pro/Max như run_pace*.sh):
  1) CHÍNH SÁCH + BỐI CẢNH: dò tiêu đề tin gần đây có nội dung chính sách/vốn/chi phí → Claude cập nhật danh sách
     (giữ mục cũ còn hiệu lực, thêm/sửa mục mới) → Infra_Overview {_id:'policy'} và {_id:'macro'}.
  2) NHẬN XÉT: số liệu tổng hợp (compute_stats — CÙNG dữ liệu và quy tắc với trang, qua step5.build_projects)
     + chính sách + bối cảnh → Claude viết 6 mục → Infra_Overview {_id:'commentary'}.
step5_build_site.py đọc Infra_Overview đưa vào hằng OVERVIEW của trang.

  python overview_commentary.py            # chạy cả 2 bước, ghi Mongo
  python overview_commentary.py --dry      # không ghi Mongo, in kết quả
  python overview_commentary.py --stats    # chỉ in số liệu tổng hợp (không gọi Claude)
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
from collections import Counter, defaultdict

from pymongo import MongoClient

from lib_db import mongo_uri

HERE = os.path.dirname(os.path.abspath(__file__))
DB = "dc_commodity"
COLL = "Infra_Overview"
SECTIONS = ("tq", "kh", "gv", "xh", "vm", "tg")
_YM = re.compile(r"^\d{4}-\d{2}$")
RX_POLICY = re.compile(r"nghị quyết|nghị định|thông tư|luật |dự thảo|cơ chế|chính sách|quy hoạch tổng thể|trái phiếu|"
                       r"lãi suất|tín dụng|đầu tư công|\bPPP\b|đối tác công tư|giá vật liệu|định mức|đơn giá|"
                       r"giải phóng mặt bằng|đất đai|mỏ vật liệu|xếp hạng tín nhiệm|ngân sách", re.I)


# ------------------------------------------------------------------ tiện ích thời gian (chỉ số tháng)
def mi(ym):
    return int(ym[:4]) * 12 + int(ym[5:7]) - 1


def ym_vn(ym):
    return f"{int(ym[5:7])}/{ym[:4]}"


# ------------------------------------------------------------------ số liệu tổng hợp (cùng quy tắc với trang)
def compute_stats(client):
    import step5_build_site as s5
    projects, _groups, tid2cat = s5.build_projects(client)
    nf = Counter(n["pname"] for n in s5.fetch_newsflow(client, projects, tid2cat))
    hist = s5.fetch_pace_history(client)                      # {tid: [[month, score, progress], ...]}
    disb = s5.fetch_disbursement(client)
    today = dt.date.today()
    now_m = today.strftime("%Y-%m")
    now_i = mi(now_m) + (today.day - 1) / 31
    today_y = today.year + (today.month - 1 + (today.day - 1) / 31) / 12

    def to_y(s):
        try:
            return int(s[:4]) + (int(s[5:7]) - 1) / 12
        except (TypeError, ValueError, IndexError):
            return 2024.0

    def prog_series(p):                                       # [(month, % đã ép đơn điệu)]
        out, mx = [], None
        for m, _s, pr in sorted(hist.get(str(p["id"]), [])):
            if pr is None:
                continue
            mx = pr if mx is None else max(mx, pr)
            out.append((m, mx))
        return out

    def prog_latest(p):
        ser = prog_series(p)
        return ser[-1][1] if ser else p.get("paceProgress")

    def stage(p):
        pr = prog_latest(p)
        if p.get("proposal"):
            return "notyet"
        if pr is not None and pr >= 100:
            return "done"
        ps = p.get("paceStage")
        if ps == "đã hoàn thành":
            return "done"
        if ps == "chưa thực hiện":
            return "notyet"
        if ps == "đang thực hiện":
            return "active"
        if p.get("status") == "hoàn thành" and (pr is None or pr >= 90):
            return "done"
        if p.get("status") == "chuẩn bị đầu tư":
            return "notyet"
        return "active"

    def done_month(p):
        if _YM.match(p.get("paceDoneMonth") or ""):
            return p["paceDoneMonth"]
        dm = sorted(str(m["date"])[:7] for m in p.get("marks", [])
                    if m.get("type") == "done" and m.get("tier") == "actual" and m.get("date"))
        if dm:
            return dm[-1]
        ser = prog_series(p)
        k = next((m for m, v in ser if v >= 100), None)
        return k if k and k != ser[0][0] else ""

    def old_done(p):
        if stage(p) != "done":
            return False
        m = done_month(p)
        return not m or int(m[:4]) < today.year

    def pace(p):                                              # 'chậm' | 'đúng' | 'vượt' | 'na'
        st = stage(p)
        if st != "active":
            return "na"
        lab = p.get("paceAuto") or ""
        if not lab:                                           # suy từ lịch thi công (như schedOf của trang)
            phz = p.get("phases", [])
            bph = [ph for ph in phz if ph.get("kind") not in ("gpmb", "swap")]
            froms = [to_y(ph["from"]) for ph in bph if ph.get("from")]
            tos = [to_y(ph["to"]) for ph in bph if ph.get("to")]
            started = any(ph.get("from") and to_y(ph["from"]) <= today_y for ph in phz)
            if started:
                lab = "chậm" if (tos and max(tos) < today_y) else ("đúng" if (tos or froms) else "")
            elif froms and min(froms) < today_y:
                lab = "chậm"
        return "vượt" if "vượt" in lab else "chậm" if "chậm" in lab else "đúng" if "đúng" in lab else "na"

    def dl(p):
        d = sorted(m["date"] for m in p.get("marks", []) if m.get("tier") == "deadline" and m.get("date"))
        return d[-1][:7] if d else ""

    def dl_eff(p):
        d, n = dl(p), p.get("deadlineNews") or ""
        return n if (_YM.match(n) and (not d or n > d)) else d

    def plan_now(p):                                          # mức kế hoạch hôm nay (mốc cam kết → nội suy; không thì chữ S)
        if p.get("autoRow") or p.get("proposal"):
            return None
        raw = sorted((mi(x["month"]) + 1, max(0, min(100, x["pct"]))) for x in (p.get("pacePlanPoints") or [])
                     if isinstance(x, dict) and _YM.match(x.get("month") or "") and isinstance(x.get("pct"), (int, float)))
        e = dl_eff(p)
        end100 = [x for x in raw if x[1] >= 100]
        if not e and not end100:
            return None
        fr = sorted(ph["from"] for ph in p.get("phases", []) if ph.get("from") and ph.get("kind") != "swap")
        t1 = mi(e) + 1 if e else end100[0][0]
        t0 = mi(fr[0]) if fr else (raw[0][0] - 1 if raw else None)
        if t0 is None or t1 - t0 < 3:
            return None
        if now_i < t0:
            return None
        if now_i >= t1:
            return 100.0
        nodes = [(t0, 5.0)]
        for x, v in raw:
            if x <= t0 or x >= t1 or v <= 5 or v >= 100 or v < nodes[-1][1]:
                continue
            if x == nodes[-1][0]:
                nodes[-1] = (x, max(v, nodes[-1][1]))
            else:
                nodes.append((x, v))
        nodes.append((t1, 100.0))
        if len(nodes) > 2:
            for (xa, va), (xb, vb) in zip(nodes, nodes[1:]):
                if now_i <= xb:
                    return va + (vb - va) * (now_i - xa) / (xb - xa)
        u = (now_i - t0) / (t1 - t0)
        return 5 + 95 * (3 * u * u - 2 * u ** 3)

    def slip_m(p):
        f, e = p.get("paceDeadlineFirst") or "", dl_eff(p)
        return mi(e) - mi(f) if (_YM.match(f) and e and f < e) else 0

    def score_delta(p):
        h = [x for x in sorted(hist.get(str(p["id"]), [])) if x[1] is not None]
        return (h[-1][1] - h[max(0, len(h) - 4)][1]) if len(h) >= 2 else None

    def disb_stat(p):
        d = disb.get(str(p["id"]))
        m = re.match(r"(\d+)T(\d{4})$", (d or {}).get("as_of") or "")
        if not d or d.get("pct") is None or (d.get("kh") or 0) < 500 or not m:
            return None
        return {"pct": float(d["pct"]), "time": int(m.group(1)) / 12 * 100, "kh": d.get("kh") or 0, "gn": d.get("gn") or 0,
                "ky": f"{m.group(1)}T{m.group(2)}"}

    def shown(p):            # cùng quy tắc _shown() của trang: ≥5 tin, hoặc dự án lớn (TMĐT ≥10.000 tỷ) có ≥2 tin
        n = nf.get(p["name"], 0)
        try:
            big = float(p.get("tmdt") or 0) >= 10000
        except (TypeError, ValueError):
            big = False
        return n >= 5 or (n >= 2 and big)
    vis = [p for p in projects if shown(p) and not old_done(p)]
    cap = lambda L: sum(p.get("tmdt") or 0 for p in L)        # noqa: E731
    K = lambda v: round(v / 1000)                             # noqa: E731  (nghìn tỷ đồng)
    pct = lambda a, b: round(a / b * 100) if b else 0         # noqa: E731
    names = lambda L, n=3: [p["name"] for p in sorted(L, key=lambda p: -(p.get("tmdt") or 0))[:n]]   # noqa: E731

    act = [p for p in vis if stage(p) == "active"]
    slow = [p for p in vis if pace(p) == "chậm"]
    rated = [p for p in vis if pace(p) != "na"]

    def bucket(L):
        r = [p for p in L if pace(p) != "na"]
        return {"so_du_an": len(L), "von_nghin_ty": K(cap(L)),
                "pct_von_cham": pct(cap([p for p in r if pace(p) == "chậm"]), cap(r)),
                "pct_von_dung": pct(cap([p for p in r if pace(p) == "đúng"]), cap(r)),
                "so_cham": sum(1 for p in r if pace(p) == "chậm")}

    def form(p):
        return "PPP" if p.get("ppp") else ("Công" if p.get("owner") == "Nhà nước" else "Tư")

    def typ(p):
        g = p.get("g") or ""
        return g if g in ("Đường bộ", "Đường sắt/Metro", "Sân bay") else "Loại hình khác"

    gaps = {}
    for p in act:
        pr, pl = prog_latest(p), plan_now(p)
        if pr is not None and pl is not None:
            gaps[p["id"]] = round(pr - pl)
    cmp_ = [p for p in act if p["id"] in gaps]
    over = [p for p in cmp_ if dl_eff(p) and now_i > mi(dl_eff(p)) + 1]
    soon = [p for p in cmp_ if p not in over and dl_eff(p) and mi(dl_eff(p)) + 1 - now_i <= 12 and (prog_latest(p) or 0) < 70]

    def mom(p):                                               # (điểm tăng 3 tháng, số tháng đi ngang)
        ser = prog_series(p)
        if len(ser) < 4:
            return None
        flat = 0
        for (_, a), (_, b) in zip(reversed(ser[:-1]), reversed(ser[1:])):
            if a == b:
                flat += 1
            else:
                break
        return ser[-1][1] - ser[-4][1], flat

    tr = [p for p in act if mom(p)]
    up = [p for p in act if (score_delta(p) or 0) >= 1]
    dn = [p for p in act if (score_delta(p) or 0) <= -1]
    wd = [(p, disb_stat(p)) for p in act if disb_stat(p)]
    low = [p for p, d in wd if d["pct"] - d["time"] < -30]
    sat = [p for p in act if p.get("satObs") and p.get("satTrend") not in (None, "", "không rõ")
           and p.get("satConf") != "thấp" and (p.get("tmdt") or 0) >= 10000]
    blk, rem = defaultdict(list), defaultdict(list)
    for p in slow:
        for b in p.get("paceBlockers") or []:
            blk[b].append(p)
        for b in p.get("paceRemedies") or []:
            rem[b].append(p)
    mb = [p for p in slow if "mặt bằng" in (p.get("paceBlockers") or []) and isinstance((p.get("paceParts") or {}).get("G"), (int, float))]
    slips = [p for p in vis if slip_m(p)]
    owners = defaultdict(list)
    for p in vis:
        if p.get("owner") and p["owner"] != "Nhà nước":
            owners[p["owner"]].append(p)
    big = sorted(vis, key=lambda p: -(p.get("tmdt") or 0))

    def brief(p):
        return {k: v for k, v in {
            "ten": p["name"], "loai": typ(p), "hinh_thuc": form(p), "chu_dau_tu": p.get("owner"),
            "von_nghin_ty": K(p.get("tmdt") or 0), "giai_doan": {"done": "đã hoàn thành", "notyet": "chưa thực hiện", "active": "đang thực hiện"}[stage(p)],
            "nhip_do": pace(p) if pace(p) != "na" else None, "pct_hoan_thanh": prog_latest(p),
            "lech_ke_hoach_diem": gaps.get(p["id"]), "han": dl_eff(p) or None, "lui_han_thang": slip_m(p) or None,
            "vuong_mac": p.get("paceBlockers") or None, "thao_go": p.get("paceRemedies") or None,
            "ly_do": (p.get("paceWhy") or "")[:260] or None}.items() if v not in (None, "", [])}

    stats = {
        "ngay": today.isoformat(),
        "danh_muc": {"so_du_an": len(vis), "tong_von_nghin_ty": K(cap(vis)),
                     "dang_thuc_hien": len(act), "chua_thuc_hien": {"so": sum(1 for p in vis if stage(p) == "notyet"),
                                                                   "von_nghin_ty": K(cap([p for p in vis if stage(p) == "notyet"]))},
                     "hoan_thanh_trong_nam": sum(1 for p in vis if stage(p) == "done")},
        "nhip_do_theo_von": {"pct_cham": pct(cap(slow), cap(rated)), "pct_dung": pct(cap([p for p in rated if pace(p) == "đúng"]), cap(rated)),
                             "pct_vuot": pct(cap([p for p in rated if pace(p) == "vượt"]), cap(rated)),
                             "so_cham": len(slow), "so_dung": sum(1 for p in rated if pace(p) == "đúng"), "so_vuot": sum(1 for p in rated if pace(p) == "vượt")},
        "tap_trung": {"du_an_lon_nhat": big[0]["name"] if big else None, "pct_tong_von": pct(big[0].get("tmdt") or 0, cap(vis)) if big else 0,
                      "ba_du_an_cham_lon_nhat": names(slow), "pct_von_cham": pct(cap(sorted(slow, key=lambda p: -(p.get("tmdt") or 0))[:3]), cap(slow))},
        "theo_hinh_thuc": {k: bucket([p for p in vis if form(p) == k]) for k in ("Công", "Tư", "PPP")},
        "theo_loai_hinh": {k: bucket([p for p in vis if typ(p) == k]) for k in ("Đường bộ", "Đường sắt/Metro", "Sân bay", "Loại hình khác")},
        "theo_chu_dau_tu_tu_nhan": {o: bucket(L) for o, L in sorted(owners.items(), key=lambda kv: -cap(kv[1]))[:8]},
        "so_voi_ke_hoach": {"so_du_an_so_sanh_duoc": len(cmp_), "thap_hon_tu_10_diem": sum(1 for p in cmp_ if gaps[p["id"]] <= -10),
                            "di_truoc_tu_5_diem": sum(1 for p in cmp_ if gaps[p["id"]] >= 5),
                            "lech_trung_vi_diem": round(statistics.median(gaps.values())) if gaps else None,
                            "qua_han": {"so": len(over), "von_nghin_ty": K(cap(over)), "lon_nhat": names(over)},
                            "han_trong_12_thang_duoi_70pct": {"so": len(soon), "lon_nhat": names(soon)},
                            "khong_co_han_co_nguon": len(act) - len(cmp_)},
        "xu_huong_3_thang": {"so_du_an_co_chuoi": len(tr), "di_ngang_tu_3_thang": sum(1 for p in tr if mom(p)[1] >= 3),
                             "tang_tu_5_diem": sum(1 for p in tr if mom(p)[0] >= 5),
                             "nhip_do_cai_thien": {"so": len(up), "lon_nhat": names(up)}, "nhip_do_xau_di": {"so": len(dn), "lon_nhat": names(dn)}},
        "giai_ngan": ({"so_du_an": len(wd), "pct_ke_hoach_von_nam": pct(sum(d["gn"] for _, d in wd), sum(d["kh"] for _, d in wd)),
                       "pct_thoi_gian_nam_da_qua": round(sum(d["time"] for _, d in wd) / len(wd)),
                       "thap_hon_nhip_thoi_gian_tren_30_diem": len(low), "trong_do_bi_cham_cham": sum(1 for p in low if pace(p) == "chậm"),
                       "duoc_cham_dung_nhung_giai_ngan_thap": names([p for p in low if pace(p) in ("đúng", "vượt")])} if wd else None),
        "ve_tinh": ({"so_du_an_co_nhan_xet_ro": len(sat), "tien_trien": sum(1 for p in sat if p["satTrend"] == "tiến triển"),
                     "it_thay_doi": sum(1 for p in sat if p["satTrend"] != "tiến triển"),
                     "bi_cham_cham_nhung_van_tien_trien": names([p for p in sat if p["satTrend"] == "tiến triển" and pace(p) == "chậm"])} if sat else None),
        "vuong_mac_o_du_an_cham": {b: {"so": len(L), "pct_von_cham": pct(cap(L), cap(slow)), "lon_nhat": names(L, 2)}
                                   for b, L in sorted(blk.items(), key=lambda kv: -cap(kv[1]))},
        "thao_go": {"bien_phap_dang_ap_dung": {b: len(L) for b, L in sorted(rem.items(), key=lambda kv: -len(kv[1]))},
                    "mat_bang": {"so_du_an_cham_con_vuong": len(mb), "da_ban_giao_tu_90pct": sum(1 for p in mb if p["paceParts"]["G"] >= 0.9),
                                 "con_duoi_70pct": names([p for p in mb if p["paceParts"]["G"] < 0.7])},
                    "da_lui_han": {"so": len(slips), "trung_vi_thang": round(statistics.median(slip_m(p) for p in slips)) if slips else None,
                                   "lon_nhat": names(slips)},
                    "du_an_cham_van_tang_tu_5_diem": sum(1 for p in slow if mom(p) and mom(p)[0] >= 5)},
        "du_an_trong_diem": [brief(p) for p in big[:22]],
    }
    return stats


# ------------------------------------------------------------------ gọi Claude
def ask_claude(prompt, payload):
    data = json.dumps(payload, ensure_ascii=False)
    if os.name == "nt":                                       # Windows: `claude` là file .cmd, đường dẫn có dấu ngoặc làm cmd.exe lỗi
        import tempfile                                       # → chạy qua bash (Git Bash) giống run_pace*.sh
        d = tempfile.mkdtemp(prefix="ovc_")
        fp, fd = os.path.join(d, "prompt.txt"), os.path.join(d, "data.json")
        open(fp, "w", encoding="utf-8").write(prompt)
        open(fd, "w", encoding="utf-8").write(data)
        cmd = ["bash", "-c", 'claude -p "$(cat "$1")" --output-format text < "$2"', "_",
               fp.replace("\\", "/"), fd.replace("\\", "/")]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=HERE, timeout=900)
    else:
        exe = shutil.which("claude")
        if not exe:
            sys.exit("Không thấy lệnh `claude` — cần Claude Code CLI (npm i -g @anthropic-ai/claude-code).")
        r = subprocess.run([exe, "-p", prompt, "--output-format", "text"], input=data,
                           capture_output=True, text=True, encoding="utf-8", cwd=HERE, timeout=900)
    m = re.search(r"\{.*\}", r.stdout or "", re.S)
    if not m:
        raise RuntimeError("Claude không trả JSON: " + (r.stdout or r.stderr or "")[:300])
    return json.loads(m.group(0))


def policy_candidates(client, days=75, cap_n=90):
    cut = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    seen, out = set(), []
    for d in client["dc_news"]["project_news_raw"].find({"date": {"$gte": cut}},
                                                        {"title": 1, "description": 1, "date": 1, "source": 1}).sort("date", -1):
        t = d.get("title") or ""
        if not RX_POLICY.search(t) or t[:70] in seen:         # chỉ là bước CHỌN tin; phán đoán do Claude
            continue
        seen.add(t[:70])
        out.append({"date": (d.get("date") or "")[:10], "source": d.get("source", ""), "title": t,
                    "desc": (d.get("description") or "")[:200]})
        if len(out) >= cap_n:
            break
    return out


def seed_context():
    """Mục khởi tạo từ overview_context.json (chỉ dùng khi Mongo chưa có gì)."""
    p = os.path.join(HERE, "overview_context.json")
    if not os.path.exists(p):
        return [], []
    c = json.load(open(p, encoding="utf-8"))
    pol = [{"title": x["title"], "date": x.get("date", ""), "status": "", "effect": x.get("effect", ""), "relieves": []} for x in c.get("policy", [])]
    mac = [{"label": x["label"], "value": x.get("value", ""), "note": x.get("note", ""), "as_of": x.get("as_of", "")} for x in c.get("macro", [])]
    return pol, mac


def run(dry=False, stats_only=False, no_policy=False):
    client = MongoClient(mongo_uri(), serverSelectionTimeoutMS=20000)
    coll = client[DB][COLL]
    stats = compute_stats(client)
    if stats_only:
        print(json.dumps(stats, ensure_ascii=False, indent=1))
        return
    now = dt.datetime.now(dt.UTC).isoformat()
    cur = {d["_id"]: d for d in coll.find({})}
    pol = (cur.get("policy") or {}).get("items") or []
    mac = (cur.get("macro") or {}).get("items") or []
    if not pol and not mac:
        pol, mac = seed_context()

    if not no_policy:                                         # 1) chính sách + bối cảnh
        news = policy_candidates(client)
        out = ask_claude(open(os.path.join(HERE, "policy_agent_prompt.txt"), encoding="utf-8").read(),
                         {"hom_nay": stats["ngay"], "chinh_sach_hien_co": pol, "boi_canh_hien_co": mac, "tin": news})
        np_ = [x for x in (out.get("policy") or []) if isinstance(x, dict) and x.get("title") and x.get("effect")][:14]
        nm = [x for x in (out.get("macro") or []) if isinstance(x, dict) and x.get("label") and x.get("value")][:6]
        if np_:
            pol = np_
        if nm:
            mac = nm
        print(f"Chính sách: {len(pol)} mục · bối cảnh: {len(mac)} mục (từ {len(news)} tiêu đề tin)")
        if not dry:
            coll.update_one({"_id": "policy"}, {"$set": {"items": pol, "at": now}}, upsert=True)
            coll.update_one({"_id": "macro"}, {"$set": {"items": mac, "at": now}}, upsert=True)

    out = ask_claude(open(os.path.join(HERE, "overview_agent_prompt.txt"), encoding="utf-8").read(),
                     {"so_lieu": stats, "chinh_sach": pol, "boi_canh": mac})
    sec = {k: str((out.get("sections") or {}).get(k) or "").strip() for k in SECTIONS}
    if sum(1 for v in sec.values() if len(v) > 80) < 5:
        raise RuntimeError("Nhận xét thiếu mục: " + json.dumps(out, ensure_ascii=False)[:300])
    doc = {"sections": sec, "headline": str(out.get("headline") or "").strip(), "at": now, "as_of": stats["ngay"]}
    print("Nhận xét: " + " · ".join(f"{k} {len(v)} ký tự" for k, v in sec.items()))
    if dry:
        print(json.dumps({"policy": pol, "macro": mac, "commentary": doc}, ensure_ascii=False, indent=1))
    else:
        coll.update_one({"_id": "commentary"}, {"$set": doc}, upsert=True)
        print(f"Đã ghi {DB}.{COLL} (policy, macro, commentary).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--no-policy", action="store_true")
    a = ap.parse_args()
    run(a.dry, a.stats, a.no_policy)
