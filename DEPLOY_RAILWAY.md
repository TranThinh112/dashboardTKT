# Deploy lên Railway bằng GitHub

## 1. Push code lên GitHub

Đảm bảo các file này có trong repo:

```text
server.py
index.html
styles.css
script.js
requirements.txt
railway.json
Procfile
runtime.txt
```

Không push `.env` và `recruitment_bot.db`.

## 2. Tạo service Railway từ GitHub

Vào Railway:

```text
New Project > Deploy from GitHub repo > chọn repo RecruitmentDashboard
```

Railway sẽ tự detect Python/Nixpacks và chạy lệnh trong `railway.json`:

```bash
python server.py
```

## 3. Thêm biến môi trường

Vào service trên Railway > Variables, thêm:

```env
MONGODB_URI=mongodb+srv://<user>:<password>@<cluster-host>/?retryWrites=true&w=majority&appName=<app-name>
MONGODB_DB_NAME=recruitment_dashboard
```

Không cần thêm `PORT`. Railway tự cấp biến này khi chạy service.

## 4. Lấy link public

Sau khi deploy xong:

```text
Service > Settings > Networking > Generate Domain
```

## Ghi chú

- App sẽ tự bind `0.0.0.0:$PORT` trên Railway.
- Khi chạy local vẫn dùng cấu hình trong `.env`.
- Không upload `.env` hoặc database SQLite local.
