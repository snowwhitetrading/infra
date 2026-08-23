# -*- coding: utf-8 -*-
"""
lib_marks.py — Sinh MARK ứng viên cho tracker từ tin tức tuần.

Nguồn: dc_commodity.Infra_News_Collection_Weekly_Digest (project_status.events,
        đã quét từ 4 báo cafef/vietstock/vneconomy/nguoiquansat).
Đích:  dc_commodity.Infra_Project_Tracker_Proposed — 1 doc/dự án, mảng marks
        "chờ duyệt" (tier thấp, có src + url + week_of). KHÔNG đụng data curated.

Nguyên tắc: MÁY chỉ đề xuất (tier company/directive), NGƯỜI duyệt nâng tier
rồi promote sang Infra_Project_Tracker.marks.

  python lib_marks.py                 # sinh đề xuất + in báo cáo
  python lib_marks.py --report-only   # chỉ in, không ghi DB
  python lib_marks.py --promote 11 2026-07  # đưa 1 đề xuất (id,tháng) vào tracker
"""
import argparse, os, re, sys, datetime as dt
from pymongo import MongoClient
from lib_db import mongo_uri

MONGO_URI = mongo_uri()
DB = "dc_commodity"
SRC_COLL = "Infra_News_Collection_Weekly_Digest"
TRACKER = "Infra_Project_Tracker"
PROPOSED = "Infra_Project_Tracker_Proposed"

# Map project_id (news backfill) -> id số của tracker (CV 5386)
PID2TID = {
    "apec_center": 1, "phu_quoc_airport": 2, "bai_dat_do": 3, "nui_ong": 4,
    "phu_quoc_tram": 5, "rach_chiec": 6, "road_giabinh_hanoi": 7,
    "road_giabinh_bacninh": 8, "rail_benthanh_cangio": 9, "rail_hanoi_quangninh": 10,
    "giabinh_airport": 11, "atc_tower": 12, "bt1": 13, "bt2": 14, "bt3": 15,
    "bt1_doiung": 16, "bt2_doiung": 17, "bt3_doiung": 18,
}

RX_DIRECTIVE = re.compile(
    r"thủ tướng|phó thủ tướng|chính phủ|quốc hội|ubnd|bộ (công an|xây dựng|tài chính)|"
    r"phê duyệt|chủ trương|chỉ đạo|quyết định|nghị quyết|yêu cầu hoàn thành|chốt hạn|háº¡n",
    re.I)
RX_CONTRACT = re.compile(r"ký hợp đồng|hợp đồng dự án|trúng thầu|chọn nhà (thầu|đầu tư)", re.I)
RX_START = re.compile(r"khởi công|động thổ|bấm nút|khởi động", re.I)
RX_DONE = re.compile(r"hoàn thành|khánh thành|vận hành|về đích|thông xe|cất nóc|đưa vào khai thác", re.I)
# Ngôn ngữ Ý ĐỊNH / HẠN (đề xuất/dự kiến/sắp/yêu cầu... + mốc) = CHƯA xảy ra.
# → KHÔNG đánh 'start'/'done' (tránh chấm "đã khởi công/đã xong" giả); Dòng tin gắn nhãn 'chỉ đạo'.
RX_INTENT = re.compile(
    r"(đề xuất|kiến nghị|đề nghị|dự kiến|kế hoạch|phương án|ý tưởng|nghiên cứu|xem xét|"
    r"muốn|mong muốn|xin chủ trương|phấn đấu|mục tiêu|quyết tâm|cam kết|yêu cầu|chốt|"
    r"hạn|trước ngày|trước tháng|trong năm|đặt mục tiêu|sắp|chuẩn bị)\s+\S*\s{0,3}\S{0,18}"
    r"(khởi công|động thổ|hoàn thành|thông xe|về đích|khánh thành|đưa vào|vận hành|hợp long)", re.I)

# CHỈ giữ CỘT MỐC TIẾN ĐỘ rời rạc: khởi công, hoàn thành hạng mục, GPMB, pháp lý,
# đấu thầu/hợp đồng/góp vốn. Whitelist hẹp + blacklist tin PR/bán hàng/tài chính DN.
RX_PROGRESS = re.compile(
    r"khởi công|động thổ|bấm nút|"
    r"cất nóc|hợp long|thông xe|khánh thành|nghiệm thu|hoàn thành|hoàn tất|về đích|"
    r"thi công|mũi thi công|đẩy nhanh|tăng tốc|tiến độ|lao dầm|đúc dầm|hầm|"
    r"đường băng|lắp đủ|\d+\s*/\s*\d+\s*nhịp|mái thép.*(nhịp|lắp)|cọc khoan nhồi|"
    r"đạt\s*\d+\s*%|\d+\s*%\s*(khối lượng|tiến độ|hoàn thành|kế hoạch|thô)|"
    r"giải phóng mặt bằng|gpmb|thu hồi đất|bàn giao (mặt bằng|\d)|tái định cư|cưỡng chế|"
    r"bồi thường|đền bù|bốc thăm|kiểm kê|dỡ nhà|nhường đất|di dời|\d+[\.,]?\d*\s*/\s*\d+[\.,]?\d*\s*ha|"
    r"phê duyệt|chủ trương đầu tư|điều chỉnh (quy hoạch|dự án|chủ trương|tổng mức)|quy hoạch phân khu|duyệt quy hoạch|"
    r"định mức|dự toán|khởi động|"
    r"ký hợp đồng|đấu thầu|chọn nhà (thầu|đầu tư)|trúng thầu|chốt (doanh nghiệp|nhà đầu tư)|"
    r"chốt hạn|yêu cầu hoàn thành|góp \d+% vốn|góp vốn|chấp thuận nhà đầu tư|"
    r"triển khai (theo hình thức|tuyến|siêu dự án)",
    re.I)

# Nếu trúng các mẫu này -> BỎ, dù có khớp progress (tin PR/thị trường/tài chính DN).
RX_NOISE = re.compile(
    r"giới đầu tư|săn dự án|săn |bán hàng|mở bán|ra mắt|căn hộ|biệt thự|"
    r"lợi nhuận|báo lãi|lãi kỷ lục|cổ phiếu|tài sản.*(triệu tỷ|tỷ usd)|tỷ phú|"
    r"kỷ lục|chứng minh năng lực|kỳ tích|made in vietnam|đô thị toàn cầu|"
    r"thương hiệu|nghỉ dưỡng|du lịch|đường bay|đón (chuyến bay|tàu bay)|"
    r"room tín dụng|năng lực tài chính|hưởng lợi|đẹp nhất|sức hút|"
    r"tai nạn|va chạm|lật xe|tông|bốc cháy|cháy xe|tử vong|thương vong|"
    r"bắt tạm giam|bạo hành|khởi tố|truy tố|trộm|cướp|đánh nhau|bạo lực",
    re.I)


def is_progress(stage, summary):
    blob = (stage or "") + " " + (summary or "")
    if RX_NOISE.search(blob):
        return False
    return bool(RX_PROGRESS.search(blob))


def is_relevant(summary):
    """Tin VỀ dự án cho Dòng tin (không đòi cột mốc): chỉ bỏ rác (tai nạn/án/PR bán hàng...)."""
    return not RX_NOISE.search(summary or "")


RX_GPMB = re.compile(r"giải phóng mặt bằng|gpmb|thu hồi đất|bàn giao (mặt bằng|\d)|tái định cư|"
                     r"đền bù|bồi thường|cưỡng chế|di dời|kiểm kê|bốc thăm|dỡ nhà", re.I)
RX_APPROVE = re.compile(r"phê duyệt|chủ trương đầu tư|điều chỉnh (quy hoạch|dự án|chủ trương|tổng mức)|"
                        r"duyệt quy hoạch|quy hoạch phân khu", re.I)


def news_tag(text):
    """Nhãn loại tin để hiện cạnh tin trong tab Tiến độ ('' = tin thường)."""
    t = text or ""
    # đề xuất/dự kiến/sắp khởi công-hoàn thành = định hướng, CHƯA xảy ra → 'chỉ đạo', không 'khởi công/hoàn thành'
    if RX_INTENT.search(t) and (RX_START.search(t) or RX_DONE.search(t)):
        return "chỉ đạo"
    if RX_START.search(t):
        return "khởi công"
    if RX_DONE.search(t):
        return "hoàn thành"
    if RX_CONTRACT.search(t):
        return "trúng thầu"
    if RX_APPROVE.search(t):
        return "phê duyệt"
    if RX_GPMB.search(t):
        return "mặt bằng"
    if RX_DIRECTIVE.search(t):
        return "chỉ đạo"
    if RX_PROGRESS.search(t):
        return "tiến độ"
    return ""


def infer_tier(text):
    if RX_CONTRACT.search(text):
        return "contract"
    if RX_DIRECTIVE.search(text):
        return "directive"
    return "company"


RX_DMY = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b")            # 15/1/2026
RX_MY = re.compile(r"(?:tháng\s*)?\b(\d{1,2})[/-](20\d{2})\b", re.I)       # tháng 1/2026
RX_Q = re.compile(r"quý\s*(iv|iii|ii|i|[1-4])\s*[/-]?\s*(20\d{2})", re.I)  # quý IV/2026
RX_YP = re.compile(r"(cuối|đầu|giữa)\s*năm\s*(20\d{2})", re.I)             # cuối năm 2026
_QMO = {"i": 2, "ii": 5, "iii": 8, "iv": 11, "1": 2, "2": 5, "3": 8, "4": 11}
_YPMO = {"đầu": 2, "giữa": 6, "cuối": 12}


def event_month(text, pub_month):
    """Suy THÁNG SỰ KIỆN (YYYY-MM) từ ngày rõ trong text; CHỈ nhận mốc ≤ tháng đăng
    (sự kiện quá khứ/hiện tại — loại ngày tương lai vì đó là HẠN). Không có → '' (dùng tháng đăng)."""
    if not text or len(pub_month) < 7:
        return ""
    c = []
    for m in RX_DMY.finditer(text):
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            c.append(f"{m.group(3)}-{mo:02d}")
    for m in RX_MY.finditer(text):
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            c.append(f"{m.group(2)}-{mo:02d}")
    for m in RX_Q.finditer(text):
        c.append(f"{m.group(2)}-{_QMO[m.group(1).lower()]:02d}")
    for m in RX_YP.finditer(text):
        c.append(f"{m.group(2)}-{_YPMO[m.group(1).lower()]:02d}")
    past = [x for x in c if x <= pub_month]        # chỉ mốc đã tới (loại hạn tương lai)
    return max(past) if past else ""               # gần tháng đăng nhất = sự kiện đang được thuật


def infer_type(stage, text):
    blob = (stage or "") + " " + (text or "")
    intent = RX_INTENT.search(blob)      # đề xuất/dự kiến/sắp... + mốc = CHƯA xảy ra → 'ms'
    if RX_START.search(blob) and not intent:
        return "start"
    if (RX_DONE.search(blob) or stage in ("hoàn thành", "khai thác")) and not intent:
        return "done"
    return "ms"


def norm(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()[:60]


def collect_proposed(client):
    src = client[DB][SRC_COLL]
    tracker = {d["id"]: d for d in client[DB][TRACKER].find({"_key": "project"})}
    # tháng đã có mark curated theo từng tracker id (để đánh dấu trùng)
    curated_months = {tid: {m["date"][:7] for m in d.get("marks", [])}
                      for tid, d in tracker.items()}

    # gom event theo tracker id, dedup theo (tháng, nhãn rút gọn)
    by_tid = {}
    seen = {}
    for doc in src.find({"segment": "vsb_projects"}).sort("week_of", 1):
        wk = doc.get("week_of")
        for ps in doc.get("project_status", []):
            tid = PID2TID.get(ps.get("project_id"))
            if not tid:
                continue
            for ev in ps.get("events", []) or []:
                date = ev.get("date", "")[:10]
                if len(date) < 7:
                    continue
                label = ev.get("summary", "")
                if not is_progress(ev.get("stage"), label):
                    continue  # bỏ tin không phải cột mốc tiến độ
                month = date[:7]
                key = (tid, month, norm(label))
                if key in seen:
                    continue
                seen[key] = True
                mark = {
                    "date": month,
                    "type": infer_type(ev.get("stage"), label),
                    "label": label,
                    "tier": infer_tier((ev.get("stage") or "") + " " + label),
                    "src": f"{ev.get('source','?')} · {wk}",
                    "url": ev.get("url", ""),
                    "week_of": wk,
                    "status": "proposed",
                    "dup_month": month in curated_months.get(tid, set()),
                }
                by_tid.setdefault(tid, []).append(mark)
    # sắp mỗi dự án theo ngày
    for tid in by_tid:
        by_tid[tid].sort(key=lambda m: m["date"])
    return by_tid, tracker


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--promote", nargs=2, metavar=("ID", "MONTH"),
                    help="Đưa 1 mark đề xuất (tracker id, YYYY-MM) vào Infra_Project_Tracker.marks")
    args = ap.parse_args()

    c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=20000)
    c.admin.command("ping")

    if args.promote:
        return promote(c, int(args.promote[0]), args.promote[1])

    by_tid, tracker = collect_proposed(c)

    print("=== ĐỀ XUẤT MARK TỪ TIN TỨC (chờ duyệt) ===")
    total_new = 0
    for tid in sorted(by_tid):
        marks = by_tid[tid]
        new = [m for m in marks if not m["dup_month"]]
        total_new += len(new)
        name = tracker.get(tid, {}).get("name", f"id {tid}")
        print(f"\n[{tid:2d}] {name}  ·  {len(marks)} đề xuất ({len(new)} tháng MỚI)")
        for m in marks:
            flag = " " if m["dup_month"] else "★"
            print(f"   {flag} {m['date']} [{m['tier']:9s}/{m['type']:5s}] {m['label'][:70]}")
        if not args.report_only:
            c[DB][PROPOSED].update_one(
                {"id": tid},
                {"$set": {"id": tid, "name": name, "proposed": marks,
                          "generated_at": now_iso()}}, upsert=True)
    print(f"\nTổng: {sum(len(v) for v in by_tid.values())} đề xuất · "
          f"{total_new} thuộc tháng chưa có mark curated (★)")
    if not args.report_only:
        print(f"Đã ghi -> {DB}.{PROPOSED}. Duyệt xong dùng: "
              f"python lib_marks.py --promote <id> <YYYY-MM>")


def promote(c, tid, month):
    pdoc = c[DB][PROPOSED].find_one({"id": tid})
    if not pdoc:
        sys.exit(f"Không có đề xuất cho id {tid}")
    picks = [m for m in pdoc["proposed"] if m["date"] == month]
    if not picks:
        sys.exit(f"Không có đề xuất tháng {month} cho id {tid}")
    tracker = c[DB][TRACKER]
    added = 0
    for m in picks:
        mark = {k: m[k] for k in ("date", "type", "label", "tier", "src")}
        res = tracker.update_one(
            {"_key": "project", "id": tid,
             "marks": {"$not": {"$elemMatch": {"date": m["date"], "label": m["label"]}}}},
            {"$push": {"marks": mark}})
        added += res.modified_count
    print(f"Đã promote {added} mark (id {tid}, tháng {month}) vào {TRACKER}.marks. "
          f"Chạy step5_build_site.py để cập nhật trang.")


if __name__ == "__main__":
    main()
