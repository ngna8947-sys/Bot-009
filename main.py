import json, logging, time, os, sys, subprocess, threading, io
import telebot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from flask import Flask, jsonify

# ─── Auto-install deps ───
def _ensure_deps():
    pkgs = {"PIL": "pillow", "qrcode": "qrcode"}
    for mod, pkg in pkgs.items():
        try: __import__(mod)
        except ImportError:
            subprocess.run([sys.executable, "-m", "pip", "install", pkg,
                            "--break-system-packages", "-q"], check=False)
_ensure_deps()

import qrcode
from PIL import Image, ImageDraw, ImageFont

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════
BOT_TOKEN          = "8819304095:AAHKZRYR2sEr5nLB0wI0O3ti-D_eq1kXmBM"
ADMIN_ID           = 5915683588

# Bakong KHQR
BAKONG_TOKEN       = "rbkMVUSQPooaey51jm1cD5ECnzmHyeNX7fBX4Afc16GU8k"
BANK_ACCOUNT       = "samnang_mon@bkrt"
MERCHANT_NAME      = "Smey Lov"
MERCHANT_CITY      = "Phnom Penh"
DEPOSIT_EXPIRE_SEC = 300  # ៥ នាទី
POLL_INTERVAL      = 5

# ═══════════════════════════════════════════════════════════
#  FILES & STATE
# ═══════════════════════════════════════════════════════════
WALLETS_FILE   = "aio_wallets.json"
USERS_FILE     = "aio_users.json"
MOVIES_FILE    = "movies_db.json"
STORE_DEP_FILE = "aio_store_deposits.json"

def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return default

def _save(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e: logger.error(f"Save {path}: {e}")

wallets    = _load(WALLETS_FILE, {})
users_db   = _load(USERS_FILE, {})
movies_db  = _load(MOVIES_FILE, {})
store_deps = _load(STORE_DEP_FILE, {})
waiting    = {}

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

def bal(uid): return float(wallets.get(str(uid), 0.0))
def add_bal(uid, amt):
    wallets[str(uid)] = round(bal(uid) + amt, 2)
    _save(WALLETS_FILE, wallets)
def ded_bal(uid, amt):
    wallets[str(uid)] = max(0.0, round(bal(uid) - amt, 2))
    _save(WALLETS_FILE, wallets)

# ═══════════════════════════════════════════════════════════
#  GENUINE BAKONG EMVCO KHQR BUILDER (ACCORDING TO NBC SPECS)
# ═══════════════════════════════════════════════════════════
def _calc_crc16_ccitt(data_bytes):
    crc = 0xFFFF
    for b in data_bytes:
        crc ^= (b << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"

def _tag(tag_id, value):
    val_str = str(value)
    return f"{tag_id:02d}{len(val_str):02d}{val_str}"

def _generate_bakong_standard_khqr(account_id, name, city, amount, bill_no):
    # Tag 29: Bakong Individual/Merchant format ស្របតាមស្តង់ដារធនាគារជាតិ NBC
    # Sub-tag 00: Global Unique Identifier
    # Sub-tag 01: Bakong Account ID (e.g. samnang_mon@bkrt)
    sub29 = _tag(0, "bakong_khqr") + _tag(1, account_id)
    tag29 = _tag(29, sub29)

    amt_str = f"{float(amount):.2f}"
    sub62 = _tag(1, bill_no[:25])
    tag62 = _tag(62, sub62)

    raw = (
        _tag(0, "01") +                # Payload Format Indicator
        _tag(1, "12") +                # 12 = Dynamic QR (មានកំណត់ទឹកប្រាក់)
        tag29 +                        # Merchant Account Information
        _tag(52, "5999") +             # Merchant Category Code
        _tag(53, "840") +              # 840 = USD
        _tag(54, amt_str) +            # ចំនួនទឹកប្រាក់
        _tag(58, "KH") +               # Country Code
        _tag(59, name[:25]) +          # Merchant Name
        _tag(60, city[:15]) +          # Merchant City
        tag62 +                        # Additional Data
        "6304"                         # CRC Tag
    )
    return raw + _calc_crc16_ccitt(raw.encode("utf-8"))

# ═══════════════════════════════════════════════════════════
#  DRAW STYLED KHQR TEMPLATE
# ═══════════════════════════════════════════════════════════
def _generate_styled_khqr_image(qr_str, amount, merchant_name):
    card_w, card_h = 600, 920
    radius = 36

    mask = Image.new("L", (card_w, card_h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([(0, 0), (card_w, card_h)], radius=radius, fill=255)

    card = Image.new("RGBA", (card_w, card_h), "#FFFFFF")
    draw = ImageDraw.Draw(card)

    header_h = 135
    header_color = "#E11A22"
    draw.rectangle([(0, 0), (card_w, header_h)], fill=header_color)

    font_header, font_name, font_amt, font_curr, font_logo = None, None, None, None, None
    for f_path in ["arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"]:
        try:
            font_header = ImageFont.truetype(f_path, 42)
            font_name   = ImageFont.truetype(f_path, 28)
            font_amt    = ImageFont.truetype(f_path, 34)
            font_logo   = ImageFont.truetype(f_path, 26)
            break
        except: pass

    for f_path in ["arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"]:
        try:
            font_curr = ImageFont.truetype(f_path, 20)
            break
        except: pass

    if not font_header:
        font_header = font_name = font_amt = font_curr = font_logo = ImageFont.load_default()

    draw.text((card_w // 2, header_h // 2), "KHQR", fill="#FFFFFF", font=font_header, anchor="mm")

    margin_x = 45
    draw.text((margin_x, 175), merchant_name, fill="#111111", font=font_name)
    
    amt_str = f"{amount:,.2f}".replace(".", ",")
    draw.text((margin_x, 225), amt_str, fill="#000000", font=font_amt)
    
    try:
        amt_w = font_amt.getbbox(amt_str)[2] - font_amt.getbbox(amt_str)[0]
    except:
        amt_w = len(amt_str) * 20
    draw.text((margin_x + amt_w + 14, 238), "USD", fill="#555555", font=font_curr)

    line_y = 295
    for x in range(margin_x, card_w - margin_x, 18):
        draw.line([(x, line_y), (x + 10, line_y)], fill="#CCCCCC", width=3)

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=12,
        border=0
    )
    qr.add_data(qr_str)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="#000000", back_color="#FFFFFF").convert("RGBA")

    qr_size = 510
    qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.LANCZOS)
    qr_top = 345
    card.paste(qr_img, ((card_w - qr_size) // 2, qr_top))

    c_x = card_w // 2
    c_y = qr_top + (qr_size // 2)
    r_outer = 35
    r_inner = 30
    draw.ellipse([(c_x - r_outer, c_y - r_outer), (c_x + r_outer, c_y + r_outer)], fill="#FFFFFF")
    draw.ellipse([(c_x - r_inner, c_y - r_inner), (c_x + r_inner, c_y + r_inner)], fill="#000000")
    draw.text((c_x, c_y), "$", fill="#FFFFFF", font=font_logo, anchor="mm")

    final_card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    final_card.paste(card, (0, 0), mask=mask)

    buf = io.BytesIO()
    final_card.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ═══════════════════════════════════════════════════════════
#  BAKONG KHQR & COUNTDOWN
# ═══════════════════════════════════════════════════════════
def _generate_khqr(uid, amount, note=""):
    try:
        # សាកល្បងហៅតាម bakong_khqr library (បើ Store token ត្រឹមត្រូវ)
        from bakong_khqr import KHQR
        k = KHQR(BAKONG_TOKEN)
        qr = k.create_qr(
            account_id=BANK_ACCOUNT,
            merchant_name=MERCHANT_NAME,
            merchant_city=MERCHANT_CITY,
            amount=round(float(amount), 2),
            currency="USD",
            bill_number=(note or f"INV{uid}{int(time.time())}")[:25],
            static=False
        )
        if qr: return qr
    except Exception:
        pass
    
    # បើ Library Error ប្រើប្រាស់ NBC Standard Builder ផ្ទាល់
    clean_bill = "".join(ch for ch in (note or f"INV{uid}{int(time.time())}") if ch.isalnum())[:20]
    return _generate_bakong_standard_khqr(BANK_ACCOUNT, MERCHANT_NAME, MERCHANT_CITY, amount, clean_bill)

def _check_bakong(md5, amount, start_ts):
    try:
        from bakong_khqr import KHQR as _BK
        return _BK(BAKONG_TOKEN).check_payment(str(md5)) == "PAID"
    except Exception: return False

def _build_caption(amount, remaining_sec):
    mins = max(0, remaining_sec // 60)
    secs = max(0, remaining_sec % 60)
    timer_text = f"{mins:02d}:{secs:02d}"
    return (
        f"💳 <b>ដាក់ប្រាក់ (Top Up)</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 ចំនួន: <b>${amount:.2f}</b>\n"
        f"⏱ ផុតកំណត់ក្នុងរយ: <b>{timer_text} នាទី</b> ⏳\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📱 Scan ជាមួយ Bakong / ABA / Wing"
    )

def _watch_deposit_and_countdown(uid, uid_str, dep_id, amount, msg_id, start_ts):
    deadline = start_ts + DEPOSIT_EXPIRE_SEC
    last_edit_time = 0

    while time.time() < deadline:
        now = time.time()
        remaining = int(deadline - now)

        dep = store_deps.get(dep_id)
        if not dep or dep.get("status") != "pending":
            return
        
        md5 = dep.get("md5", "")
        if _check_bakong(md5, amount, start_ts):
            add_bal(uid, round(amount, 2))
            store_deps[dep_id]["status"] = "confirmed"
            _save(STORE_DEP_FILE, store_deps)
            try:
                bot.edit_message_caption(
                    chat_id=uid, message_id=msg_id,
                    caption=f"✅ <b>ការទូទាត់ទទួលបានជោគជ័យ!</b>\n💰 ចំនួន: +${amount:.2f}"
                )
                bot.send_message(uid, f"✅ <b>ដាក់លុយបានជោគជ័យ!</b>\n💰 +${amount:.2f}\n💳 សរុប: <b>${bal(uid):.2f}</b>", reply_markup=user_kb())
                bot.send_message(ADMIN_ID, f"💰 <b>Auto KHQR</b>\n👤 <code>{uid_str}</code> | +${amount:.2f}")
            except: pass
            return

        if now - last_edit_time >= 10 and msg_id:
            try:
                bot.edit_message_caption(
                    chat_id=uid, message_id=msg_id,
                    caption=_build_caption(amount, remaining)
                )
                last_edit_time = now
            except: pass

        time.sleep(POLL_INTERVAL)

    dep = store_deps.get(dep_id)
    if dep and dep.get("status") == "pending":
        dep["status"] = "expired"
        _save(STORE_DEP_FILE, store_deps)
        try:
            bot.edit_message_caption(
                chat_id=uid, message_id=msg_id,
                caption=f"❌ <b>QR ផុតកំណត់ហើយ (Expired)!</b>\nសូមធ្វើការស្នើសុំដាក់ប្រាក់ម្ដងទៀត។"
            )
            bot.send_message(uid, "⏰ <b>QR ផុតកំណត់!</b> សូមព្យាយាមម្ដងទៀត។")
        except: pass

def _send_deposit_qr(uid, amount):
    uid_str = str(uid)
    bill_no = f"INV{uid}{int(time.time())}"[:20]
    qr_str = _generate_khqr(uid, amount, bill_no)
    if not qr_str:
        bot.send_message(uid, "⚠️ មានបញ្ហាបង្កើត QR! សូមទាក់ទង Admin"); return

    import hashlib
    md5_hash = hashlib.md5(qr_str.encode()).hexdigest()

    dep_id = f"dep_{uid}_{int(time.time())}"
    store_deps[dep_id] = {"uid": uid_str, "amount": amount, "status": "pending", "md5": md5_hash, "qr_str": qr_str}
    _save(STORE_DEP_FILE, store_deps)

    initial_cap = _build_caption(amount, DEPOSIT_EXPIRE_SEC)

    admin_kb_dep = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ បញ្ចូលលុយឱ្យ", callback_data=f"manual_dep:approve:{dep_id}"),
         InlineKeyboardButton("❌ បដិសេធ", callback_data=f"manual_dep:reject:{dep_id}")]
    ])
    try: bot.send_message(ADMIN_ID, f"📥 <b>ការស្នើដាក់លុយ!</b>\n👤 <code>{uid_str}</code> | 💰 <b>${amount:.2f}</b>", reply_markup=admin_kb_dep)
    except: pass

    sent_msg = None
    try:
        buf = _generate_styled_khqr_image(qr_str, amount, MERCHANT_NAME)
        sent_msg = bot.send_photo(uid, buf, caption=initial_cap)
    except Exception as e:
        logger.error(f"Styled QR generation failed: {e}")
        try:
            qr = qrcode.QRCode(box_size=6, border=2)
            qr.add_data(qr_str); qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            buf = io.BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
            sent_msg = bot.send_photo(uid, buf, caption=initial_cap)
        except Exception:
            sent_msg = bot.send_message(uid, initial_cap + f"\n\n<code>{qr_str}</code>")

    msg_id = sent_msg.message_id if sent_msg else None
    threading.Thread(target=_watch_deposit_and_countdown, args=(uid, uid_str, dep_id, amount, msg_id, int(time.time())), daemon=True).start()

# ═══════════════════════════════════════════════════════════
#  KEYBOARDS
# ═══════════════════════════════════════════════════════════
def user_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🎬 រឿងទាំងអស់ (VIP)", "🎁 រឿង Free")
    kb.row("🔍 ស្វែងរករឿង", "🔥 រឿងពេញនិយម")
    kb.row("💳 ដាក់ប្រាក់", "👜 កាបូបលុយ", "💬 ជំនួយ Support")
    return kb

def admin_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("➕ បន្ថែមរឿងថ្មី", "🎬 គ្រប់គ្រងរឿង")
    kb.row("💰 កាបូបលុយសរុប", "💸 បន្ថែម/កាត់លុយ")
    kb.row("👥 អ្នកប្រើប្រាស់", "📢 ផ្សព្វផ្សាយ")
    kb.row("🏠 Menu ភ្ញៀវ")
    return kb

def cancel_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("✕ Cancel")
    return kb

def deposit_amt_kb():
    amts = [1, 2, 5, 10, 20, 50]
    btns, row = [], []
    for a in amts:
        row.append(InlineKeyboardButton(f"${a}", callback_data=f"dep:{a}"))
        if len(row) == 3:
            btns.append(row); row = []
    if row: btns.append(row)
    btns.append([InlineKeyboardButton("✏️ បញ្ចូលចំនួនផ្ទាល់ខ្លួន", callback_data="dep:custom")])
    return InlineKeyboardMarkup(btns)

def movies_list_kb(filter_type="all", page=0, per_page=6):
    items = []
    for mid, m in movies_db.items():
        if filter_type == "free" and m.get("access") == "free":
            items.append((mid, m))
        elif filter_type == "vip" and m.get("access") != "free":
            items.append((mid, m))
        elif filter_type == "all":
            items.append((mid, m))

    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    start = page * per_page
    end = start + per_page
    
    btns = []
    for mid, m in items[start:end]:
        title = m.get("title", "វីដេអូរឿង")
        if m.get("access") == "free":
            label = f"🎁 {title} (Free)"
        else:
            price = float(m.get("price", 0.0))
            label = f"🔒 {title} (${price:.2f})"
        btns.append([InlineKeyboardButton(label, callback_data=f"view_movie:{mid}")])
    
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️ ថយក្រោយ", callback_data=f"page:{filter_type}:{page-1}"))
    if page < total_pages - 1: nav.append(InlineKeyboardButton("បន្ទាប់ ➡️", callback_data=f"page:{filter_type}:{page+1}"))
    if nav: btns.append(nav)
    return InlineKeyboardMarkup(btns)

# ═══════════════════════════════════════════════════════════
#  START
# ═══════════════════════════════════════════════════════════
@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid = message.chat.id
    waiting.pop(uid, None)
    users_db[str(uid)] = {
        "name": message.from_user.first_name or "",
        "username": message.from_user.username or "",
        "last": int(time.time())
    }
    _save(USERS_FILE, users_db)
    wallets.setdefault(str(uid), 0.0)

    welcome_text = (
        f"👋 សួស្ដី <b>{message.from_user.first_name}</b>!\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🌟 ស្វាគមន៍មកកាន់បណ្តុំ <b>Bot ទស្សនារឿងកម្សាន្ត</b>\n"
        f"💰 សាច់ប្រាក់ក្នុងកាបូប: <b>${bal(uid):.2f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>រឿង VIP មានតម្លៃតាមរឿងនីមួយៗ ឬចុច <b>🎁 រឿង Free</b> ដើម្បីទស្សនាដោយឥតគិតថ្លៃ!</i>"
    )
    bot.send_message(uid, welcome_text, reply_markup=admin_kb() if uid == ADMIN_ID else user_kb())

# ═══════════════════════════════════════════════════════════
#  CALLBACK QUERIES
# ═══════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: True)
def handle_callbacks(call):
    uid = call.message.chat.id
    data = call.data

    if data.startswith("dep:"):
        val = data[4:]
        bot.answer_callback_query(call.id)
        if val == "custom":
            waiting[uid] = "dep_custom"
            bot.send_message(uid, "✏️ <b>សូមផ្ញើចំនួន $ ដែលចង់ដាក់:</b>", reply_markup=cancel_kb())
            return
        _send_deposit_qr(uid, float(val))

    elif data.startswith("manual_dep:"):
        if uid != ADMIN_ID: return
        _, act, dep_id = data.split(":")
        dep = store_deps.get(dep_id)
        if not dep:
            bot.answer_callback_query(call.id, "❌ សំណើមិនមាន!", show_alert=True); return
        target_uid = int(dep["uid"])
        amt = float(dep["amount"])
        if act == "approve":
            if dep.get("status") == "confirmed":
                bot.answer_callback_query(call.id, "⚠️ បានដាក់រួចហើយ!", show_alert=True); return
            add_bal(target_uid, amt)
            dep["status"] = "confirmed"
            _save(STORE_DEP_FILE, store_deps)
            bot.answer_callback_query(call.id, "✅ បានបញ្ចូលលុយជូនរួចរាល់")
            try: bot.send_message(target_uid, f"✅ <b>Admin បានបញ្ចូលលុយជូន:</b> +${amt:.2f}\n💳 សរុប: <b>${bal(target_uid):.2f}</b>")
            except: pass
        elif act == "reject":
            dep["status"] = "rejected"
            _save(STORE_DEP_FILE, store_deps)
            bot.answer_callback_query(call.id, "❌ បានបដិសេធ")
            try: bot.send_message(target_uid, "❌ សំណើដាក់ប្រាក់របស់អ្នកត្រូវបានបដិសេធ។")
            except: pass

    elif data.startswith("set_access:"):
        if uid != ADMIN_ID: return
        _, access_type = data.split(":")
        step = waiting.get(uid)
        if isinstance(step, dict) and step.get("step") == "choose_access":
            if access_type == "free":
                mid = f"m_{int(time.time())}"
                movies_db[mid] = {
                    "title": step["title"],
                    "type": "video",
                    "file_id": step["file_id"],
                    "access": "free",
                    "price": 0.0,
                    "desc": step["title"],
                    "views": 0,
                    "date": int(time.time())
                }
                _save(MOVIES_FILE, movies_db)
                waiting.pop(uid, None)
                bot.answer_callback_query(call.id, "✅ បន្ថែមជារឿង Free")
                bot.edit_message_text(f"✅ <b>បានបញ្ចូលរឿង Free ជោគជ័យ!</b>\n🎬 {step['title']}", 
                                      chat_id=uid, message_id=call.message.message_id)
                bot.send_message(uid, "💡 ភ្ញៀវអាចទស្សនាបានដោយសេរី!", reply_markup=admin_kb())
            else:
                waiting[uid] = {
                    "step": "enter_movie_price",
                    "title": step["title"],
                    "file_id": step["file_id"]
                }
                bot.answer_callback_query(call.id)
                bot.edit_message_text(
                    f"🎬 រឿង: <b>{step['title']}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"💰 សូមវាយ <b>តម្លៃរឿង (USD)</b> ដែលភ្ញៀវត្រូវបង់ដើម្បីមើល:\n"
                    f"<i>(ឧទាហរណ៍៖ <code>0.25</code> ឬ <code>0.50</code> ឬ <code>1.00</code>)</i>",
                    chat_id=uid, message_id=call.message.message_id
                )

    elif data.startswith("page:"):
        _, f_type, page_str = data.split(":")
        bot.edit_message_reply_markup(chat_id=uid, message_id=call.message.message_id, 
                                      reply_markup=movies_list_kb(f_type, int(page_str)))
        bot.answer_callback_query(call.id)

    elif data.startswith("view_movie:"):
        mid = data.split(":")[1]
        movie = movies_db.get(mid)
        if not movie:
            bot.answer_callback_query(call.id, "❌ រឿងត្រូវបានលុប!", show_alert=True); return
        
        bot.answer_callback_query(call.id)
        title = movie.get("title", "វីដេអូរឿង")
        is_free = (movie.get("access") == "free")
        price = float(movie.get("price", 0.0))

        if is_free or uid == ADMIN_ID:
            movie["views"] = movie.get("views", 0) + 1
            _save(MOVIES_FILE, movies_db)
            caption = f"🎬 <b>{title}</b>\n🏷️ 🎁 Free | 👁 ទស្សនា: {movie.get('views', 1)} ដង"
            if movie.get("type") == "video" and movie.get("file_id"):
                try: bot.send_video(uid, movie["file_id"], caption=caption, parse_mode=None)
                except Exception as e: bot.send_message(uid, f"⚠️ Error: {e}")
            elif movie.get("link"):
                bot.send_message(uid, caption, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ ទស្សនា", url=movie["link"])]]))
            return

        preview_txt = (
            f"🎬 <b>{title}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏷️ ប្រភេទ: <b>🔒 VIP Movie</b>\n"
            f"💰 តម្លៃទស្សនា: <b>${price:.2f}</b>\n"
            f"💳 សាច់ប្រាក់របស់អ្នក: <b>${bal(uid):.2f}</b>\n"
            f"👁 ទស្សនា: {movie.get('views', 0)} ដង\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💡 ចុចប៊ូតុងខាងក្រោមដើម្បីទូទាត់ទស្សនាវីដេអូនេះ៖"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔓 ទូទាត់ ${price:.2f} ដើម្បីទស្សនា", callback_data=f"buy_movie:{mid}")],
            [InlineKeyboardButton("💳 ដាក់ប្រាក់បន្ថែម", callback_data="dep:custom")]
        ])
        bot.send_message(uid, preview_txt, reply_markup=kb)

    elif data.startswith("buy_movie:"):
        mid = data.split(":")[1]
        movie = movies_db.get(mid)
        if not movie:
            bot.answer_callback_query(call.id, "❌ រឿងត្រូវបានលុប!", show_alert=True); return
        
        price = float(movie.get("price", 0.0))
        user_bal = bal(uid)

        if user_bal < price:
            bot.answer_callback_query(call.id, "❌ សាច់ប្រាក់របស់អ្នកមិនគ្រប់គ្រាន់ទេ!", show_alert=True)
            bot.send_message(uid, 
                f"❌ <b>សាច់ប្រាក់មិនគ្រប់គ្រាន់!</b>\n"
                f"💰 តម្លៃរឿង: <b>${price:.2f}</b>\n"
                f"💳 សាច់ប្រាក់បច្ចុប្បន្ន: <b>${user_bal:.2f}</b>\n\n"
                f"👉 សូមចុចប៊ូតុង <b>💳 ដាក់ប្រាក់</b> ជាមុនសិន។",
                reply_markup=deposit_amt_kb())
            return

        ded_bal(uid, price)
        movie["views"] = movie.get("views", 0) + 1
        _save(MOVIES_FILE, movies_db)
        bot.answer_callback_query(call.id, f"✅ ទូទាត់ជោគជ័យ -${price:.2f}")

        try:
            bot.send_message(ADMIN_ID, f"🍿 <b>ភ្ញៀវទិញរឿងទស្សនា!</b>\n👤 <code>{uid}</code>\n🎬 {movie['title']}\n💰 +${price:.2f}")
        except: pass

        caption = (
            f"🎬 <b>{movie['title']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"✅ បានទូទាត់: <b>${price:.2f}</b>\n"
            f"💳 សាច់ប្រាក់នៅសល់: <b>${bal(uid):.2f}</b>\n"
            f"🍿 សូមរីករាយទស្សនា!"
        )
        if movie.get("type") == "video" and movie.get("file_id"):
            try: bot.send_video(uid, movie["file_id"], caption=caption, parse_mode=None)
            except Exception as e: bot.send_message(uid, f"⚠️ Error: {e}")
        elif movie.get("link"):
            bot.send_message(uid, caption, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ ទស្សនា", url=movie["link"])]]))

    elif data.startswith("del_movie:"):
        if uid != ADMIN_ID: return
        mid = data.split(":")[1]
        if mid in movies_db:
            del movies_db[mid]
            _save(MOVIES_FILE, movies_db)
            bot.answer_callback_query(call.id, "✅ បានលុបរឿងរួចរាល់")
            try: bot.edit_message_text("🗑️ បានលុបរឿងដោយជោគជ័យ!", chat_id=uid, message_id=call.message.message_id)
            except: pass

# ═══════════════════════════════════════════════════════════
#  VIDEO HANDLER
# ═══════════════════════════════════════════════════════════
@bot.message_handler(content_types=["video", "document"])
def handle_video(message):
    uid = message.chat.id
    if uid != ADMIN_ID: return

    step = waiting.get(uid)
    if step == "add_movie_video":
        file_id = message.video.file_id if message.video else message.document.file_id
        caption_title = message.caption.strip() if message.caption else ""

        if caption_title:
            waiting[uid] = {"step": "choose_access", "file_id": file_id, "title": caption_title}
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔒 រឿង VIP (គិតលុយ)", callback_data="set_access:vip")],
                [InlineKeyboardButton("🎁 រឿង Free (ឥតគិតថ្លៃ)", callback_data="set_access:free")]
            ])
            bot.send_message(uid, f"🎬 ចំណងជើង: <b>{caption_title}</b>\n\nតើរឿងនេះជាប្រភេទអ្វី?", reply_markup=kb)
        else:
            waiting[uid] = {"step": "add_movie_title", "file_id": file_id}
            bot.send_message(uid, 
                "📥 <b>បានទទួលវីដេអូរួចរាល់!</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "📝 សូមវាយ <b>ចំណងជើងរឿង</b> រួចផ្ញើមកទីនេះ:", 
                reply_markup=cancel_kb())

# ═══════════════════════════════════════════════════════════
#  TEXT MESSAGES HANDLER
# ═══════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: True)
def handle_messages(message):
    uid = message.chat.id
    text = message.text.strip() if message.text else ""
    step = waiting.get(uid)

    if text in ("✕ Cancel", "❌ Cancel"):
        waiting.pop(uid, None)
        bot.send_message(uid, "🏠 ត្រឡប់មក Menu ដើមវិញ", reply_markup=admin_kb() if uid == ADMIN_ID else user_kb())
        return

    if isinstance(step, dict) and step.get("step") == "enter_movie_price":
        try:
            price = round(float(text.replace("$", "")), 2)
            if price <= 0: raise ValueError
        except:
            bot.send_message(uid, "❌ សូមបញ្ចូលតម្លៃជាលេខឱ្យបានត្រឹមត្រូវ (ឧទាហរណ៍: <code>0.50</code>):")
            return
        
        mid = f"m_{int(time.time())}"
        movies_db[mid] = {
            "title": step["title"],
            "type": "video",
            "file_id": step["file_id"],
            "access": "vip",
            "price": price,
            "desc": step["title"],
            "views": 0,
            "date": int(time.time())
        }
        _save(MOVIES_FILE, movies_db)
        waiting.pop(uid, None)
        bot.send_message(uid, 
            f"✅ <b>បានបញ្ចូលរឿង VIP ជោគជ័យ!</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎬 ចំណងជើង: <b>{step['title']}</b>\n"
            f"💰 តម្លៃ: <b>${price:.2f}</b>\n"
            f"💡 ភ្ញៀវនឹងឃើញតម្លៃនេះពេលចុចមើល!", reply_markup=admin_kb())
        return

    if isinstance(step, dict) and step.get("step") == "add_movie_title":
        file_id = step["file_id"]
        title = text
        waiting[uid] = {"step": "choose_access", "file_id": file_id, "title": title}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔒 រឿង VIP (គិតលុយ)", callback_data="set_access:vip")],
            [InlineKeyboardButton("🎁 រឿង Free (ឥតគិតថ្លៃ)", callback_data="set_access:free")]
        ])
        bot.send_message(uid, f"🎬 ចំណងជើង: <b>{title}</b>\n\nតើរឿងនេះជាប្រភេទអ្វី?", reply_markup=kb)
        return

    if step == "dep_custom":
        try:
            amt = float(text.replace("$", ""))
            if amt < 0.1: raise ValueError
            waiting.pop(uid, None)
            _send_deposit_qr(uid, amt)
        except:
            bot.send_message(uid, "❌ សូមបញ្ចូលចំនួនទឹកប្រាក់ជាលេខ (ឧ: 2.50):")
        return

    # --- USER MENU ---
    if text in ("🎬 រឿងទាំងអស់ (VIP)", "🎬 រឿងទាំងអស់"):
        vip_movies = [m for m in movies_db.values() if m.get("access") != "free"]
        if not vip_movies:
            bot.send_message(uid, "❌ មិនទាន់មានរឿង VIP នៅឡើយទេ!"); return
        bot.send_message(uid, "🎬 <b>ជ្រើសរើសរឿង VIP៖</b>", 
                         reply_markup=movies_list_kb(filter_type="vip", page=0))
        return

    if text == "🎁 រឿង Free":
        free_movies = [m for m in movies_db.values() if m.get("access") == "free"]
        if not free_movies:
            bot.send_message(uid, "❌ មិនទាន់មានរឿង Free នៅឡើយទេ!"); return
        bot.send_message(uid, "🎁 <b>ជ្រើសរើសរឿង Free (ទស្សនាឥតគិតថ្លៃ)៖</b>", 
                         reply_markup=movies_list_kb(filter_type="free", page=0))
        return

    if text in ("👜 កាបូបលុយ", "👜 Wallet"):
        bot.send_message(uid,
            f"👜 <b>កាបូបលុយរបស់អ្នក</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"👤 ID: <code>{uid}</code>\n💰 សាច់ប្រាក់: <b>${bal(uid):.2f}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n💡 ចុចប៊ូតុង <b>💳 ដាក់ប្រាក់</b> ដើម្បីបញ្ចូលលុយទស្សនារឿង VIP។", reply_markup=user_kb())
        return

    if text in ("💳 ដាក់ប្រាក់", "💳 Top Up"):
        waiting.pop(uid, None)
        bot.send_message(uid, f"💸 <b>បញ្ចូលទឹកប្រាក់</b>\n💳 សាច់ប្រាក់បច្ចុប្បន្ន: <b>${bal(uid):.2f}</b>\n\nសូមជ្រើសរើសចំនួនប្រាក់៖", reply_markup=deposit_amt_kb())
        return

    if text == "🔥 រឿងពេញនិយម":
        if not movies_db:
            bot.send_message(uid, "❌ មិនទាន់មានរឿងនៅឡើយទេ!"); return
        top_movies = sorted(movies_db.items(), key=lambda x: x[1].get("views", 0), reverse=True)[:5]
        btns = []
        for mid, m in top_movies:
            badge = "🎁" if m.get("access") == "free" else f"🔒 ${m.get('price', 0):.2f}"
            btns.append([InlineKeyboardButton(f"{m['title']} ({badge})", callback_data=f"view_movie:{mid}")])
        bot.send_message(uid, "🔥 <b>រឿងដែលមានអ្នកទស្សនាច្រើនជាងគេ៖</b>", reply_markup=InlineKeyboardMarkup(btns))
        return

    if text == "🔍 ស្វែងរករឿង":
        waiting[uid] = "search_movie"
        bot.send_message(uid, "🔎 សូមវាយ <b>ចំណងជើងរឿង</b> ដែលអ្នកចង់ស្វែងរក:", reply_markup=cancel_kb())
        return

    if step == "search_movie":
        waiting.pop(uid, None)
        query = text.lower()
        results = [(mid, m) for mid, m in movies_db.items() if query in m.get("title", "").lower()]
        if not results:
            bot.send_message(uid, f"❌ រកមិនឃើញរឿង: <b>{text}</b>", reply_markup=user_kb()); return
        btns = []
        for mid, m in results:
            badge = "🎁" if m.get("access") == "free" else f"🔒 ${m.get('price', 0):.2f}"
            btns.append([InlineKeyboardButton(f"{m['title']} ({badge})", callback_data=f"view_movie:{mid}")])
        bot.send_message(uid, f"✅ រកឃើញចំនួន <b>{len(results)}</b> រឿង:", reply_markup=InlineKeyboardMarkup(btns))
        return

    if text == "💬 ជំនួយ Support":
        bot.send_message(uid, "💬 <b>ទំនាក់ទំនងជំនួយ</b>\n━━━━━━━━━━━━━━━━━━\n📞 Admin: @SmeyLov008")
        return

    # --- ADMIN MENU ---
    if uid == ADMIN_ID:
        if text.startswith("/addbal"):
            parts = text.split()
            if len(parts) == 3:
                try:
                    target, amt = parts[1], float(parts[2])
                    add_bal(target, amt)
                    bot.send_message(uid, f"✅ +${amt:.2f} ទៅកាន់ <code>{target}</code>\nBalance: <b>${bal(target):.2f}</b>")
                    try: bot.send_message(int(target), f"✅ Admin បានបន្ថែមលុយជូន: +${amt:.2f}\nសាច់ប្រាក់: <b>${bal(target):.2f}</b>")
                    except: pass
                except: bot.send_message(uid, "❌ Format: /addbal UID AMOUNT")
            return

        if text.startswith("/deductbal"):
            parts = text.split()
            if len(parts) == 3:
                try:
                    target, amt = parts[1], float(parts[2])
                    ded_bal(target, amt)
                    bot.send_message(uid, f"✅ -${amt:.2f} ពី <code>{target}</code>")
                except: bot.send_message(uid, "❌ Format: /deductbal UID AMOUNT")
            return

        if text == "➕ បន្ថែមរឿងថ្មី":
            waiting[uid] = "add_movie_video"
            bot.send_message(uid, 
                "📤 <b>សូមផ្ញើ ឬ Forward វីដេអូរឿងចូលទីនេះ៖</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "💡 <i>អ្នកអាចជ្រើសរើស VIP រួចកំណត់តម្លៃវីដេអូបាននៅជំហានបន្ទាប់។</i>", 
                reply_markup=cancel_kb())
            return

        if text == "🎬 គ្រប់គ្រងរឿង":
            if not movies_db:
                bot.send_message(uid, "❌ គ្មានរឿងទេ!", reply_markup=admin_kb()); return
            for mid, m in list(movies_db.items())[-10:]:
                price_str = "Free" if m.get("access") == "free" else f"${m.get('price',0):.2f}"
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ លុបរឿងនេះ", callback_data=f"del_movie:{mid}")]])
                bot.send_message(uid, f"🎬 <b>{m['title']}</b>\n💰 តម្លៃ: <b>{price_str}</b> | 👁 Views: {m.get('views',0)}", reply_markup=kb)
            return

        if text == "💰 កាបូបលុយសរុប":
            lines = ["<b>💰 កាបូបលុយអ្នកប្រើ</b>\n━━━━━━━━━━━━━━━━━━"]
            for u_id, u_info in sorted(users_db.items(), key=lambda x: x[1].get("last",0), reverse=True)[:25]:
                lines.append(f"👤 {u_info.get('name','?')} (<code>{u_id}</code>): <b>${bal(u_id):.2f}</b>")
            bot.send_message(uid, "\n".join(lines)[:4000], reply_markup=admin_kb()); return

        if text == "💸 បន្ថែម/កាត់លុយ":
            bot.send_message(uid, "💡 ប្រើ:\n• /addbal UID AMOUNT\n• /deductbal UID AMOUNT", reply_markup=admin_kb()); return

        if text == "🏠 Menu ភ្ញៀវ":
            bot.send_message(uid, "👁 ទម្រង់ជាភ្ញៀវ", reply_markup=user_kb()); return

        if text == "👥 អ្នកប្រើប្រាស់":
            bot.send_message(uid, f"👥 សរុប: <b>{len(users_db)}</b> នាក់", reply_markup=admin_kb()); return

        if text == "📢 ផ្សព្វផ្សាយ":
            waiting[uid] = "broadcast"
            bot.send_message(uid, "📢 ផ្ញើសារដែលចង់ផ្សាយ:", reply_markup=cancel_kb()); return

        if step == "broadcast":
            waiting.pop(uid, None)
            sent = 0
            for u in users_db.keys():
                try: bot.send_message(int(u), text); sent += 1
                except: pass
            bot.send_message(uid, f"✅ ផ្ញើបាន {sent} នាក់", reply_markup=admin_kb()); return

    bot.send_message(uid, "❓ សូមជ្រើសរើសតាម Menu ខាងក្រោម៖", reply_markup=user_kb())

# ═══════════════════════════════════════════════════════════
#  FLASK RUN
# ═══════════════════════════════════════════════════════════
flask_app = Flask(__name__)
@flask_app.route("/health")
def health(): return jsonify({"status": "running", "type": "Movie Bot Pay-Per-View"})

def run_flask():
    flask_app.run(host="0.0.0.0", port=5055, debug=False, use_reloader=False)

if __name__ == "__main__":
    logger.info("🚀 Bot is running...")
    threading.Thread(target=run_flask, daemon=True).start()
    bot.infinity_polling(timeout=20, long_polling_timeout=15)
