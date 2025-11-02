import discord
from discord.ext import commands, tasks
import pytesseract
from PIL import Image
import numpy as np
import cv2
import re
import io
import datetime
import os
import sqlite3
import time
import threading
from dotenv import load_dotenv
from flask import Flask
from waitress import serve

load_dotenv()

# ---------------- ENV CONFIG ----------------
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
SCAN_CHANNEL_ID = int(os.getenv("SCAN_CHANNEL_ID"))
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID"))
TRUEWALLET_PHONE = os.getenv("TRUEWALLET_PHONE")

# ---------------- PRICE / ROLE DATA ----------------
ROLE_IDS = {
    "20": "1433747080660258867",
    "40": "1433747173039804477",
    "80": "1433747209475719332",
    "150": "1433747247295889489",
    "300": "1433747281932189826"
}

PRICES = {
    20: "1433747080660258867",
    40: "1433747173039804477",
    80: "1433747209475719332",
    150: "1433747247295889489",
    300: "1433747281932189826"
}

DURATIONS = {
    "20": 1,
    "40": 3,
    "80": 7,
    "150": 15,
    "300": 30
}

TARGET_COMPANY = "บริษัท วันดีดี คอร์ปอเรชั่น จำกัด"
QR_IMAGE_URL = "https://img2.pic.in.th/pic/b3353abf-04b1-4d82-a806-9859e0748f24.webp"

# ---------------- Discord Bot ----------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- Database ----------------
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

# ---------------- Helpers ----------------
def preprocess_image(pil_image):
    img = np.array(pil_image)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    sharp = cv2.filter2D(blur, -1, np.array([[0,-1,0],[-1,5,-1],[0,-1,0]]))
    return sharp

def extract_text(image_bytes):
    pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    processed = preprocess_image(pil)
    return pytesseract.image_to_string(processed, lang="tha+eng")

def check_company(text):
    return TARGET_COMPANY in text

def check_price(text):
    for price in PRICES.keys():
        if str(price) in text:
            return price
    return None

def check_time(text):
    now = datetime.datetime.now()
    match = re.search(r"(\d{1,2}[:.]\d{2})", text)
    if not match:
        return False

    slip_time = match.group(1).replace(".", ":")
    try:
        slip_dt = datetime.datetime.strptime(slip_time, "%H:%M")
        slip_dt = slip_dt.replace(
            year=now.year, month=now.month, day=now.day
        )
        diff = abs((now - slip_dt).total_seconds())
        return diff <= 600
    except:
        return False

def make_invoice_id():
    return f"INV{int(time.time())}"

async def give_role_and_store(user_id: int, role_id: str, days: int):
    guild = bot.get_guild(GUILD_ID)
    member = guild.get_member(user_id)
    role = guild.get_role(int(role_id))

    if not member or not role:
        return False

    try:
        await member.add_roles(role)
        expires = int(time.time() + days * 86400)

        cur.execute("INSERT INTO subs VALUES (?,?,?)",
                    (str(user_id), str(role_id), expires))
        conn.commit()

        try:
            await member.send(
                f"✅ คุณได้รับยศ {role.name} ({days} วัน)"
            )
        except:
            pass

        return True
    except Exception as e:
        print("give_role error:", e)
        return False

async def remove_expired_roles():
    guild = bot.get_guild(GUILD_ID)
    rows = cur.execute(
        "SELECT user_id, role_id, expires_at FROM subs"
    ).fetchall()
    now = int(time.time())

    for user_id, role_id, exp in rows:
        if now >= exp:
            member = guild.get_member(int(user_id))
            role = guild.get_role(int(role_id))

            if member and role and role in member.roles:
                await member.remove_roles(role)
                try:
                    await member.send("⛔ ยศของคุณหมดอายุแล้ว")
                except:
                    pass

            cur.execute("DELETE FROM subs WHERE user_id=? AND role_id=?",
                        (user_id, role_id))
            conn.commit()
    """ปรับภาพสำหรับมือถือ"""
    img = np.array(pil_image)

    # แปลงสีเทา
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # ลดนอยส์
    blur = cv2.GaussianBlur(gray, (5, 5), 0)

    # เพิ่มความคม
    sharpen_kernel = np.array([[0, -1, 0],
                               [-1, 5, -1],
                               [0, -1, 0]])
    sharp = cv2.filter2D(blur, -1, sharpen_kernel)

    return sharp


def extract_text(image_bytes):
    pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    processed = preprocess_image(pil)
    text = pytesseract.image_to_string(processed, lang="tha+eng")
    return text


def check_company(text):
    return TARGET_COMPANY in text


def check_price(text):
    for amount in PRICES.keys():
        if str(amount) in text:
            return amount
    return None


def check_time(text):
    now = datetime.datetime.now()
    # หาเวลาในรูป เช่น 22:12
    match = re.search(r"(\d{1,2}[:.]\d{2})", text)
    if not match:
        return False
    slip_time = match.group(1).replace(".", ":")
    
    try:
        slip_dt = datetime.datetime.strptime(slip_time, "%H:%M")
        slip_dt = slip_dt.replace(year=now.year, month=now.month, day=now.day)
        diff = abs((now - slip_dt).total_seconds())
        return diff <= 600  # 10 นาที
    except:
        return False


@bot.event
async def on_ready():
    print(f"✅ Bot Online: {bot.user}")


@bot.event
async def on_message(message):
    if message.channel.id != SCAN_CHANNEL_ID:
        return

    if not message.attachments:
        return

    attachment = message.attachments[0]
    img_bytes = await attachment.read()

    text = extract_text(img_bytes)

    company_ok = check_company(text)
    price = check_price(text)
    time_ok = check_time(text)

    if not company_ok:
        await message.reply("❌ ไม่พบชื่อบริษัทบนสลิป")
        return
    
    if not price:
        await message.reply("❌ ไม่พบยอดเงินที่ถูกต้อง")
        return
    
    if not time_ok:
        await message.reply("❌ สลิปเกิน 10 นาที")
        return

    # ✅ ส่งของให้ลูกค้า
    key_channel_id = PRICES[price]
    key_channel = bot.get_channel(key_channel_id)

    if key_channel:
        await key_channel.send(f"<@{message.author.id}> ✅ ได้รับจำนวน **{price} บาท** แล้วครับ")
    
    await message.reply("✅ ตรวจสอบสำเร็จ ส่งของเรียบร้อยแล้ว!")
    await message.delete()

keep_alive()
bot.run(TOKEN)
ROLE_IDS = {
    "1":"1433747080660258867",
    "3":"1433747173039804477",
    "7":"1433747209475719332",
    "15":"1433747247295889489",
    "30":"1433747281932189826"
}

# QR IMAGE / Organization name (display)
QR_IMAGE_URL = "https://img2.pic.in.th/pic/b3353abf-04b1-4d82-a806-9859e0748f24.webp"
ORG_NAME = "บริษัท วันดีดี คอร์ปอเรชั่น จำกัด"

# ---------------- Discord Setup ----------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- Database ----------------
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

# ---------------- Helpers ----------------
def make_invoice_id() -> str:
    return f"INV{int(time.time())}"

async def give_role_and_store(user_id: int, role_id: str, days: int) -> bool:
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return False
    member = guild.get_member(int(user_id))
    role = guild.get_role(int(role_id))
    if not member or not role:
        return False
    try:
        await member.add_roles(role)
        expires = int(time.time() + days * 86400)
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
    for user_id, role_id, exp in rows:
        if now >= exp:
            member = guild.get_member(int(user_id)) if guild else None
            role = guild.get_role(int(role_id)) if guild else None
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role)
                except:
                    pass
                try:
                    await member.send("⛔ ยศของคุณหมดอายุแล้ว ถูกดึงคืนเรียบร้อยครับ")
                except:
                    pass
            cur.execute("DELETE FROM subs WHERE user_id=? AND role_id=?", (user_id, role_id))
            conn.commit()

# ---------------- Cron / Task ----------------
@tasks.loop(seconds=60)
async def check_expired():
    await remove_expired_roles()

# ---------------- Flask keepalive (for Replit/Railway) ----------------
app = Flask(__name__)

@app.get("/")
def index():
    return "Bot is running"

def run_flask():
    serve(app, host="0.0.0.0", port=3000)

# ---------------- UI Views ----------------
class BuyButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for plan, price in PRICES.items():
            days = DURATIONS.get(plan, 1)
            self.add_item(discord.ui.Button(
                label=f"{days} วัน • {price}฿",
                style=discord.ButtonStyle.green,
                custom_id=f"buy_{plan}"
            ))

class AdminApproveView(discord.ui.View):
    def __init__(self, invoice_id: str, user_id: str, role_id: str, days: int):
        super().__init__(timeout=None)
        self.invoice_id = invoice_id
        self.user_id = user_id
        self.role_id = role_id
        self.days = days

    @discord.ui.button(label="✅ อนุมัติ", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # mark paid and give role
        cur.execute("UPDATE invoices SET status='paid' WHERE invoice_id=?", (self.invoice_id,))
        conn.commit()
        await give_role_and_store(int(self.user_id), self.role_id, self.days)
        await interaction.response.send_message("✅ อนุมัติสำเร็จ และมอบยศให้ผู้ซื้อแล้ว", ephemeral=True)

    @discord.ui.button(label="❌ ปฏิเสธ", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        # open modal to get reason
        class RejectModal(discord.ui.Modal, title="ระบุเหตุผลการปฏิเสธ"):
            reason = discord.ui.TextInput(label="เหตุผล", style=discord.TextStyle.paragraph, required=True, max_length=500)

            async def on_submit(self, modal_interaction: discord.Interaction):
                reason_text = self.reason.value
                # update invoice status
                cur.execute("UPDATE invoices SET status='rejected' WHERE invoice_id=?", (self.invoice_id,))
                conn.commit()
                # dm buyer
                try:
                    user = await bot.fetch_user(int(self.user_id))
                    await user.send(f"⛔ คำสั่งซื้อ `{self.invoice_id}` ถูกปฏิเสธ: {reason_text}")
                except:
                    pass
                await modal_interaction.response.send_message("✅ ปฏิเสธและแจ้งผู้ซื้อเรียบร้อยแล้ว", ephemeral=True)

        await interaction.response.send_modal(RejectModal())

    @discord.ui.button(label="🔎 ดูข้อมูลผู้ซื้อ", style=discord.ButtonStyle.secondary)
    async def view_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        # fetch buyer info (basic)
        user = None
        try:
            user = await bot.fetch_user(int(self.user_id))
        except:
            pass
        info = f"User ID: {self.user_id}\nInvoice: {self.invoice_id}\nPlan: {self.days} วัน\nRole ID: {self.role_id}"
        if user:
            info = f"Username: {user}\n" + info
        await interaction.response.send_message(f"🔎 ข้อมูลผู้ซื้อ:\n{info}", ephemeral=True)

# ---------------- Commands ----------------
@bot.command()
async def buy(ctx):
    embed = discord.Embed(title="🛒 ซื้อแพ็ก", description="กดปุ่มเพื่อสร้างคำสั่งซื้อ", color=0x00ffcc)
    embed.add_field(name="บริษัท", value=ORG_NAME, inline=False)
    embed.set_image(url=QR_IMAGE_URL)
    embed.set_footer(text=f"TrueWallet: {TRUEWALLET_PHONE}")
    await ctx.send(embed=embed, view=BuyButtons())

# ---------------- Interaction handler (button presses) ----------------
@bot.event
async def on_interaction(interaction: discord.Interaction):
    # Only handle custom_id buttons starting with buy_
    try:
        data = interaction.data
        if not data:
            return
        cid = data.get("custom_id")
        if not cid:
            return
        if cid.startswith("buy_"):
            plan = cid.split("_",1)[1]
            price = PRICES.get(plan)
            days = DURATIONS.get(plan, 1)
            role_id = ROLE_IDS.get(plan)
            invoice_id = make_invoice_id()
            # store invoice pending
            cur.execute("INSERT INTO invoices VALUES (?,?,?,?,?,?,?)",
                        (invoice_id, str(interaction.user.id), role_id, plan, price, "pending", int(time.time())))
            conn.commit()

            # build embed invoice
            embed = discord.Embed(
                title="🧾 ใบคำสั่งซื้อ",
                description=(
                    f"**แพ็ก:** {days} วัน\n"
                    f"**ราคา:** {price} บาท\n"
                    f"**TrueMoney (ส่งลิงก์):** {TRUEWALLET_PHONE}\n"
                    f"**เลขอ้างอิง:** `{invoice_id}`\n\n"
                    "✅ กรุณาส่งลิงก์ TrueMoney (gift.truemoney.com/...) ในช่องนี้ (เฉพาะแชทเดียวกับบอท) หรือสแกน QR ด้านล่าง"
                ),
                color=0x00ffcc
            )
            embed.set_image(url=QR_IMAGE_URL)
            embed.set_footer(text=f"บริษัท: {ORG_NAME} • ระบบออโต้")
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        print("on_interaction error:", e)

# ---------------- Message handler (scan links) ----------------
@bot.event
async def on_message(msg: discord.Message):
    # allow commands processing
    await bot.process_commands(msg)

    # ignore bots
    if msg.author.bot:
        return

    # only process in SCAN_CHANNEL_ID
    if msg.channel.id != SCAN_CHANNEL_ID:
        return

    # check user's latest invoice pending
    row = cur.execute(
        "SELECT invoice_id, role_id, plan, price, status FROM invoices WHERE discord_id=? ORDER BY created_at DESC",
        (str(msg.author.id),)
    ).fetchone()

    if not row:
        # no invoice found for user
        return

    invoice_id, role_id, plan, price, status = row
    days = DURATIONS.get(str(plan), 1)

    content = (msg.content or "").strip()

    # simple TrueMoney link detection
    if "gift.truemoney.com" in content.lower() or "gift.truemoney.com/campaign" in content.lower():
        # Auto Full = automatically approve & assign
        cur.execute("UPDATE invoices SET status='paid' WHERE invoice_id=?", (invoice_id,))
        conn.commit()

        # give role
        ok = await give_role_and_store(int(msg.author.id), role_id, days)

        # notify admin with buttons to undo/view if needed
        guild = bot.get_guild(GUILD_ID)
        admin_ch = guild.get_channel(ADMIN_CHANNEL_ID) if guild else None
        view = AdminApproveView(invoice_id=invoice_id, user_id=str(msg.author.id), role_id=role_id, days=days)
        admin_msg = f"🔔 TRUE MONEY ตรวจสอบอัตโนมัติ: <@{msg.author.id}> (invoice {invoice_id})\nลิงก์ที่ส่ง:\n{content}"
        try:
            if admin_ch:
                await admin_ch.send(admin_msg, view=view)
            else:
                # fallback notify server owner
                owner = (await bot.application_info()).owner
                try:
                    await owner.send(admin_msg, view=view)
                except:
                    pass
        except Exception as e:
            print("notify admin error:", e)

        # feedback to user (ephemeral by DM)
        try:
            await msg.author.send("✅ ขอบคุณ! ระบบตรวจสอบอัตโนมัติพบลิงก์และมอบยศให้เรียบร้อยแล้ว หากต้องการข้อมูลเพิ่มเติม ติดต่อแอดมิน")
        except:
            try:
                await msg.channel.send(f"<@{msg.author.id}> ✅ ระบบตรวจสอบอัตโนมัติพบลิงก์และมอบยศให้เรียบร้อยแล้ว")
            except:
                pass

        # remove user's message in public channel for privacy
        try:
            await msg.delete()
        except:
            pass

    # else ignore other messages in scan channel

# ---------------- On ready ----------------
@bot.event
async def on_ready():
    print("✅ Bot Online:", bot.user)
    check_expired.start()
    threading.Thread(target=run_flask, daemon=True).start()

# ---------------- Start ----------------
if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN not set in environment.")
    else:
        bot.run(TOKEN)
