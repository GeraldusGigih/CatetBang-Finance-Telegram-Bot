import os
import re
import logging
from datetime import datetime, time, timedelta
import pytz
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from google import genai
from google.genai import types
from pydantic import BaseModel

# =========================
# CONFIG & SETUP
# =========================
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "TOKEN LU")
ALLOWED_USER_ID = os.getenv("TELEGRAM_USER_ID", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "CatetBang")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================
# GOOGLE SHEETS SETUP
# =========================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

try:
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open(SPREADSHEET_NAME).sheet1
    logger.info("✅ Berhasil konek ke Google Sheets")
except Exception as e:
    logger.error(f"❌ Gagal konek ke Google Sheets: {e}")

# =========================
# GEMINI AI SETUP
# =========================
genai_client = genai.Client(api_key=GEMINI_API_KEY)

class Pengeluaran(BaseModel):
    is_pengeluaran: bool
    nama: str
    kategori: str
    harga: int
    jumlah: int
    total: int

def parse_expense(text: str) -> Pengeluaran | None:
    prompt = f"""
Kamu adalah asisten pencatat keuangan pribadi. Ekstrak informasi pengeluaran dari teks berikut.
Kategori yang valid HANYA: "Makanan & Minuman", "Transport", "Hiburan", "Belanja", "Tagihan", "Lainnya".
Jika teks BUKAN tentang pengeluaran uang (misal: sapaan, tanya kabar, dll), atur is_pengeluaran=false.
Jika teks tentang pengeluaran uang, ekstrak:
- nama: nama barang/jasa yang dibeli
- kategori: pilih salah satu dari kategori valid di atas
- harga: harga satuan dalam angka (misal: 15rb jadi 15000, 300k jadi 300000)
- jumlah: kuantitas/jumlah barang (default 1 jika tidak disebut)
- total: harga dikali jumlah

Teks user: "{text}"
"""
    try:
        response = genai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Pengeluaran,
                temperature=0.0
            ),
        )
        return Pengeluaran.model_validate_json(response.text)
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return None

# =========================
# BOT HANDLERS
# =========================
def check_auth(update: Update) -> bool:
    user_id = str(update.effective_user.id)
    if not ALLOWED_USER_ID:
        return False
    return user_id == ALLOWED_USER_ID

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not ALLOWED_USER_ID:
        await update.message.reply_text(
            f"👋 Halo! Bot ini sedang mode setup.\n\n"
            f"ID Telegram kamu adalah: `{user_id}`\n\n"
            f"Tolong copy ID di atas dan masukkan ke file `.env` di variabel `TELEGRAM_USER_ID` lalu restart bot ya.",
            parse_mode='Markdown'
        )
        return
    
    if not check_auth(update):
        await update.message.reply_text(f"⛔ Akses ditolak. Bot ini private.")
        return
        
    await update.message.reply_text(
        "🚀 *CatetBang AI Ready!*\n\n"
        "Ketik aja pengeluaran lu bahasa sehari-hari, ntar gw catetin otomatis ke Sheets.\n"
        "Contoh: _'Beli bensin 20rb'_ atau _'ngopi di starbucks 2 gelas 100k'_.",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ALLOWED_USER_ID:
        user_id = str(update.effective_user.id)
        await update.message.reply_text(f"Bot belum disetup User ID. ID kamu: `{user_id}`", parse_mode='Markdown')
        return

    if not check_auth(update):
        return

    text = update.message.text
    await update.message.chat.send_action(action="typing")
    
    hasil = parse_expense(text)
    
    if not hasil:
        await update.message.reply_text("Aduh, API Gemini lagi pusing bang. Coba lagi ya.")
        return
        
    if not hasil.is_pengeluaran:
        await update.message.reply_text("🤖 Oke bang, tapi ini bukan catetan pengeluaran kan? Kalau mau nyatet, sebutin nama barang & harganya yak!")
        return

    # Waktu Jakarta
    tz = pytz.timezone('Asia/Jakarta')
    tanggal = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # Append ke sheets: Nama, Kategori, Harga, Jumlah, TOTAL, Tanggal
        sheet.append_row([
            hasil.nama,
            hasil.kategori,
            hasil.harga,
            hasil.jumlah,
            hasil.total,
            tanggal
        ])
        await update.message.reply_text(
            f"✅ *Tercatat di Sheets!*\n\n"
            f"🛒 *Item:* {hasil.nama}\n"
            f"📂 *Kategori:* {hasil.kategori}\n"
            f"💰 *Harga:* Rp{hasil.harga:,}\n"
            f"📦 *Jumlah:* {hasil.jumlah}\n"
            f"💵 *Total:* Rp{hasil.total:,}",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Sheet error: {e}")
        await update.message.reply_text("❌ Gagal simpan ke Google Sheets. Coba cek log di server.")

# =========================
# JOBS / REMINDERS
# =========================
async def daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_USER_ID:
        await context.bot.send_message(
            chat_id=ALLOWED_USER_ID,
            text="☀️ Pagi bang! Jangan lupa nyatet pengeluaran hari ini yak, biar gak boncos 💸"
        )

async def check_end_of_month(context: ContextTypes.DEFAULT_TYPE):
    tz = pytz.timezone('Asia/Jakarta')
    today = datetime.now(tz)
    tomorrow = today + timedelta(days=1)
    
    # Kalau besok bulannya beda, berarti hari ini hari terakhir bulan ini
    if tomorrow.month != today.month:
        if ALLOWED_USER_ID:
            try:
                # Ambil semua baris dari sheets
                records = sheet.get_all_values()
                
                total_bulan_ini = 0
                # Format bulan ini, contoh: "2026-05"
                current_month_str = today.strftime("%Y-%m")
                
                # Looping dari baris ke-2 (skip header)
                for row in records[1:]:
                    # Pastikan baris punya minimal 6 kolom (karena Tanggal ada di kolom ke-6)
                    if len(row) >= 6:
                        total_str = row[4] # Kolom ke-5 (index 4) adalah TOTAL
                        tanggal_str = row[5] # Kolom ke-6 (index 5) adalah Tanggal
                        
                        # Kalau tanggal berawalan "2026-05" (bulan ini)
                        if str(tanggal_str).startswith(current_month_str):
                            try:
                                total_bulan_ini += int(total_str)
                            except ValueError:
                                pass # Abaikan kalau bukan angka
                                
                pesan = (
                    f"📊 *Rekap Akhir Bulan!*\n\n"
                    f"Total boncos lu bulan ini: *Rp{total_bulan_ini:,}*\n\n"
                    f"Jangan lupa siapin budget buat bulan depan ya bang! 💸"
                )
            except Exception as e:
                logger.error(f"Gagal hitung rekap bulanan: {e}")
                pesan = "📊 *Akhir Bulan Bang!*\n\nWaktunya cek Google Sheets lu buat ngecek total boncos bulan ini. Coba cek sheets langsung ya!"

            await context.bot.send_message(
                chat_id=ALLOWED_USER_ID,
                text=pesan,
                parse_mode='Markdown'
            )

# =========================
# MAIN APP
# =========================
if __name__ == '__main__':
    if TELEGRAM_TOKEN == "TOKEN LU" or not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "isi_token_telegram_bot_lu_disini":
        print("❌ ERROR: TELEGRAM_TOKEN belum diisi di file .env!")
        exit(1)
        
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Schedulers
    tz = pytz.timezone('Asia/Jakarta')
    job_queue = app.job_queue
    
    # Reminder tiap jam 09:00 WIB
    job_queue.run_daily(daily_reminder, time=time(hour=9, minute=0, tzinfo=tz))
    
    # Cek akhir bulan tiap jam 20:00 WIB
    job_queue.run_daily(check_end_of_month, time=time(hour=20, minute=0, tzinfo=tz))
    
    print("🚀 Bot AI CatetBang udah jalan! Tekan Ctrl+C buat stop.")
    app.run_polling()
