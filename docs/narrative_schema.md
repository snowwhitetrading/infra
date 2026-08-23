# Hướng dẫn soạn narrative tiến độ dự án (cho routine tự động)

Mục tiêu: đọc dòng tin của dự án trong MongoDB → tổng hợp thành entry tiến độ có cấu trúc,
ghi vào `dc_commodity.Infra_Project_Tracker` (`_key='project'`). KHÔNG dùng Anthropic API —
chính agent này đọc tin và soạn.

## Nguồn dữ liệu (MongoDB, nối bằng lib_db.mongo_uri — đọc mongo_uri.txt trong repo)
- `dc_commodity.Infra_Projects_Registry`: danh sách dự án (tid, name, aliases, location, active)
- `dc_commodity.Infra_Newsflow`: dòng tin (date, source, title, projects=[tid]) — NGUỒN để soạn
- `dc_commodity.Infra_Project_Tracker`: đích ghi (`_key='project'`, 1 doc/dự án)

## Chọn dự án cần soạn/soạn lại mỗi lượt (giới hạn ~10-12 để không quá tải)
Ưu tiên dự án ACTIVE có nhiều tin nhưng entry thiếu/cũ:
- có ≥10 tin trong Infra_Newsflow, VÀ
- (chưa có entry `_key='project'`) HOẶC (entry thiếu `items`/`sched`) HOẶC (có tin mới sau
  mốc gần nhất của entry — narrative đã cũ).
Bỏ dự án thuần "chuẩn bị đầu tư" đã đủ (không có tiến độ mới).

## Schema mỗi dự án (ghi $set vào Tracker, _key='project', origin_ai=True)
```json
{
 "id": <tid int>, "name": "<tên>",
 "status": "thi công | giải phóng mặt bằng | đối ứng | chuẩn bị đầu tư | hoàn thành",
 "loc": "<địa điểm ngắn>", "owner": "<CĐT/nhà thầu nếu tin nêu, else ''>",
 "tmdt": <tỷ đồng int khi tin nêu, else null>,
 "sched": "đúng tiến độ | chậm tiến độ | vượt tiến độ | ''",
 "schedWhy": "<1 câu vì sao, dẫn mốc>", "note": "<cảnh báo/mâu thuẫn số liệu; '' nếu không>",
 "phases": [{"kind":"gpmb|build|swap","from":"YYYY-MM","to":"YYYY-MM","state":"ongoing","doneTo":"YYYY-MM"}],
 "marks": [{"date":"YYYY-MM","type":"start|ms|done","label":"<ngắn>","tier":"actual|contract|directive|company|inferred","src":"<Báo + MM/YYYY>"}],
 "items": [{"name":"<hạng mục>","prog":"<mô tả tiến độ>","phases":[...],"marks":[]}]
}
```

## Quy tắc (bắt buộc)
- **sched** không để trống nếu có tin tiến độ: lỡ mốc/gia hạn/đội vốn/vướng MB kéo dài → chậm;
  về đích sớm → vượt; bám sát lịch → đúng. Chỉ '' khi thuần chuẩn bị đầu tư.
- **items ≥2 hạng mục** khi đã thi công (GPMB · thi công chính · cầu/hầm/nhà ga/khán đài…), mỗi cái có `prog`.
- **marks 4-8 mốc chính**, mỗi mốc có `src` (báo + tháng). Nhãn VIẾT LẠI gọn, không chép tiêu đề thô.
- **phases**: ≥1 build từ khởi công → hạn hoàn thành (tìm trong tin; không có thì ước lượng theo loại
  + ghi "ước lượng" trong note). Có GPMB thì thêm phase gpmb.
- **Giai đoạn CHƯA XONG (`state:'ongoing'`) — QUAN TRỌNG để thanh không trông như đã hoàn thành:**
  - `to` = **hạn hoàn thành THỰC TẾ** (nếu đã lỡ hạn thì đẩy sang mốc dự kiến mới ở **TƯƠNG LAI**);
    KHÔNG để `to` ở quá khứ cho việc chưa xong (nếu không thanh sẽ tô kín trông như đã xong).
  - `doneTo` = **tháng có tiến độ XÁC NHẬN gần nhất** (≤ hôm nay và **≤ `to`**). KHÔNG đặt `doneTo` ≥ `to`,
    KHÔNG đặt bằng "hôm nay" nếu chưa xác nhận tới đó. Đoạn [doneTo→to] tự vẽ "đang làm" (rỗng + mũi ›).
  - Việc ĐÃ XONG hẳn: bỏ `state`/`doneTo`, đặt `to` = tháng hoàn thành thật.
- KHÔNG bịa số (tmdt/owner chỉ khi tin nêu). Mâu thuẫn số → note. Cảnh báo tin trộn dự án khác → note.
- Nhãn tiếng Việt, ngày YYYY-MM. Lấy đúng id=tid.

## Ví dụ GOLD
```json
{"id":184,"name":"Cầu Trần Hưng Đạo","status":"thi công","loc":"Cầu vượt sông Hồng, Hà Nội",
 "owner":"Đạt Phương","tmdt":16000,"sched":"đúng tiến độ",
 "schedWhy":"GPMB từng là nút thắt nhưng đã cơ bản bàn giao 5-6/2026; phần cầu tăng tốc, dựng vòm thép 8/2026.",
 "note":"TMĐT 2 con số: ~7.500 tỷ (đầu) và ~16.000 tỷ (điều chỉnh 11/2025). Hạn hoàn thành ~2027.",
 "phases":[{"kind":"gpmb","from":"2025-06","to":"2026-06","state":"ongoing","doneTo":"2026-06"},
           {"kind":"build","from":"2025-10","to":"2027-12"}],
 "marks":[{"date":"2025-10","type":"start","label":"Khởi công cầu qua sông Hồng (~16.000 tỷ)","tier":"actual","src":"VnExpress 10/2025"},
          {"date":"2026-01","type":"ms","label":"Đạt Phương trúng thầu thi công","tier":"contract","src":"VietnamBiz 1/2026"},
          {"date":"2026-06","type":"ms","label":"Cơ bản bàn giao mặt bằng","tier":"actual","src":"CafeF 6/2026"}],
 "items":[{"name":"GPMB & tái định cư","prog":"200 hộ di dời; 5-6/2026 cơ bản bàn giao.","phases":[{"kind":"gpmb","from":"2025-06","to":"2026-06"}],"marks":[]},
          {"name":"Thi công cầu chính (vòm thép)","prog":"Khởi công 10/2025; 8/2026 dựng vòm thép.","phases":[{"kind":"build","from":"2025-10","to":"2027-12"}],"marks":[]}]}
```

## Truy cập DB
- **Máy local / CI GitHub**: nối trực tiếp qua `lib_db.mongo_uri()` (pymongo) — cổng 27017 mở.
- **Cloud routine (CCR)**: cổng 27017 BỊ CHẶN → KHÔNG dùng pymongo. Dùng MCP connector `DC_Database`
  (`mongo_find`/`mongo_aggregate`/`mongo_update`, `database='dc_commodity'`, gọi trực tiếp, không qua
  `execute_python`). `mongo_update(collection, filter, {$set:...}, upsert=True)` có quyền GHI.

## Sau khi ghi Tracker
KHÔNG cần build/push từ routine — CI hàng giờ (`deploy.yml`) tự rebuild từ DB và deploy, nên entry
vừa ghi tự lên web trong ~1 giờ. (Chạy tay ở local thì `python step5_build_site.py` + commit để deploy ngay.)
