import os
import re
import base64
import logging
import threading
import time
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("telegram_bot")

def parse_candidate_text(text):
    if not text:
        return {}
    data = {}
    lines = text.split("\n")
    for line in lines:
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip().lower()
            val = val.strip()
            if key in ["họ tên", "ho ten", "tên", "ten", "name", "họ và tên"]:
                data["fullName"] = val
            elif key in ["sđt", "sdt", "phone", "số điện thoại", "dienthoai"]:
                data["phone"] = val
            elif key in ["mã đơn", "ma don", "đơn", "don", "code", "order"]:
                data["orderCode"] = val.upper()
            elif key in ["ctv", "nguồn", "nguon", "cộng tác viên"]:
                data["ctvName"] = val
            elif key in ["ghi chú", "ghi chu", "note"]:
                data["note"] = val
            elif key in ["ngành", "nganh", "vị trí", "vi tri", "role"]:
                data["role"] = val

    if not data.get("fullName"):
        match_name = re.search(r"(?:Họ tên|Ho ten|Tên)\s*:\s*([^\n]+)", text, re.IGNORECASE)
        if match_name:
            data["fullName"] = match_name.group(1).strip()
    if not data.get("phone"):
        match_phone = re.search(r"(?:SĐT|SDT|Phone|Số điện thoại)\s*:\s*([0-9\s+]+)", text, re.IGNORECASE)
        if match_phone:
            data["phone"] = re.sub(r"\s+", "", match_phone.group(1).strip())
    if not data.get("orderCode"):
        match_order = re.search(r"(?:Mã đơn|Ma don|Đơn|Don|Code)\s*:\s*([A-Za-z0-9_-]+)", text, re.IGNORECASE)
        if match_order:
            data["orderCode"] = match_order.group(1).strip().upper()

    return data

def run_bot():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.info("[Telegram Bot] Chưa cấu hình TELEGRAM_BOT_TOKEN trong .env. Bot đang ở chế độ chờ.")
        return

    try:
        import telebot
        from telebot.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
    except ImportError:
        logger.error("[Telegram Bot] Chưa cài đặt thư viện pyTelegramBotAPI. Vui lòng chạy pip install pyTelegramBotAPI")
        return

    from server import MongoStore, doc_id, utc_now

    bot = telebot.TeleBot(token, parse_mode="HTML")
    logger.info("[Telegram Bot] Khởi động Telegram Bot thành công!")

    # Set Telegram Command Suggestions Menu (gõ / sẽ hiện giống ảnh)
    try:
        bot.set_my_commands([
            BotCommand("menu", "Mở bảng điều khiển tiện ích"),
            BotCommand("thongke", "Xem báo cáo số liệu tuyển dụng"),
            BotCommand("tim", "Tra cứu ứng viên (ví dụ: /tim An)"),
            BotCommand("don", "Tra cứu đơn hàng (ví dụ: /don AGT12)"),
            BotCommand("help", "Hướng dẫn cú pháp nộp hồ sơ & CV")
        ])
    except Exception as err:
        logger.warning(f"[Telegram Bot] Không thể cài đặt bot commands: {err}")

    def get_menu_keyboard():
        markup = InlineKeyboardMarkup(row_width=2)
        btn_thongke = InlineKeyboardButton("📊 Báo cáo Thống kê", callback_data="cb_thongke")
        btn_help = InlineKeyboardButton("📖 Hướng dẫn nộp UV", callback_data="cb_help")
        btn_tim = InlineKeyboardButton("🔍 Cách tìm Ứng viên", callback_data="cb_tim_guide")
        btn_don = InlineKeyboardButton("📋 Cách tra cứu Đơn", callback_data="cb_don_guide")
        markup.add(btn_thongke, btn_help, btn_tim, btn_don)
        return markup

    @bot.message_handler(commands=["menu"])
    def handle_menu(message):
        text = "<b>🎛 BẢNG ĐIỀU KHIỂN TELEGRAM BOT TPD</b>\n\nBấm các nút dưới đây để thao tác nhanh hoặc gõ các lệnh <code>/</code>:"
        bot.reply_to(message, text, reply_markup=get_menu_keyboard())

    @bot.message_handler(commands=["start", "help"])
    def send_welcome(message):
        welcome_text = (
            "<b>🤖 BOT QUẢN LÝ TUYỂN DỤNG TPD - HƯỚNG DẪN SỬ DỤNG</b>\n\n"
            "Bot tự động cập nhật ứng viên và file CV trực tiếp lên Dashboard hệ thống.\n\n"
            "📥 <b>1. CÁCH NỘP ỨNG VIÊN MỚI:</b>\n"
            "Gửi 1 tin nhắn theo định dạng chuẩn bên dưới:\n\n"
            "<code>Họ tên: Nguyễn Văn A\n"
            "SĐT: 0987654321\n"
            "Mã đơn: AGT12\n"
            "CTV: Trần Thịnh\n"
            "Ghi chú: Kinh nghiệm hàn 2 năm</code>\n\n"
            "📎 <b>2. CÁCH ĐÍNH KÈM FILE CV (PDF/Word/Ảnh):</b>\n"
            "• Chọn file CV cần gửi (hoặc ảnh chụp CV).\n"
            "• Dán đoạn cú pháp ở <b>Mục 1</b> vào phần <b>Ghi chú (Caption)</b> của file rồi gửi.\n"
            "• Bot sẽ tự động tải tệp CV lên Dashboard.\n\n"
            "🔍 <b>3. DANH SÁCH LỆNH TRA CỨU NHANH:</b>\n"
            "• <code>/menu</code> - Bảng điều khiển nút bấm tiện ích\n"
            "• <code>/tim &lt;Tên hoặc SĐT&gt;</code> - Tra cứu hồ sơ ứng viên\n"
            "• <code>/don &lt;Mã đơn&gt;</code> - Tra cứu thông tin đơn & danh sách UV\n"
            "• <code>/thongke</code> - Báo cáo số liệu đơn hàng & ứng viên\n"
        )
        bot.reply_to(message, welcome_text, reply_markup=get_menu_keyboard())

    @bot.message_handler(commands=["thongke"])
    def handle_thongke(message):
        try:
            store = MongoStore()
            dash = store.get_dashboard_data({"status": ["all"], "search": [""]}, include_heavy=False)
            metrics = dash.get("metrics", {})
            total_orders = metrics.get("totalOrders", 0)
            total_cand = metrics.get("totalCandidates", 0)
            active_ctv = metrics.get("activeCollaborators", 0)
            
            res_text = (
                "<b>📊 BÁO CÁO THỐNG KÊ HỆ THỐNG</b>\n\n"
                f"• <b>Đơn tuyển dụng đang mở:</b> {total_orders}\n"
                f"• <b>Tổng số Ứng viên:</b> {total_cand}\n"
                f"• <b>Cộng tác viên đang hoạt động:</b> {active_ctv}\n"
            )
            bot.reply_to(message, res_text)
        except Exception as e:
            bot.reply_to(message, f"❌ Lỗi khi lấy thống kê: {e}")

    @bot.callback_query_handler(func=lambda call: True)
    def handle_callback_query(call):
        try:
            if call.data == "cb_thongke":
                store = MongoStore()
                dash = store.get_dashboard_data({"status": ["all"], "search": [""]}, include_heavy=False)
                metrics = dash.get("metrics", {})
                res_text = (
                    "<b>📊 BÁO CÁO THỐNG KÊ HỆ THỐNG</b>\n\n"
                    f"• <b>Đơn tuyển dụng đang mở:</b> {metrics.get('totalOrders', 0)}\n"
                    f"• <b>Tổng số Ứng viên:</b> {metrics.get('totalCandidates', 0)}\n"
                    f"• <b>Cộng tác viên đang hoạt động:</b> {metrics.get('activeCollaborators', 0)}\n"
                )
                bot.send_message(call.message.chat.id, res_text)
            elif call.data == "cb_help":
                help_text = (
                    "<b>📌 HƯỚNG DẪN NỘP UV & CV TỰ ĐỘNG</b>\n\n"
                    "<b>1. Nộp tin nhắn Text:</b> Gửi văn bản theo cú pháp:\n"
                    "<code>Họ tên: Nguyễn Văn A\n"
                    "SĐT: 0987654321\n"
                    "Mã đơn: AGT12\n"
                    "CTV: Trần Thịnh\n"
                    "Ghi chú: Kinh nghiệm hàn 2 năm</code>\n\n"
                    "<b>2. Nộp tệp CV:</b> Chọn gửi file PDF/Word/Ảnh CV và dán cú pháp trên vào phần <b>Caption</b> của file."
                )
                bot.send_message(call.message.chat.id, help_text)
            elif call.data == "cb_tim_guide":
                bot.send_message(call.message.chat.id, "💡 Gõ cú pháp: <code>/tim Nguyen Van A</code> hoặc <code>/tim 0988123456</code> để tra cứu hồ sơ UV.")
            elif call.data == "cb_don_guide":
                bot.send_message(call.message.chat.id, "💡 Gõ cú pháp: <code>/don AGT12</code> để tra cứu chi tiết đơn hàng & danh sách UV nộp đơn.")
            bot.answer_callback_query(call.id)
        except Exception as err:
            logger.error(f"Callback error: {err}")

    @bot.message_handler(commands=["tim"])
    def handle_search_candidate(message):
        try:
            query_str = message.text.removeprefix("/tim").strip()
            if not query_str:
                bot.reply_to(message, "⚠️ Vui lòng nhập từ khóa tìm kiếm. Ví dụ: <code>/tim Nguyen Van A</code> hoặc <code>/tim 0987654321</code>")
                return

            store = MongoStore()
            cands = list(store.db.candidates.find({
                "$or": [
                    {"fullName": {"$regex": query_str, "$options": "i"}},
                    {"phone": {"$regex": query_str, "$options": "i"}}
                ]
            }).limit(5))

            if not cands:
                bot.reply_to(message, f"❌ Không tìm thấy ứng viên nào phù hợp với từ khóa: <b>{query_str}</b>")
                return

            res = [f"<b>🔍 TÌM THẤY {len(cands)} ỨNG VIÊN PHÙ HỢP:</b>\n"]
            for idx, c in enumerate(cands, 1):
                name = c.get("fullName", "N/A")
                phone = c.get("phone", "Chưa có SĐT")
                role = c.get("role", "N/A")
                stage = c.get("stage", "Chờ PV")
                res.append(f"{idx}. <b>{name}</b> | SĐT: {phone} | Ngành: {role} | Trạng thái: <b>{stage}</b>")

            bot.reply_to(message, "\n".join(res))
        except Exception as e:
            bot.reply_to(message, f"❌ Lỗi tra cứu: {e}")

    @bot.message_handler(commands=["don"])
    def handle_search_order(message):
        try:
            code = message.text.removeprefix("/don").strip().upper()
            if not code:
                bot.reply_to(message, "⚠️ Vui lòng nhập mã đơn. Ví dụ: <code>/don AGT12</code>")
                return

            store = MongoStore()
            order = store.db.orders.find_one({"code": code})
            if not order:
                bot.reply_to(message, f"❌ Không tìm thấy đơn tuyển dụng mã: <b>{code}</b>")
                return

            apps = list(store.db.applications.find({"orderId": order["_id"]}))
            cand_names = []
            for a in apps[:5]:
                cand = store.db.candidates.find_one({"_id": a.get("candidateId")}, {"fullName": 1})
                if cand:
                    cand_names.append(f"• {cand.get('fullName')} ({a.get('stage', 'N/A')})")

            res_text = (
                f"<b>📋 ĐƠN TUYỂN DỤNG: {order.get('code')}</b>\n"
                f"• <b>Vị trí:</b> {order.get('title')}\n"
                f"• <b>Loại đơn:</b> {order.get('orderType', 'Tokutei')}\n"
                f"• <b>Địa điểm:</b> {order.get('location', 'Nhật Bản')}\n"
                f"• <b>Tổng ứng viên đã nộp:</b> {len(apps)}\n\n"
                + (f"<b>Danh sách UV gần nhất:</b>\n" + "\n".join(cand_names) if cand_names else "Chưa có UV ứng tuyển.")
            )
            bot.reply_to(message, res_text)
        except Exception as e:
            bot.reply_to(message, f"❌ Lỗi tra cứu đơn: {e}")

    @bot.message_handler(content_types=["text", "document", "photo"])
    def handle_incoming_candidate_data(message):
        text_content = message.text or message.caption or ""
        parsed = parse_candidate_text(text_content)

        if not parsed.get("fullName") and not parsed.get("phone"):
            return

        full_name = parsed.get("fullName")
        phone = parsed.get("phone", "")
        order_code = parsed.get("orderCode", "")
        ctv_name = parsed.get("ctvName", "")
        note = parsed.get("note", "")
        role = parsed.get("role", "Ứng viên")

        if not full_name:
            bot.reply_to(message, "⚠️ Phát hiện dữ liệu nhưng thiếu <b>Họ tên</b> ứng viên. Vui lòng nhập dòng <code>Họ tên: ...</code>")
            return

        try:
            store = MongoStore()
            
            cv_data_url = ""
            cv_file_name = ""
            if message.document:
                file_info = bot.get_file(message.document.file_id)
                downloaded_file = bot.download_file(file_info.file_path)
                ext = Path(message.document.file_name or "cv.pdf").suffix.lower()
                mime = "application/pdf" if ext == ".pdf" else "image/jpeg"
                b64 = base64.b64encode(downloaded_file).decode("utf-8")
                cv_data_url = f"data:{mime};base64,{b64}"
                cv_file_name = message.document.file_name or "CV_Candidate.pdf"
            elif message.photo:
                file_info = bot.get_file(message.photo[-1].file_id)
                downloaded_file = bot.download_file(file_info.file_path)
                b64 = base64.b64encode(downloaded_file).decode("utf-8")
                cv_data_url = f"data:image/jpeg;base64,{b64}"
                cv_file_name = f"CV_{full_name}.jpg"

            cand_payload = {
                "fullName": full_name,
                "phone": phone,
                "role": role,
                "note": note,
                "stage": "Chờ PV",
                "status": "Đang hoạt động"
            }
            if cv_data_url:
                cand_payload["cvDataUrl"] = cv_data_url
                cand_payload["cvFileName"] = cv_file_name
                cand_payload["hasCvData"] = True

            cand_res = store.create_candidate(cand_payload)
            cand_id = cand_res.get("id")

            ctv_doc = None
            if ctv_name:
                ctv_doc = store.db.ctvs.find_one({"fullName": {"$regex": ctv_name, "$options": "i"}})
            if not ctv_doc:
                ctv_doc = store.db.ctvs.find_one({})

            order_doc = None
            if order_code:
                order_doc = store.db.orders.find_one({"code": {"$regex": order_code, "$options": "i"}})
            if not order_doc:
                order_doc = store.db.orders.find_one({})

            app_id = ""
            if order_doc and cand_id and ctv_doc:
                app_payload = {
                    "orderId": doc_id(order_doc["_id"]),
                    "candidateId": cand_id,
                    "ctvId": doc_id(ctv_doc["_id"]),
                    "stage": "Chờ PV",
                    "status": "Đang xử lý",
                    "role": role,
                    "note": note
                }
                app_res = store.create_application(app_payload)
                app_id = app_res.get("id")

            confirm_msg = (
                f"<b>✅ ĐÃ TỰ ĐỘNG THÊM ỨNG VIÊN VÀO HỆ THỐNG!</b>\n\n"
                f"• <b>Họ tên:</b> {full_name}\n"
                f"• <b>SĐT:</b> {phone or 'Chưa có'}\n"
                f"• <b>Đơn tuyển:</b> {order_doc.get('code') if order_doc else 'Chưa gán'}\n"
                f"• <b>CTV giới thiệu:</b> {ctv_doc.get('fullName') if ctv_doc else 'Chưa gán'}\n"
                f"• <b>File CV:</b> {'Đã tải lên hệ thống (' + cv_file_name + ')' if cv_data_url else 'Chưa có file'}\n\n"
                f"<i>Dữ liệu đã tự động đồng bộ lên Dashboard quản lý!</i>"
            )
            bot.reply_to(message, confirm_msg)

        except Exception as e:
            logger.error(f"[Telegram Bot Error] {e}")
            bot.reply_to(message, f"❌ Không thể lưu ứng viên tự động: {e}")

    bot.infinity_polling(timeout=20, long_polling_timeout=5)

def start_telegram_bot_thread():
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    return t

if __name__ == "__main__":
    run_bot()
