# Hướng dẫn dùng VPS Vietnix

Server riêng chạy pipeline Claude (đánh giá tiến độ, vệ tinh…) cho dự án **infra**.

| Mục | Giá trị |
|---|---|
| Nhà cung cấp | **Vietnix** (portal.vietnix.vn / my.vietnix.vn) |
| Kết nối | `ssh root@14.225.212.181` · port 22 |
| Thư mục code | `~/infra` |
| Môi trường | Python 3 + pymongo (nối thẳng Mongo, không cần MCP) · **Claude Code đăng nhập Pro/Max** (`claude -p` không tốn phí API) |

---

## 1. Kết nối SSH

```powershell
ssh root@14.225.212.181
```
- Nếu wifi công ty chặn cổng 22 → dùng **4G điện thoại** (hotspot).
- Nhập mật khẩu khi được hỏi (gõ **không hiện ký tự** là bình thường).
- Vào được → dấu nhắc đổi thành `root@...:~#`.
- Thoát: `exit`.

---

## 2. Chạy một script bất kỳ

```bash
cd ~/infra
git pull                                  # luôn lấy code mới trước khi chạy

# Chạy thường (xem trực tiếp; đóng cửa sổ là dừng):
python3 ten_script.py
./ten_script.sh

# Chạy NỀN (đóng SSH vẫn chạy tiếp) + ghi log:
nohup ./ten_script.sh > ten_script.log 2>&1 &

# Xem log khi đang chạy:
tail -f ten_script.log                    # Ctrl+C để thoát xem (script vẫn chạy)
```

- Script `.sh` báo *Permission denied* → chạy `bash ten_script.sh`, hoặc cấp quyền 1 lần: `chmod +x ten_script.sh`.
- Kiểm tra tiến trình nền: `ps aux | grep ten_script` · dừng: `kill <PID>`.

---

## 3. Lên lịch tự động (cron)

```bash
crontab -e        # sửa lịch (nano: Ctrl+O Enter Ctrl+X để lưu/thoát)
crontab -l        # xem lịch hiện có
```

Cú pháp mỗi dòng: `phút giờ * * thứ  <lệnh>` — **giờ tính theo UTC** (VN = UTC + 7).

```cron
0 11 * * 6   cd ~/infra && ./run_pace.sh >> run_pace.log 2>&1
```
→ `11h UTC` = **18h thứ 7 (VN)**. Thứ: `1`=T2 … `6`=T7, `0`=CN.

Đổi lịch nhanh (ví dụ thứ 6 → thứ 7):
```bash
crontab -l | sed 's|0 11 \* \* 5|0 11 * * 6|' | crontab -
```

---

## 4. Thêm script MỚI (quy trình chuẩn)

1. Viết script ở máy cá nhân → **commit + push** lên GitHub.
2. Trên server: `cd ~/infra && git pull`.
3. Chạy thử tay 1 lần: `python3 script_moi.py`.
4. Chạy êm → thêm vào cron (mục 3) hoặc chạy nền (mục 2).

---

## 5. Khi SSH bị "Connection timed out"

Không phải lỗi lệnh — là **server không phản hồi**. Thứ tự xử lý:

1. **Thử 4G** (loại trừ mạng công ty chặn cổng 22).
2. **Ping thử:** `ping 14.225.212.181` (nhiều VPS chặn ping nên timeout ping chưa chắc là chết — SSH timeout mới là tín hiệu chính).
3. **Vào portal Vietnix** → mục **VPS / Cloud Server**:
   - Server còn **Running** không? → nếu tắt, bấm **Start / Reboot**.
   - **IP** còn đúng `14.225.212.181`? → nếu đổi, dùng IP mới.
   - **Firewall / Security Group** có chặn cổng 22?
   - Dùng nút **Console / VNC** để vào thẳng (không qua SSH).
   - Quên mật khẩu → **Reset root password** trên portal.

---

## 6. Theo dõi tài nguyên

```bash
df -h       # dung lượng ổ đĩa
free -m     # RAM
top         # CPU / tiến trình (q để thoát)
```

Lưu ý:
- CPU/RAM/disk có hạn → script nặng nên chạy `nohup ... &` + log, tránh nhiều job cùng lúc.
- `claude -p` dùng **hạn mức gói Pro/Max** → script tốn nhiều lượt nên chạy theo lô/ban đêm, hoặc chỉ xử lý phần cần (ví dụ `run_pace.sh` mặc định chỉ chấm nhóm lỗi thời `stale_pace.json`).

---

## 7. Các script đang có

| Script | Việc | Lịch |
|---|---|---|
| `run_pace.sh` | Claude đọc tin → chấm nhịp độ (chậm/đúng/vượt) + hạn + TMĐT + chủ đầu tư | cron `0 11 * * 6` (18h T7). `./run_pace.sh` = chỉ nhóm lỗi thời · `./run_pace.sh all` = toàn bộ |
| `run_sat.sh` | Claude đọc ảnh vệ tinh → nhận định tiến triển công trường | hằng tháng |
| `qa_check.py` | Dò lỗi dữ liệu (pace lỗi thời, dự án ảo, mồ côi); `--fix` tự dọn | chạy trong CI GitHub Actions mỗi giờ |

> Site tự build lại từ Mongo mỗi giờ (GitHub Actions), **không phụ thuộc** server này. Server chỉ lo phần "Claude chấm". Server chết thì site vẫn chạy, chỉ là verdict không được làm mới tới khi server sống lại.
