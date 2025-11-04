import os
import time
import threading
import discord
from discord.ext import commands, tasks
from discord import ui
from dotenv import load_dotenv
from flask import Flask
from waitress import serve
import sqlite3

load_dotenv()

# ---------------- CONFIG ----------------
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
SCAN_CHANNEL_ID = int(os.getenv("SCAN_CHANNEL_ID"))
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID"))
TRUEWALLET_PHONE = os.getenv("TRUEWALLET_PHONE")

QR_BANK_URL = "https://img2.pic.in.th/pic/b3353abf-04b1-4d82-a806-9859e0748f24-13025bdde0f821678.webp"

# ราคา / วัน
PRICES = {"1": 20, "3": 40, "7": 80, "15": 150, "30": 300}
DAYS = {"1": 1, "3": 3, "7": 7, "15": 15, "30": 30}

# Role IDs
ROLE_IDS = {
    "1": "1433747080660258867",
    "3": "1433747173039804477",
    "7": "1433747209475719332",
    "15": "1433747247295889489",
    "30": "1433747281932189826"
}

# ---------------- DISCORD ----------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- DATABASE ----------------
conn = sqlite3.connect("database.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS invoices(
    invoice_id TEXT PRIMARY KEY,
    discord_id TEXT,
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


# ---------------- FUNCTION ----------------
def create_invoice_id():
    return f"INV{int(time.time())}"


async def give_role(user_id: str, plan: str):
    guild = bot.get_guild(GUILD_ID)
    member = guild.get_member(int(user_id))
    role = guild.get_role(int(ROLE_IDS[plan]))
    days = DAYS[plan]

    if member and role:
        await member.add_roles(role)

        expire = int(time.time() + days * 86400)
        cur.execute("INSERT INTO subs VALUES (?, ?, ?)", (user_id, ROLE_IDS[plan], expire))
        conn.commit()

        try:
            await member.send(f"✅ ยืนยันสำเร็จ! คุณได้รับยศ {days} วัน")
        except:
            pass


# ---------------- SEND TO ADMIN ----------------
async def send_to_admin(invoice_id, user_id, plan, content=None, image=None):
    admin_ch = bot.get_channel(ADMIN_CHANNEL_ID)

    embed = discord.Embed(
        title="🔔 งานตรวจสอบใหม่",
        description=f"ผู้ใช้: <@{user_id}>\nแพ็ก {DAYS[plan]} วัน ({PRICES[plan]}฿)\nInvoice: `{invoice_id}`",
        color=0xffaa00
    )

    if content:
        embed.add_field(name="ข้อความ", value=content, inline=False)

    if image:
        embed.set_image(url=image)

    msg = await admin_ch.send(embed=embed)
    await msg.edit(view=AdminView(invoice_id, user_id, plan, msg))


# ---------------- ADMIN UI ----------------
class RejectModal(ui.Modal, title="เหตุผลไม่อนุมัติ"):
    reason = ui.TextInput(label="เหตุผล", style=discord.TextStyle.paragraph)

    def __init__(self, invoice_id, user_id, admin_msg):
        super().__init__()
        self.invoice_id = invoice_id
        self.user_id = user_id
        self.admin_msg = admin_msg

    async def on_submit(self, interaction):
        cur.execute("UPDATE invoices SET status='rejected' WHERE invoice_id=?", (self.invoice_id,))
        conn.commit()

        user = await bot.fetch_user(int(self.user_id))
        try:
            await user.send(f"❌ คำสั่งซื้อถูกปฏิเสธ\nเหตุผล: {self.reason.value}")
        except:
            pass

        try:
            await self.admin_msg.delete()
        except:
            pass

        await interaction.response.send_message("✅ ปฏิเสธเรียบร้อย", ephemeral=True)


class AdminView(ui.View):
    def __init__(self, invoice_id, user_id, plan, admin_msg):
        super().__init__(timeout=None)
        self.invoice_id = invoice_id
        self.user_id = user_id
        self.plan = plan
        self.admin_msg = admin_msg

    @ui.button(label="✅ อนุมัติ", style=discord.ButtonStyle.green)
    async def approve(self, interaction, button):
        cur.execute("UPDATE invoices SET status='paid' WHERE invoice_id=?", (self.invoice_id,))
        conn.commit()

        await give_role(self.user_id, self.plan)

        try:
            await self.admin_msg.delete()
        except:
            pass

        await interaction.response.send_message("✅ อนุมัติสำเร็จ", ephemeral=True)

    @ui.button(label="❌ ไม่อนุมัติ", style=discord.ButtonStyle.red)
    async def reject(self, interaction, button):
        await interaction.response.send_modal(RejectModal(
            self.invoice_id,
            self.user_id,
            self.admin_msg
        ))


# ---------------- BUY COMMAND ----------------
@bot.command()
async def buy(ctx):
    class BuyView(ui.View):
        def __init__(self):
            super().__init__()
            for plan, price in PRICES.items():
                self.add_item(ui.Button(
                    label=f"{DAYS[plan]} วัน • {price}฿",
                    custom_id=f"buy_{plan}",
                    style=discord.ButtonStyle.green
                ))

    embed = discord.Embed(
        title="🛒 ซื้อแพ็ก",
        description="เลือกแพ็กที่คุณต้องการด้านล่าง",
        color=0x00ffcc
    )
    embed.add_field(name="เบอร์ TrueMoney", value=TRUEWALLET_PHONE)
    embed.set_image(url=QR_BANK_URL)

    await ctx.send(embed=embed, view=BuyView())


# ---------------- BUTTON HANDLER ----------------
@bot.event
async def on_interaction(interaction):
    if not interaction.data:
        return

    cid = interaction.data.get("custom_id")
    if cid and cid.startswith("buy_"):
        plan = cid.split("_")[1]
        price = PRICES[plan]
        invoice_id = create_invoice_id()

        cur.execute("INSERT INTO invoices VALUES (?, ?, ?, ?, ?, ?)",
                    (invoice_id, str(interaction.user.id), plan, price, "pending", int(time.time())))
        conn.commit()

        embed = discord.Embed(
            title="🧾 ใบสั่งซื้อ",
            description=f"แพ็ก: {DAYS[plan]} วัน\nราคา: {price}฿\nInvoice: `{invoice_id}`\n\n"
                        f"✅ ส่ง **ลิงก์ซอง** หรือ **ภาพสลิป**\nในห้องตรวจสอบเท่านั้น",
            color=0x00ffcc
        )
        embed.set_image(url=QR_BANK_URL)

        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------- MESSAGE HANDLER (ลูกค้า) ----------------
@bot.event
async def on_message(msg):
    await bot.process_commands(msg)

    if msg.author.bot:
        return

    if msg.channel.id != SCAN_CHANNEL_ID:
        return

    row = cur.execute(
        "SELECT invoice_id, plan FROM invoices WHERE discord_id=? AND status='pending' ORDER BY created_at DESC",
        (str(msg.author.id),)
    ).fetchone()

    if not row:
        await msg.delete()
        return

    invoice_id, plan = row
    plan = str(plan)

    # ซอง TrueMoney
    if "gift.truemoney.com" in msg.content.lower():
        await send_to_admin(invoice_id, msg.author.id, plan, content=msg.content)
        await msg.delete()
        return

    # ภาพสลิป
    if msg.attachments:
        await send_to_admin(invoice_id, msg.author.id, plan, image=msg.attachments[0].url)
        await msg.delete()
        return

    # ข้อความอื่นๆ ลบออก
    await msg.delete()


# ---------------- EXPIRE SYSTEM ----------------
@tasks.loop(seconds=30)
async def check_expired():
    guild = bot.get_guild(GUILD_ID)
    now = int(time.time())

    rows = cur.execute("SELECT user_id, role_id, expires_at FROM subs").fetchall()

    for uid, rid, exp in rows:
        if now >= exp:
            member = guild.get_member(int(uid))
            role = guild.get_role(int(rid))

            if member and role in member.roles:
                await member.remove_roles(role)
                try:
                    await member.send("⛔ ยศหมดอายุแล้ว")
                except:
                    pass

            cur.execute("DELETE FROM subs WHERE user_id=? AND role_id=?", (uid, rid))
            conn.commit()


# ---------------- KEEP ALIVE ----------------
app = Flask(__name__)

@app.get("/")
def home():
    return "Bot Running"

def run_flask():
    serve(app, host="0.0.0.0", port=3000)


# ---------------- START ----------------
@bot.event
async def on_ready():
    print("✅ Bot Online:", bot.user)
    check_expired.start()
    threading.Thread(target=run_flask, daemon=True).start()


bot.run(TOKEN)
