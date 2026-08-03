# Triển khai website tracker (công khai, tự động)

Kiến trúc: **GitHub repo → GitHub Actions (build từ MongoDB) → GitHub Pages → tên miền riêng.**
Mỗi lần `git push` hoặc mỗi sáng thứ Hai, CI tự dựng lại `index.html` từ DB rồi deploy.

```
   sửa DB (duyệt mark)  ─┐
   git push             ─┼─►  GitHub Actions  ──►  build_tracker.py  ──►  Pages  ──►  yourdomain.com
   cron thứ Hai 08:00   ─┘        (đọc secret IRIS_MONGO_URI)
```

---

## 0. BẢO MẬT — làm trước tiên
- [ ] **Đổi mật khẩu MongoDB** user `danvu` (mật khẩu cũ đã lộ). Atlas → Database Access → Edit → Edit Password.
- [ ] Tạo **user read-only** cho CI: Atlas → Database Access → Add New User → quyền `read` trên `dc_commodity`. CI chỉ cần đọc để build.
- [ ] Không bao giờ commit `mongo_uri.txt` (đã có trong `.gitignore`).

> ⚠️ Trang này để **công khai** nên bất kỳ ai có link đều thấy số liệu Phụ lục I của CV 5386/NHNN-TD. Nếu sau muốn giới hạn, chuyển sang Cloudflare Pages + Access (khoá email).

---

## 1. Đưa code lên GitHub
Tại `Q:\Coding\infra` (PowerShell):
```powershell
git init
git add .
git commit -m "Infra project tracker site"
git branch -M main
git remote add origin https://github.com/<user>/<repo>.git
git push -u origin main
```
`mongo_uri.txt` sẽ **không** được đẩy lên (đã gitignore). Kiểm tra: trên GitHub không thấy file này.

## 2. Khai báo secret + biến
GitHub repo → **Settings → Secrets and variables → Actions**:
- Tab **Secrets** → New repository secret:
  - Tên `IRIS_MONGO_URI`, giá trị = connection string của **user read-only** (đã đổi mật khẩu).
- Tab **Variables** → New repository variable:
  - Tên `CUSTOM_DOMAIN`, giá trị = tên miền của bạn (vd `tracker.example.com`).

## 3. Bật GitHub Pages
Settings → **Pages** → **Source** = **GitHub Actions**.

## 4. Mua & trỏ tên miền
- Mua domain: Cloudflare Registrar / Namecheap / Mắt Bão (`.vn`). ~250–300k/năm.
- Trỏ DNS:
  - **Subdomain** (khuyên dùng, vd `tracker.example.com`): thêm bản ghi **CNAME** `tracker` → `<user>.github.io`.
  - **Domain gốc** (`example.com`): thêm 4 bản ghi **A** trỏ tới GitHub Pages:
    `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`.
- Settings → Pages → **Custom domain** = nhập domain → Save (GitHub tự cấp HTTPS sau vài phút).

## 5. Chạy lần đầu
Actions → workflow **Build & Deploy tracker** → **Run workflow** (hoặc chỉ cần push lần nữa).
Xong: mở `https://<domain>` là thấy trang.

---

## Vận hành hằng ngày
- **Cập nhật tiến độ:** chạy local `python propose_marks.py` → xem đề xuất → `python propose_marks.py --promote <id> <YYYY-MM>` để duyệt mốc vào `Infra_Project_Tracker.marks`.
- Sau đó `git commit --allow-empty -m "refresh" && git push` (hoặc đợi cron thứ Hai) → site tự dựng lại từ DB.
- Nội dung trang chỉ đổi khi **mark curated** trong DB đổi (bạn duyệt). CI chỉ build + deploy, **không tự sửa nội dung** — đúng nguyên tắc "máy đề xuất, người duyệt".

## Giới hạn của "tự động hoàn toàn"
- ✅ Tự động: build tracker từ DB + deploy (cron + push).
- ⚠️ Bán tự động: bước **tin tức → weekly digest** (`update_project_news.py`) cần Claude viết narrative → hoặc mở phiên Claude Code (gói Max), hoặc đặt `ANTHROPIC_API_KEY` để chạy hẳn không người.
- ⚠️ Theo thiết kế: **promote mark là bước người duyệt** (không auto, vì là dữ liệu tín dụng — cần kiểm chứng nguồn/tier).
