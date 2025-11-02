import os
import re
import time
import sqlite3
import requests
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# ---------------- CONFIG ----------------
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
ADMIN_CHANNEL_ID = 1433789961403895999
SLIP_CHANNEL_ID = 1433762345058041896
TRUEWALLET_PHONE = os.getenv("TRUEWALLET_PHONE", "0808432571")

COMPANY_NAME = "บริษัท วันดีดี คอร์ปอเรชั่น จำกัด"

PRICES = {"1":20, "3":40, "7":80, "15":150, "30":300}
ROLE_IDS = {
    "1":"1433747080660258867",
    "3":"1433747173039804477",
    "7":"1433747209475719332",
    "15":"1433747247295889489",
    "30":"1433747281932189826"
}
DURATIONS = {"1":1, "3":3, "7":7, "15":15, "30":30}

# API ฟรีสำหรับตรวจสลิป
SLIP_API = "https://script.google.com/macros/s/AKfycbxw5rjL2slip-check-lite/exec"

# ---------------- DISCORD BOT SETUP ----------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- DATABASE ----------------
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


# ---------------- FUNCTIONS ----------------
async def give_role(user_id, role_id, days):
    guild = bot.get_guild(GUILD_ID)
    member = guild.get_member(int(user_id))
    role = guild.get_role(int(role_id))

    if member and role:
        await member.add_roles(role)
        expires = int(time.time() + days * 86400)
        cur.execute("INSERT INTO subs VALUES (?,?,?)", (str(user_id), str(role_id), expires))
        conn.commit()
        try:
            await member.send(f"✅ ระบบอนุมัติแล้ว คุณได้รับยศ {role.name} ({days} วัน)")
        except:
            pass


def get_last_invoice(user_id):
    row = cur.execute(
        "SELECT invoice_id, role_id, plan, price, status FROM invoices WHERE discord_id=? ORDER BY created_at DESC",
        (str(user_id),)
    ).fetchone()
    if not row:
        return None
    invoice_id, role_id, plan, price, status = row
    return {
        "invoice_id": invoice_id,
        "role_id": role_id,
        "plan": plan,
        "price": price,
        "status": status
    }


def slip_check_api(image_bytes):
    files = {"file": ("slip.jpg", image_bytes, "image/jpeg")}
    try:
        r = requests.post(SLIP_API, files=files, timeout=15)
        return r.json()
    except:
        return None


# ---------------- BUY COMMAND ----------------
@bot.command()
async def buy(ctx):

    class BuyButtons(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            for plan, p in PRICES.items():
                days = DURATIONS[plan]
                self.add_item(
                    discord.ui.Button(
                        label=f"{days} วัน • {p}฿",
                        custom_id=f"buy_{plan}",
                        style=discord.ButtonStyle.green
                    )
                )

    embed = discord.Embed(
        title="🛒 เลือกแพ็กเกจ",
        description="กดเลือกแพ็กที่ต้องการจากด้านล่าง",
        color=0x00ffcc
    )
    await ctx.send(embed=embed, view=BuyButtons())


# ---------------- BUY BUTTON ----------------
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if not interaction.data:
        return

    cid = interaction.data.get("custom_id")
    if cid and cid.startswith("buy_"):

        plan = cid.split("_")[1]
        price = PRICES[plan]
        role_id = ROLE_IDS[plan]
        days = DURATIONS[plan]

        invoice_id = f"INV{int(time.time())}"

        cur.execute(
            "INSERT INTO invoices VALUES (?,?,?,?,?,?,?)",
            (invoice_id, str(interaction.user.id), role_id, plan, price, "pending", int(time.time()))
        )
        conn.commit()

        embed = discord.Embed(
            title="🧾 ใบคำสั่งซื้อ",
            description=(
                f"**แพ็ก:** {days} วัน\n"
                f"**ราคา:** {price} บาท\n"
                f"**เลขอ้างอิง:** `{invoice_id}`\n\n"
                "✅ กรุณาชำระและส่งสลิปในห้องที่กำหนด\n"
                f"📌 ห้องส่งสลิป: <#{SLIP_CHANNEL_ID}>"
            ),
            color=0x00ffcc
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------- SLIP SCAN ----------------
class AdminApproveView(discord.ui.View):
    def __init__(self, user_id, role_id, days, invoice_id):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.role_id = role_id
        self.days = days
        self.invoice_id = invoice_id

    @discord.ui.button(label="✅ อนุมัติ", style=discord.ButtonStyle.green)
    async def approve(self, interaction, button):
        cur.execute("UPDATE invoices SET status='paid' WHERE invoice_id=?", (self.invoice_id,))
        conn.commit()
        await give_role(self.user_id, self.role_id, self.days)
        await interaction.response.send_message("✅ อนุมัติแล้ว", ephemeral=True)

    @discord.ui.button(label="❌ ปฏิเสธ", style=discord.ButtonStyle.red)
    async def deny(self, interaction, button):
        await interaction.response.send_message("❌ ปฏิเสธแล้ว", ephemeral=True)

    @discord.ui.button(label="👤 ดูข้อมูลผู้ซื้อ", style=discord.ButtonStyle.blurple)
    async def info(self, interaction, button):
        await interaction.response.send_message(f"ผู้ซื้อ: <@{self.user_id}>\nInvoice: `{self.invoice_id}`", ephemeral=True)


@bot.event
async def on_message(msg):
    await bot.process_commands(msg)

    if msg.author.bot:
        return

    if msg.channel.id != SLIP_CHANNEL_ID:
        return

    invoice = get_last_invoice(msg.author.id)
    if not invoice:
        await msg.channel.send("❌ ไม่พบคำสั่งซื้อค้างอยู่")
        return

    invoice_id = invoice["invoice_id"]
    expected_amount = float(invoice["price"])
    role_id = invoice["role_id"]
    plan = invoice["plan"]
    days = DURATIONS[plan]

    # Must have image
    if not msg.attachments:
        await msg.channel.send("❌ กรุณาส่งเป็นรูปสลิป")
        return

    bts = await msg.attachments[0].read()

    res = slip_check_api(bts)

    if not res:
        await msg.channel.send("⚠️ ไม่สามารถอ่านสลิปได้ กรุณาส่งใหม่")
        return

    # API response checking
    slip_amount = float(res.get("amount", 0))
    slip_company = res.get("company", "").strip()
    slip_ref = res.get("ref", "")

    admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)

    # Auto-filter
    if abs(slip_amount - expected_amount) < 0.01 and COMPANY_NAME in slip_company:

        view = AdminApproveView(msg.author.id, role_id, days, invoice_id)

        await admin_channel.send(
            f"🔍 ตรวจพบสลิปของ <@{msg.author.id}> (Invoice `{invoice_id}`):\n"
            f"ยอด: {slip_amount} บาท ✅\n"
            f"บริษัท: {slip_company} ✅\n"
            f"Ref: {slip_ref}\n\n"
            f"โปรดอนุมัติรายการนี้",
            view=view
        )

        await msg.reply("✅ สลิปถูกส่งให้แอดมินตรวจสอบแล้ว")
        return

    else:
        await admin_channel.send(
            f"❌ สลิปไม่ผ่านการตรวจของ <@{msg.author.id}>\n"
            f"ยอด: {slip_amount} | ต้องการ: {expected_amount}\n"
            f"บริษัทในสลิป: {slip_company}\n"
            f"Ref: {slip_ref}"
        )
        await msg.reply("❌ สลิปไม่ผ่านการตรวจ กรุณาติดต่อแอดมิน")
        return


# ---------------- EXPIRY SYSTEM ----------------
@tasks.loop(seconds=30)
async def check_expired():
    guild = bot.get_guild(GUILD_ID)
    rows = cur.execute("SELECT user_id, role_id, expires_at FROM subs").fetchall()
    now = int(time.time())

    for uid, rid, exp in rows:
        if now >= exp:
            member = guild.get_member(int(uid))
            role = guild.get_role(int(rid))
            if member and role:
                await member.remove_roles(role)
                try:
                    await member.send("⛔ ยศหมดอายุแล้ว")
                except:
                    pass
            cur.execute("DELETE FROM subs WHERE user_id=? AND role_id=?", (uid, rid))
            conn.commit()


@bot.event
async def on_ready():
    print("✅ Bot Online:", bot.user)
    check_expired.start()


bot.run(TOKEN)
