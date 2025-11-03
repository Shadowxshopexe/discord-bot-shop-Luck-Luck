import os
import time
import threading
import sqlite3
import discord
from discord.ext import commands, tasks
from discord import ui
from dotenv import load_dotenv
from flask import Flask
from waitress import serve

load_dotenv()

# ---------------- CONFIG ----------------
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
SCAN_CHANNEL_ID = int(os.getenv("SCAN_CHANNEL_ID"))          # ห้องที่ลูกค้าส่งสลิป/ซอง
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID"))        # ห้องแอดมิน
TRUEWALLET_PHONE = os.getenv("TRUEWALLET_PHONE")

QR_BANK_URL = "https://img2.pic.in.th/pic/b3353abf-04b1-4d82-a806-9859e0748f24-13025bdde0f821678.webp"

PRICES = {"1": 20, "3": 40, "7": 80, "15": 150, "30": 300}
ROLE_IDS = {
    "1": "1433747080660258867",
    "3": "1433747173039804477",
    "7": "1433747209475719332",
    "15": "1433747247295889489",
    "30": "1433747281932189826"
}
DAYS = {"1": 1, "3": 3, "7": 7, "15": 15, "30": 30}

# ---------------- DISCORD ----------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- DATABASE ----------------
conn = sqlite3.connect("database.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS subs(
    user_id TEXT,
    role_id TEXT,
    expires_at INTEGER
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS invoices(
    invoice_id TEXT PRIMARY KEY,
    discord_id TEXT,
    plan TEXT,
    price INTEGER,
    role_id TEXT,
    status TEXT,
    created_at INTEGER
)
""")
conn.commit()

# ---------------- FUNCTIONS ----------------

def create_invoice_id():
    return f"INV{int(time.time())}"

async def give_role(user_id, role_id, days):
    guild = bot.get_guild(GUILD_ID)
    member = guild.get_member(int(user_id))
    role = guild.get_role(int(role_id))

    if not member or not role:
        return False

    await member.add_roles(role)
    expire = int(time.time() + days * 86400)

    cur.execute("INSERT INTO subs VALUES (?,?,?)", (user_id, role_id, expire))
    conn.commit()

    try:
        await member.send(f"✅ ยศของคุณได้รับการอนุมัติแล้ว ({days} วัน)")
    except:
        pass

    return True

async def send_to_admin(invoice_id, user_id, plan, content=None, image_url=None):
    guild = bot.get_guild(GUILD_ID)
    ch = guild.get_channel(ADMIN_CHANNEL_ID)

    view = AdminView(invoice_id, user_id, plan)

    embed = discord.Embed(
        title="🔔 คำสั่งซื้อใหม่",
        description=(
            f"ผู้ใช้: <@{user_id}>\n"
            f"แพ็ก: {plan} วัน ({PRICES[plan]}฿)\n"
            f"หมายเลขคำสั่งซื้อ: `{invoice_id}`"
        ),
        color=0xffcc00
    )

    if content:
        embed.add_field(name="ลิงก์ซอง", value=content, inline=False)

    if image_url:
        embed.set_image(url=image_url)

    await ch.send(embed=embed, view=view)

# ---------------- ADMIN UI ----------------

class ReasonModal(ui.Modal, title="เหตุผลการไม่อนุมัติ"):
    reason = ui.TextInput(label="เหตุผล", style=discord.TextStyle.paragraph)

    def __init__(self, invoice_id, user_id):
        super().__init__()
        self.invoice_id = invoice_id
        self.user_id = user_id

    async def on_submit(self, interaction):
        cur.execute("UPDATE invoices SET status='rejected' WHERE invoice_id=?", (self.invoice_id,))
        conn.commit()

        try:
            user = await bot.fetch_user(int(self.user_id))
            await user.send(f"⛔ คำสั่งซื้อ `{self.invoice_id}` ถูกปฏิเสธ\n\nเหตุผล: {self.reason.value}")
        except:
            pass

        await interaction.response.send_message("✅ ส่งเหตุผลให้ลูกค้าแล้ว", ephemeral=True)

class AdminView(ui.View):
    def __init__(self, invoice_id, user_id, plan):
        super().__init__(timeout=None)
        self.invoice_id = invoice_id
        self.user_id = user_id
        self.plan = plan

    @ui.button(label="✅ อนุมัติ", style=discord.ButtonStyle.green)
    async def approve(self, interaction, button):
        cur.execute("UPDATE invoices SET status='paid' WHERE invoice_id=?", (self.invoice_id,))
        conn.commit()

        role_id = ROLE_IDS[self.plan]
        days = DAYS[self.plan]

        await give_role(self.user_id, role_id, days)
        await interaction.response.send_message("✅ อนุมัติแล้ว", ephemeral=True)

    @ui.button(label="❌ ไม่อนุมัติ", style=discord.ButtonStyle.red)
    async def reject(self, interaction, button):
        await interaction.response.send_modal(ReasonModal(self.invoice_id, self.user_id))

# ---------------- BUY COMMAND ----------------

@bot.command()
async def buy(ctx):
    class BuyButtons(ui.View):
        def __init__(self):
            super().__init__()
            for plan, price in PRICES.items():
                days = DAYS[plan]
                self.add_item(ui.Button(
                    label=f"{days} วัน • {price}฿",
                    custom_id=f"buy_{plan}",
                    style=discord.ButtonStyle.green
                ))

    embed = discord.Embed(
        title="🛒 ซื้อแพ็ก",
        description="กดปุ่มด้านล่างเพื่อเลือกแพ็ก\nส่งสลิป/ซองในห้องตรวจสอบเท่านั้น",
        color=0x00ffcc
    )
    embed.add_field(name="TrueMoney", value=TRUEWALLET_PHONE)
    embed.set_image(url=QR_BANK_URL)

    await ctx.send(embed=embed, view=BuyButtons())

# ---------------- BUTTON HANDLER ----------------

@bot.event
async def on_interaction(interaction):
    if not interaction.data:
        return

    cid = interaction.data.get("custom_id")
    if cid and cid.startswith("buy_"):
        plan = cid.split("_")[1]
        price = PRICES[plan]
        role_id = ROLE_IDS[plan]
        invoice_id = create_invoice_id()

        cur.execute("INSERT INTO invoices VALUES (?,?,?,?,?,?,?)",
                    (invoice_id, str(interaction.user.id), plan, price, role_id, "pending", int(time.time())))
        conn.commit()

        embed = discord.Embed(
            title="🧾 ใบคำสั่งซื้อ",
            description=(f"แพ็ก: {plan} วัน\nราคา: {price} บาท\n\nInvoice: `{invoice_id}`\n"
                         "ส่ง **ลิงก์ซองหรือภาพสลิป** ในห้องตรวจสอบทันที"),
            color=0x00ffcc
        )
        embed.set_image(url=QR_BANK_URL)

        await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------- MESSAGE HANDLER ----------------

@bot.event
async def on_message(msg):
    await bot.process_commands(msg)

    if msg.author.bot:
        return

    if msg.channel.id != SCAN_CHANNEL_ID:
        return

    # หา invoice ล่าสุดของลูกค้า
    row = cur.execute(
        "SELECT invoice_id, plan FROM invoices WHERE discord_id=? ORDER BY created_at DESC",
        (str(msg.author.id),)
    ).fetchone()

    if not row:
        await msg.delete()
        return

    invoice_id, plan = row

    # ✨ ลิงก์ซอง TrueMoney
    if "gift.truemoney.com" in (msg.content or ""):
        await send_to_admin(invoice_id, msg.author.id, plan, content=msg.content)
        await msg.author.send("✅ ส่งลิงก์ซองให้แอดมินตรวจสอบแล้ว")
        await msg.delete()
        return

    # ✨ รูปภาพสลิป
    if msg.attachments:
        img_url = msg.attachments[0].url
        await send_to_admin(invoice_id, msg.author.id, plan, image_url=img_url)
        await msg.author.send("✅ ส่งสลิปให้แอดมินตรวจสอบแล้ว")
        await msg.delete()
        return

    # ✨ ข้อความอื่น ลบทิ้ง
    await msg.delete()

# ---------------- EXPIRE ROLES ----------------

@tasks.loop(seconds=30)
async def check_expired():
    guild = bot.get_guild(GUILD_ID)

    rows = cur.execute("SELECT user_id, role_id, expires_at FROM subs").fetchall()
    now = int(time.time())

    for uid, rid, exp in rows:
        if now >= exp:
            member = guild.get_member(int(uid))
            role = guild.get_role(int(rid))

            if member and role in member.roles:
                await member.remove_roles(role)
                try:
                    await member.send("⛔ ยศหมดอายุแล้ว ถูกดึงคืนแล้ว")
                except:
                    pass

            cur.execute("DELETE FROM subs WHERE user_id=? AND role_id=?", (uid, rid))
            conn.commit()

# ---------------- KEEPALIVE FOR RAILWAY ----------------

app = Flask(__name__)

@app.get("/")
def home():
    return "Bot Running"

def run_flask():
    serve(app, host="0.0.0.0", port=3000)

# ---------------- START BOT ----------------

@bot.event
async def on_ready():
    print("✅ Bot Online:", bot.user)
    check_expired.start()
    threading.Thread(target=run_flask, daemon=True).start()

bot.run(TOKEN)
