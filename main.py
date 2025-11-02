# main.py
# Full Discord Payment Bot (Auto Bank Slip OCR + TrueMoney link + Auto Role + Admin verify)
# DO NOT put your Discord token here. Use ENV/Secrets.

import os
import re
import io
import time
import sqlite3
import datetime
import threading

import discord
from discord.ext import commands, tasks

from PIL import Image
import numpy as np
import cv2
import pytesseract

from dotenv import load_dotenv
load_dotenv()

# ---------------- CONFIG (from ENV) ----------------
TOKEN = os.getenv("DISCORD_TOKEN")  # <-- DO NOT hardcode. Set in Secrets/Env.
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID", "0"))
SCAN_CHANNEL_ID = int(os.getenv("SCAN_CHANNEL_ID", "0"))
TRUEWALLET_PHONE = os.getenv("TRUEWALLET_PHONE", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# ---------------- PRICES / ROLES / DURATIONS ----------------
# Prices map to role ids (use strings for DB)
PRICES_TO_ROLE = {
    20: "1433747080660258867",
    40: "1433747173039804477",
    80: "1433747209475719332",
    150: "1433747247295889489",
    300: "1433747281932189826"
}

DURATIONS = {
    20: 1,
    40: 3,
    80: 7,
    150: 15,
    300: 30
}

TARGET_COMPANY = "บริษัท วันดีดี คอร์ปอเรชั่น จำกัด"
QR_IMAGE_URL = "https://img2.pic.in.th/pic/b3353abf-04b1-4d82-a806-9859e0748f24.webp"

# ---------------- Discord setup ----------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- Database (sqlite) ----------------
conn = sqlite3.connect("database.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS invoices(
    invoice_id TEXT PRIMARY KEY,
    discord_id TEXT,
    role_id TEXT,
    plan TEXT,
    price INTEGER,
    status TEXT,
    created_at INTEGER
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS subs(
    user_id TEXT,
    role_id TEXT,
    expires_at INTEGER
)
""")
conn.commit()

# ---------------- Helper functions ----------------
def make_invoice_id():
    return f"INV{int(time.time())}"

def preprocess_image_bytes(image_bytes):
    """Preprocess image bytes -> OpenCV gray sharpened numpy array"""
    pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = np.array(pil)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # denoise
    blur = cv2.GaussianBlur(gray, (5,5), 0)
    # sharpen
    kernel = np.array([[0,-1,0],[-1,5,-1],[0,-1,0]])
    sharp = cv2.filter2D(blur, -1, kernel)
    # optional threshold / resize if small
    h, w = sharp.shape
    if max(h,w) < 800:
        scale = 800 / max(h,w)
        sharp = cv2.resize(sharp, (int(w*scale), int(h*scale)), interpolation=cv2.INTER_LINEAR)
    return sharp

def ocr_text_from_bytes(image_bytes):
    img = preprocess_image_bytes(image_bytes)
    # pytesseract expects Pillow or numpy; pass numpy
    text = pytesseract.image_to_string(img, lang='tha+eng')
    return text

def detect_price_in_text(text):
    # try to find numbers like 20, 20.00, 20.0, 20฿ etc.
    for price in sorted(PRICES_TO_ROLE.keys()):
        # match whole number or with .00
        if re.search(rf"\b{price}\b", text):
            return price
        if re.search(rf"\b{price}\.00\b", text):
            return price
    # fallback: detect pattern like 1,500 or 1500 etc -> normalize
    m = re.search(r"(\d{1,3}(?:[,\s]\d{3})+|\d{2,4})(?:\.\d+)?", text.replace('฿',''))
    if m:
        cleaned = re.sub(r"[,\s]","", m.group(1))
        try:
            n = int(cleaned)
            for p in PRICES_TO_ROLE:
                if abs(n - p) <= 0:  # exact
                    return p
        except:
            pass
    return None

def detect_time_recent(text, max_seconds=600):
    # find HH:MM occurrences
    now = datetime.datetime.now()
    m = re.search(r"(\d{1,2}[:.]\d{2})", text)
    if not m:
        return False
    t = m.group(1).replace('.',':')
    try:
        slip_tm = datetime.datetime.strptime(t, "%H:%M")
        slip_dt = slip_tm.replace(year=now.year, month=now.month, day=now.day)
        diff = abs((now - slip_dt).total_seconds())
        return diff <= max_seconds
    except:
        return False

async def give_role_and_store(user_id:int, role_id:str, days:int):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return False
    member = guild.get_member(int(user_id))
    role = guild.get_role(int(role_id))
    if not member or not role:
        return False
    try:
        await member.add_roles(role)
        expires = int(time.time() + days*86400)
        cur.execute("INSERT INTO subs VALUES (?,?,?)", (str(user_id), str(role_id), expires))
        conn.commit()
        try:
            await member.send(f"✅ คุณได้รับยศ {role.name} ({days} วัน)")
        except:
            pass
        return True
    except Exception as e:
        print("give_role error:", e)
        return False

async def remove_expired_roles():
    guild = bot.get_guild(GUILD_ID)
    rows = cur.execute("SELECT user_id, role_id, expires_at FROM subs").fetchall()
    now = int(time.time())
    for uid, rid, exp in rows:
        if now >= exp:
            member = guild.get_member(int(uid))
            role = guild.get_role(int(rid))
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role)
                except:
                    pass
                try:
                    await member.send("⛔ ยศของคุณหมดอายุแล้ว")
                except:
                    pass
            cur.execute("DELETE FROM subs WHERE user_id=? AND role_id=?", (uid, rid))
            conn.commit()

# ---------------- Tasks ----------------
@tasks.loop(seconds=60)
async def job_check_expired():
    await remove_expired_roles()

# ---------------- Buy UI ----------------
class BuyButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for price in sorted(PRICES_TO_ROLE.keys()):
            days = DURATIONS.get(str(price), DURATIONS.get(price, 1))
            label = f"{days} วัน • {price}฿"
            self.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.green, custom_id=f"buy_{price}"))

# Admin approve view used for both bank OCR notifications & TMN notifications
class AdminApproveView(discord.ui.View):
    def __init__(self, invoice_id, user_id, role_id, days):
        super().__init__(timeout=None)
        self.invoice_id = invoice_id
        self.user_id = user_id
        self.role_id = role_id
        self.days = days

    @discord.ui.button(label="✅ อนุมัติ", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            cur.execute("UPDATE invoices SET status='paid' WHERE invoice_id=?", (self.invoice_id,))
            conn.commit()
        except:
            pass
        await give_role_and_store(int(self.user_id), self.role_id, self.days)
        await interaction.response.send_message("✅ อนุมัติเรียบร้อยแล้ว", ephemeral=True)

    @discord.ui.button(label="❌ ปฏิเสธ", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        class RejectModal(discord.ui.Modal, title="เหตุผลการปฏิเสธ"):
            reason = discord.ui.TextInput(label="เหตุผล", style=discord.TextStyle.paragraph, required=True, max_length=500)
            async def on_submit(self, modal_interaction: discord.Interaction):
                txt = self.reason.value
                try:
                    cur.execute("UPDATE invoices SET status='rejected' WHERE invoice_id=?", (self.parent.invoice_id,))
                    conn.commit()
                except:
                    pass
                try:
                    user = await bot.fetch_user(int(self.parent.user_id))
                    await user.send(f"⛔ คำสั่งซื้อ `{self.parent.invoice_id}` ถูกปฏิเสธ: {txt}")
                except:
                    pass
                await modal_interaction.response.send_message("✅ ปฏิเสธและแจ้งผู้ซื้อแล้ว", ephemeral=True)
        # attach parent data for modal use
        RejectModal.parent = self
        await interaction.response.send_modal(RejectModal())

    @discord.ui.button(label="🔎 ดูข้อมูลผู้ซื้อ", style=discord.ButtonStyle.secondary)
    async def info(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = None
        try:
            user = await bot.fetch_user(int(self.user_id))
        except:
            pass
        info = f"User ID: {self.user_id}\nInvoice: {self.invoice_id}\nRole ID: {self.role_id}\nDays: {self.days}"
        if user:
            info = f"Username: {user}\n" + info
        await interaction.response.send_message(f"🔎 ข้อมูลผู้ซื้อ:\n{info}", ephemeral=True)

# ---------------- Commands ----------------
@bot.command()
async def buy(ctx):
    embed = discord.Embed(title="🛒 ซื้อแพ็ก", description="กดปุ่มด้านล่างเพื่อเลือกแพ็ก", color=0x00ffcc)
    embed.set_image(url=QR_IMAGE_URL)
    embed.set_footer(text=f"TrueWallet: {TRUEWALLET_PHONE}")
    await ctx.send(embed=embed, view=BuyButtons())

# ---------------- Interaction handler ----------------
@bot.event
async def on_interaction(interaction: discord.Interaction):
    try:
        data = interaction.data or {}
        cid = data.get("custom_id")
        if not cid:
            return
        # buy button pressed
        if cid.startswith("buy_"):
            price = int(cid.split("_",1)[1])
            role_id = PRICES_TO_ROLE.get(price)
            days = DURATIONS.get(str(price), DURATIONS.get(price, 1))
            invoice_id = make_invoice_id()
            cur.execute("INSERT INTO invoices VALUES (?,?,?,?,?,?,?)",
                        (invoice_id, str(interaction.user.id), role_id, str(price), price, "pending", int(time.time())))
            conn.commit()
            embed = discord.Embed(
                title="🧾 ใบคำสั่งซื้อ",
                description=(f"**แพ็ก:** {days} วัน\n**ราคา:** {price} บาท\n**เลขอ้างอิง:** `{invoice_id}`\n\n"
                             f"กรุณาชำระและส่งสลิป/ลิงก์ในช่อง <#{SCAN_CHANNEL_ID}>"),
                color=0x00ffcc
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        print("interaction error:", e)

# ---------------- Message handler (scan channel) ----------------
@bot.event
async def on_message(message: discord.Message):
    # allow commands
    await bot.process_commands(message)

    if message.author.bot:
        return

    # truemoney link handling: if user posts TMN link in scan channel
    content = (message.content or "").strip()
    if message.channel.id == SCAN_CHANNEL_ID and ("gift.truemoney.com" in content.lower()):
        # find latest invoice for user
        row = cur.execute("SELECT invoice_id, role_id, plan, price, status FROM invoices WHERE discord_id=? ORDER BY created_at DESC", (str(message.author.id),)).fetchone()
        if not row:
            await message.reply("❌ ไม่พบคำสั่งซื้อก่อนหน้า โปรดลองกด !buy ก่อน")
            return
        invoice_id, role_id, plan, price, status = row
        days = DURATIONS.get(str(int(price)), DURATIONS.get(int(price),1))
        # mark paid + give role auto
        cur.execute("UPDATE invoices SET status='paid' WHERE invoice_id=?", (invoice_id,))
        conn.commit()
        await give_role_and_store(int(message.author.id), role_id, days)
        # notify admin with buttons
        admin_ch = bot.get_guild(GUILD_ID).get_channel(ADMIN_CHANNEL_ID) if bot.get_guild(GUILD_ID) else None
        view = AdminApproveView(invoice_id, str(message.author.id), role_id, days)
        admin_msg = f"🔔 (TrueMoney) ตรวจอัตโนมัติ: <@{message.author.id}> Invoice `{invoice_id}`\nลิงก์: {content}"
        try:
            if admin_ch:
                await admin_ch.send(admin_msg, view=view)
        except Exception as e:
            print("notify admin error:", e)
        try:
            await message.author.send("✅ ขอบคุณ! ระบบตรวจอัตโนมัติพบลิงก์และมอบยศให้เรียบร้อยแล้ว")
        except:
            pass
        try:
            await message.delete()
        except:
            pass
        return

    # bank slip image handling
    if message.channel.id == SCAN_CHANNEL_ID and message.attachments:
        # find invoice
        row = cur.execute("SELECT invoice_id, role_id, plan, price, status FROM invoices WHERE discord_id=? ORDER BY created_at DESC", (str(message.author.id),)).fetchone()
        if not row:
            await message.reply("❌ ไม่พบคำสั่งซื้อค้างอยู่ กรุณากด !buy ก่อน")
            return
        invoice_id, role_id, plan, price_db, status = row
        days = DURATIONS.get(str(int(price_db)), DURATIONS.get(int(price_db),1))
        # read attachment bytes
        att = message.attachments[0]
        img_bytes = await att.read()
        text = ocr_text_from_bytes(img_bytes)
        company_ok = TARGET_COMPANY in text
        price_found = detect_price_in_text(text)
        time_ok = detect_time_recent(text, max_seconds=600)
        # log for debug (optional)
        print("OCR TEXT:", text[:200].replace("\n"," "))
        if company_ok and price_found and price_found == int(price_db) and time_ok:
            # success -> admin notify with approve buttons (auto-pass)
            cur.execute("UPDATE invoices SET status='pending_admin' WHERE invoice_id=?", (invoice_id,))
            conn.commit()
            view = AdminApproveView(invoice_id, str(message.author.id), role_id, days)
            admin_ch = bot.get_guild(GUILD_ID).get_channel(ADMIN_CHANNEL_ID) if bot.get_guild(GUILD_ID) else None
            admin_msg = (f"✅ สลิปตรงตามเงื่อนไข: <@{message.author.id}> Invoice `{invoice_id}`\n"
                         f"ยอด: {price_found} บาท\nบริษัท: {TARGET_COMPANY}\n(กดอนุมัติเพื่อลงยศหรือปฏิเสธถ้าจำเป็น)")
            try:
                if admin_ch:
                    await admin_ch.send(admin_msg, view=view)
            except Exception as e:
                print("admin send error:", e)
            # give role immediately (auto)
            await give_role_and_store(int(message.author.id), role_id, days)
            try:
                await message.author.send("✅ สลิปตรวจสอบผ่าน ระบบมอบยศให้แล้วครับ")
            except:
                pass
            # delete image for privacy
            try:
                await message.delete()
            except:
                pass
            return
        else:
            # failed checks -> notify admin and user
            admin_ch = bot.get_guild(GUILD_ID).get_channel(ADMIN_CHANNEL_ID) if bot.get_guild(GUILD_ID) else None
            admin_msg = (f"❌ สลิปไม่ผ่านการตรวจ: <@{message.author.id}> Invoice `{invoice_id}`\n"
                         f"Detected price: {price_found}\nCompany OK: {company_ok}\nTime OK: {time_ok}\n")
            try:
                if admin_ch:
                    view = AdminApproveView(invoice_id, str(message.author.id), role_id, days)
                    await admin_ch.send(admin_msg, view=view)
            except Exception as e:
                print("admin notify error:", e)
            try:
                await message.author.send("❌ สลิปไม่ผ่านการตรวจอัตโนมัติ กรุณาติดต่อแอดมินหรือส่งใหม่")
            except:
                pass
            try:
                await message.delete()
            except:
                pass
            return

# ---------------- On ready ----------------
@bot.event
async def on_ready():
    print("✅ Bot Online:", bot.user)
    job_check_expired.start()

# ---------------- Run ----------------
if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN not set in environment.")
    else:
        bot.run(TOKEN)
