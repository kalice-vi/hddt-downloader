# HDDT Downloader — tải hóa đơn điện tử hoadondientu.gdt.gov.vn

Mini app (Tkinter + Playwright) tự động tra cứu và tải hóa đơn điện tử từ cổng thuế
điện tử quốc gia, chạy hoàn toàn trên máy của bạn.

## Cách dùng — chỉ có 1 file
**Nhấp đôi `hddt.bat`.**
- Lần đầu (thiếu môi trường): nó tự cài Playwright + Chromium (cần mạng, vài phút) rồi mở app.
- Từ lần sau: mở app ngay, không cài lại.

## Cài đặt trên máy MỚI
Hai cách, chọn 1:

**Cách A (khuyên dùng) — dùng file zip gói sẵn:**
1. Copy `HDDT_Download_Setup.zip` sang máy mới, giải nén ra 1 thư mục.
2. Nhấp đôi `hddt.bat` → **tự cài toàn bộ** (Python nếu thiếu, Playwright, Chromium). Cần mạng Internet.
3. Đăng nhập tài khoản thuế của người dùng máy đó.

**Cách B — copy nguyên folder `D:\HDDT_Tool`:**
- Được, nhưng **KHÔNG copy kèm thư mục `.venv`** (141MB) và **`pw_profile\`** (32MB) vì:
  - `.venv` ghi đường dẫn Python của máy cũ → máy mới giữ lại sẽ hỏng; bỏ đi để `hddt.bat` tự cài lại.
  - `pw_profile\` chứa **phiên đăng nhập của tài khoản bạn** — nếu copy, người khác truy cập hóa đơn của bạn; và cookie `TS` của Akamai ràng buộc máy nên vẫn phải đăng nhập lại.
  - `secret.mem` mã hóa bằng DPAPI của tài khoản Windows máy bạn → máy mới không đọc được, họ đăng nhập lại là xong.
- Nếu vô tình copy cả `.venv` hỏng, **không sao**: `hddt.bat` tự phát hiện venv chết và **tự xóa + cài lại**.
- Nếu máy mới **không có Python sẵn**, `hddt.bat` tự tải `uv` và cài Python quản lý của nó — **không cần bạn cài Python riêng**.

## Các tính năng
1. **Đăng nhập trong app** (không mở trình duyệt):
   - Nhập MST + mật khẩu → bấm **"Lấy mã captcha"** → ảnh captcha hiện ngay trong cửa sổ
     (không popup, không lưu ảnh) → gõ mã → **Đăng nhập**.
2. **Ghi nhớ đăng nhập** (tích chọn): MST + mật khẩu được mã hóa bằng DPAPI (Windows),
   lưu trong file `secret.mem` — không phải văn bản rõ, chỉ tài khoản Windows hiện hành
   đọc được. Lần mở sau tự điền sẵn.
3. **Chọn điều kiện tải** (giống giao diện taihoadon):
   - **Loại hóa đơn** (tích ≥1): `Mua vào` / `Bán ra`
   - **Hình thức** (tích ≥1): `Hóa đơn điện tử` / `Hóa đơn máy tính tiền`
   - **MST người bán** (không bắt buộc, chỉ mua vào) — lọc cục bộ theo MST.
4. **Chọn kỳ** `MM/YYYY` từ → đến — app tự lặp theo từng tháng (server chỉ cho tối đa 1
   tháng/lần).
5. **Tích "Chỉ lấy file XML"**: sau khi tải, tự **extract `invoice.xml`** từ file, **đổi
   tên** theo `YYYYMM_KýHiệu_Số.xml`, và **không để lại** các file thừa (html/js/ảnh/zip)
   — chỉ thao tác đúng file của hóa đơn đó, không đụng file hóa đơn khác.
6. **Chọn folder lưu** → **Bắt đầu tải**.
7. **Tải song song** (tối đa 4 file cùng lúc) với **log dạng bảng**: mỗi file 1 dòng
   `đang xử lý` → cập nhật `✓ tải xong` / `❌ lỗi` ngay khi xong. File lỗi không chặn
   các file khác. **Trỏ chuột vào dòng lỗi → hiện icon `↻ Thử lại`**, bấm để tải lại
   (icon tự ẩn khi rời chuột). Cuối đợt có **tổng kết** + danh sách hóa đơn cần tải tay.

## Lưu ý kỹ thuật
- Cổng thuế dùng **Akamai WAF** chặn HTTP trực tiếp của `requests`/`urllib` (403) kể cả
  có token — nên app mở một **Chromium ẩn** (Playwright) và gọi API bằng `fetch` trong
  page-context, profile riêng của app. Đăng nhập bằng captcha ngay trong cửa sổ app.
- Giới hạn 1 tháng là do server, **không bypass được**; app tự lặp từng tháng.
- Server đôi khi trả mã 504 (nghẽn): export tự thử lại tối đa 3 lần; file nào vẫn lỗi
  sẽ hiện đỏ và liệt kê để tải tay.

## Tính năng mới (v1.2.0 — clone từ repo dieutx/TaiHoaDonDienTu)
- **Tự giải CAPTCHA**: bấm "Lấy mã captcha" là app tự đọc mã (đọc path SVG) và điền sẵn — chỉ cần bấm Đăng nhập. (module `hddt_captcha.py`)
- **Lọc trạng thái HĐ (tthai) + kết quả kiểm tra (ttxly)**: chọn trong form để tra cứu đúng nhóm. (module `hddt_filter.py`)
- **Hóa đơn liên quan / chuỗi thay thế–điều chỉnh**: tích chọn để tải kèm, lưu file `*_lienquan.json`. (module `hddt_related.py`)
- **Tải bổ sung (chống trùng)**: tích chọn để bỏ qua hóa đơn đã tải (lưu trạng thái `hddt_state.json`). (module `hddt_resume.py`)

## Xử lý sự cố
- Nếu app báo thiếu thư viện/chromium, **xóa thư mục `.venv`** rồi mở lại `hddt.bat` để cài lại từ đầu.

## Cấu trúc
- `hddt.bat`     — file duy nhất cần chạy (tự cài khi thiếu, mở app khi đủ).
- `hddt_app.py`  — ứng dụng chính.
- `pw_profile\`  — profile Chromium của app (tạo sau lần chạy đầu).
- `secret.mem`   — MST + mật khẩu mã hóa DPAPI (tạo khi tích "Ghi nhớ đăng nhập").