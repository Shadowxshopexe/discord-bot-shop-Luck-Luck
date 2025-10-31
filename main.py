# main.py
import os
import io
import time
import sqlite3
import threading
import requests
from PIL import Image
import imagehash

# keep-alive + flask app
from keep_alive import keep_alive, app as flask_app
keep_alive()

from flask import request
import discord
from discord.ext import tasks, commands
from dotenv import load_dotenv

load_dotenv()

# ---------------- CONFIG ----------------
TOKEN = os.getenv("DISCORD_TOKEN")  # ***เก็บใน Replit Secrets***
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID", "0"))
TRUEWALLET_PHONE = os.getenv("TRUEWALLET_PHONE", "0808432571")
QR_IMAGE_URL = os.getenv("QR_IMAGE_URL", "https://img2.pic.in.th/pic/b3353abf-04b1-4d82-a806-9859e0748f24.webp")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", None)

PRICES = {"1":20, "3":40, "7":80, "15":150, "30":300}
ROLE_IDS = {
    "1":"1433747080660258867",
    "3":"1433747173039804477",
    "7":"1433747209475719332",
    "15":"1433747247295889489",
    "30":"1433747281932189826"
}
DURATIONS = {"1":1, "3":3, "7":7, "15":15, "30":30}

# ---------------- DISCORD SETUP ----------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- DATABASE ----------------
conn = sqlite3.connect("database.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS invoices(
    invoice_id TEXT PRIMARY KEY,
    discord_id TEXT,
    role_id TEXT,
    plan TEXT,
    price INTEGER,
    status TEXT,
    created_at INTEGER
)""")
cur.execute("""CREATE TABLE IF NOT EXISTS subs(
    user_id TEXT,
    role_id TEXT,
    expires_at INTEGER
)""")
conn.commit()

# ---------------- QR HASH (reference) ----------------
REF_QR_HASH = None
def load_qr_hash():
    global REF_QR_HASH
    try:
        r = requests.get(QR_IMAGE_URL, timeout=10)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("L")
        REF_QR_HASH = imagehash.phash(img)
        print("Loaded reference QR hash.")
    except Exception as e:
        REF_QR_HASH = None
        print("Cannot load QR image:", e)

load_qr_hash()

def compute_hash(bts):
    try:
        img = Image.open(io.BytesIO(bts)).convert("L")
        return imagehash.phash(img)
    except Exception:
        return None

def is_similar(h1, h2, max_dist=6):
    try:
        return (h1 - h2) <= max_dist
    except Exception:
        return False

# ---------------- HELPERS ----------------
async def give_role(user_id, role_id, days):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return False
    member = guild.get_member(int(user_id))
    role = guild.get_role(int(role_id))
    if not member or not role:
        return False
    try:
        await member.add_roles(role)
    except Exception as e:
        print("Add role error:", e)
        return False
    expires = int(time.time() + days * 86400)
    cur.execute("INSERT INTO subs VALUES (?,?,?)", (str(user_id), str(role_id), expires))
    conn.commit()
    try:
        await member.send(f"✅ ระบบอนุมัติแล้ว คุณได้รับยศ {role.name} ({days} วัน)")
    except:
        pass
    return True

async def notify_admin(msg, view=None):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    ch = guild.get_channel(ADMIN_CHANNEL_ID)
    if ch:
        try:
            await ch.send(msg, view=view)
        except Exception as e:
            print("notify_admin error:", e)

# ---------------- AUTO REMOVE ROLE ----------------
@tasks.loop(seconds=60)
async def check_expired():
    guild = bot.get_guild(GUILD_ID)
    rows = cur.execute("SELECT user_id, role_id, expires_at FROM subs").fetchall()
    now = int(time.time())
    for uid, rid, exp in rows:
        if now >= exp:
            member = guild.get_member(int(uid)) if guild else None
            role = guild.get_role(int(rid)) if guild else None
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role)
                    await member.send("⛔ ยศของคุณหมดอายุแล้ว")
                except:
                    pass
            cur.execute("DELETE FROM subs WHERE user_id=? AND role_id=?", (uid, rid))
            conn.commit()

# ---------------- WEBHOOK (optional) ----------------
@flask_app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET:
        header = request.headers.get("X-SECRET") or request.headers.get("X-Webhook-Secret")
        if header != WEBHOOK_SECRET:
            return "INVALID", 403
    data = request.json or {}
    invoice_id = data.get("invoice_id")
    status = data.get("status")
    if invoice_id and status == "paid":
        row = cur.execute("SELECT discord_id, role_id, plan FROM invoices WHERE invoice_id=?", (invoice_id,)).fetchone()
        if row:
            discord_id, role_id, plan = row
            days = DURATIONS.get(plan, 1)
            bot.loop.create_task(give_role(discord_id, role_id, days))
            cur.execute("UPDATE invoices SET status='paid' WHERE invoice_id=?", (invoice_id,))
            conn.commit()
            return "OK", 200
    return "IGNORED", 200

# ---------------- BUY COMMAND ----------------
@bot.command()
async def buy(ctx):
    class Buttons(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            for plan, price in PRICES.items():
                days = DURATIONS[plan]
                b = discord.ui.Button(label=f"{days} วัน • {price}฿", custom_id=f"buy_{plan}", style=discord.ButtonStyle.green)
                self.add_item(b)
    embed = discord.Embed(title="🛒 เลือกแพ็กเกจ", description="กดเลือกแพ็กที่คุณต้องการ", color=0x00ffcc)
    await ctx.send(embed=embed, view=Buttons())

# ---------------- HANDLE BUTTON (interaction) ----------------
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if not interaction.data:
        return
    cid = interaction.data.get("custom_id")
    if not cid or not cid.startswith("buy_"):
        return
    plan = cid.split("_")[1]
    price = PRICES.get(plan, 0)
    days = DURATIONS.get(plan, 1)
    role_id = ROLE_IDS.get(plan)
    invoice_id = f"INV{int(time.time())}"
    cur.execute("INSERT INTO invoices VALUES (?,?,?,?,?,?,?)", (invoice_id, str(interaction.user.id), role_id, plan, price, "pending", int(time.time())))
    conn.commit()
    qr_url = QR_IMAGE_URL
    tmn_link = f"https://pay.example.com/truewallet?to={TRUEWALLET_PHONE}&amount={price}&ref={invoice_id}"
    embed = discord.Embed(
        title="🧾 ใบคำสั่งซื้อ",
        description=(
            f"**แพ็ก:** {days} วัน\n"
            f"**ราคา:** {price} บาท\n"
            f"**TrueMoney:** {TRUEWALLET_PHONE}\n"
            f"**เลขอ้างอิง:** `{invoice_id}`\n\n"
            "✅ ธนาคาร: แนบรูปสลิปที่นี่ (เฉพาะคุณเห็น)\n"
            "✅ TrueMoney: ส่งลิงก์ซอง/ลิงก์ Gift (paste link) ที่นี่"
        ),
        color=0x00ffcc
    )
    embed.set_image(url=qr_url)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------- MODAL for reject reason ----------------
class RejectReasonModal(discord.ui.Modal, title="เหตุผลที่ไม่อนุมัติ"):
    def __init__(self, user_id, invoice_id):
        super().__init__()
        self.user_id = user_id
        self.invoice_id = invoice_id
        self.reason = discord.ui.TextInput(
            label="กรุณากรอกเหตุผล",
            style=discord.TextStyle.long,
            required=True,
            placeholder="เช่น สลิปไม่ตรง จำนวนเงินไม่ถูกต้อง ฯลฯ"
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        # update DB
        cur.execute("UPDATE invoices SET status='rejected' WHERE invoice_id=?", (self.invoice_id,))
        conn.commit()
        # DM user with reason
        try:
            user = await bot.fetch_user(int(self.user_id))
            await user.send(
                f"❌ **คำสั่งซื้อถูกปฏิเสธ**\n"
                f"📌 เหตุผล: **{self.reason.value}**\n"
                "กรุณาตรวจสอบข้อมูลให้ถูกต้องแล้วลองใหม่ได้ครับ 🙏"
            )
        except:
            pass
        await interaction.response.send_message("❌ ปฏิเสธสำเร็จ พร้อมส่งเหตุผลให้ลูกค้าเรียบร้อย", ephemeral=True)

# ---------------- ADMIN VIEW: approve / reject / info ----------------
class ApproveButton(discord.ui.View):
    def __init__(self, user_id, role_id, days, invoice_id):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.role_id = role_id
        self.days = days
        self.invoice_id = invoice_id

    @discord.ui.button(label="✅ อนุมัติ", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        cur.execute("UPDATE invoices SET status='paid' WHERE invoice_id=?", (self.invoice_id,))
        conn.commit()
        await give_role(self.user_id, self.role_id, self.days)
        await interaction.response.send_message("✅ อนุมัติสำเร็จ ให้ยศเรียบร้อย", ephemeral=True)
        try:
            user = await bot.fetch_user(int(self.user_id))
            await user.send(f"✅ ยอดชำระถูกอนุมัติแล้ว! ระบบได้ให้ยศ {self.days} วันแก่คุณเรียบร้อย ❤️")
        except:
            pass

    @discord.ui.button(label="❌ ไม่อนุมัติ", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RejectReasonModal(self.user_id, self.invoice_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="📄 ดูข้อมูลผู้ซื้อ", style=discord.ButtonStyle.blurple)
    async def info(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = cur.execute("SELECT price, plan, created_at FROM invoices WHERE invoice_id=?", (self.invoice_id,)).fetchone()
        if not row:
            await interaction.response.send_message("ไม่พบข้อมูลคำสั่งซื้อ", ephemeral=True)
            return
        price, plan, created_at = row
        timestamp = f"<t:{created_at}:R>"
        embed = discord.Embed(title="📄 ข้อมูลผู้สั่งซื้อ", color=0x00aaff)
        embed.add_field(name="ผู้ใช้", value=f"<@{self.user_id}>", inline=False)
        embed.add_field(name="แพ็ก", value=f"{plan} วัน", inline=True)
        embed.add_field(name="ราคา", value=f"{price} บาท", inline=True)
        embed.add_field(name="เวลาออกใบ", value=timestamp, inline=False)
        embed.add_field(name="Invoice", value=self.invoice_id, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------- HANDLE MESSAGES (attachments / truemoney links) ----------------
@bot.event
async def on_message(msg: discord.Message):
    if msg.author.bot:
        return
    # check latest invoice
    row = cur.execute("SELECT invoice_id, role_id, plan, price, status FROM invoices WHERE discord_id=? ORDER BY created_at DESC", (str(msg.author.id),)).fetchone()
    if not row:
        await bot.process_commands(msg)
        return
    invoice_id, role_id, plan, price, status = row
    days = DURATIONS.get(str(plan), 1)
    # attachments (bank slip)
    if msg.attachments:
        att = msg.attachments[0]
        try:
            bts = await att.read()
            user_hash = compute_hash(bts)
            if user_hash and REF_QR_HASH and is_similar(user_hash, REF_QR_HASH):
                cur.execute("UPDATE invoices SET status='paid' WHERE invoice_id=?", (invoice_id,))
                conn.commit()
                try: await msg.delete()
                except: pass
                await give_role(msg.author.id, role_id, days)
                await notify_admin(f"✅ AUTO BANK: <@{msg.author.id}> จ่ายสำเร็จ (invoice {invoice_id})")
                return
            else:
                await msg.author.send("❌ สลิปไม่ตรง QR กรุณาลองใหม่หรือแจ้งแอดมิน")
                await notify_admin(f"⚠️ สลิปไม่ตรง: <@{msg.author.id}> (invoice {invoice_id})")
                return
        except Exception:
            await msg.author.send("ไม่สามารถอ่านรูปได้ กรุณาลองอีกครั้ง")
            return
    # truemoney link -> forward to admin with approve view
    content = (msg.content or "").strip()
    if "truemoney" in content.lower() or "gift.truemoney" in content.lower():
        try:
            await msg.delete()
        except:
            pass
        view = ApproveButton(msg.author.id, role_id, days, invoice_id)
        await notify_admin(f"🔔 TRUE MONEY ตรวจสอบ: <@{msg.author.id}> (invoice {invoice_id})\nลิงก์ที่ส่ง:\n{content}", view=view)
        try:
            await msg.author.send("✅ ส่งลิงก์แล้ว กำลังรอแอดมินตรวจสอบ")
        except:
            pass
        return
    await bot.process_commands(msg)

# ---------------- ON READY ----------------
@bot.event
async def on_ready():
    print("✅ Bot Online:", bot.user)
    check_expired.start()

# ---------------- START BOT ----------------
if not TOKEN:
    print("Error: DISCORD_TOKEN not set in environment.")
else:
    bot.run(TOKEN)
