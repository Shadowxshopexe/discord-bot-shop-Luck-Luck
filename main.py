import os
import time
import sqlite3
import threading
import discord
from discord.ext import commands, tasks
from discord import ui
from dotenv import load_dotenv
from keep_alive import run_keep_alive

load_dotenv()

# ---------------- CONFIG ----------------
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
SCAN_CHANNEL_ID = int(os.getenv("SCAN_CHANNEL_ID"))      # ห้องที่ลูกค้าส่งซอง/สลิป
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID"))    # ห้องแอดมินตรวจสอบ
TRUEWALLET_PHONE = os.getenv("TRUEWALLET_PHONE")

QR_IMAGE = "https://img2.pic.in.th/pic/b3353abf-04b1-4d82-a806-9859e0748f24-13025bdde0f821678.webp"

PRICES = {
    "1": 20,
    "3": 40,
    "7": 80,
    "15": 150,
    "30": 300
}

ROLE_IDS = {
    "1": "1433747080660258867",
    "3": "1433747173039804477",
    "7": "1433747209475719332",
    "15": "1433747247295889489",
    "30": "1433747281932189826"
}

DAYS = {"1":1, "3":3, "7":7, "15":15, "30":30}


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

def make_invoice_id():
    return f"INV{int(time.time())}"


async def give_role(user_id: str, role_id: str, days: int):
    guild = bot.get_guild(GUILD_ID)
    member = guild.get_member(int(user_id))
    role = guild.get_role(int(role_id))

    if member and role:
        await member.add_roles(role)

        expires = int(time.time() + days * 86400)
        cur.execute("INSERT INTO subs VALUES (?,?,?)", (user_id, role_id, expires))
        conn.commit()

        try:
            await member.send(f"✅ ยศของคุณถูกอนุมัติแล้ว ({days} วัน)")
        except:
            pass


async def send_to_admin(invoice_id, user_id, plan, content=None, image=None):
    guild = bot.get_guild(GUILD_ID)
    admin_ch = guild.get_channel(ADMIN_CHANNEL_ID)

    view = AdminView(invoice_id, user_id, plan)

    embed = discord.Embed(
        title="🔔 ตรวจสอบคำสั่งซื้อ",
        description=(
            f"ผู้ใช้: <@{user_id}>\n"
            f"แพ็ก: {plan} วัน ({PRICES[plan]}฿)\n"
            f"Invoice: `{invoice_id}`"
        ),
        color=0xffcc00
    )

    if content:
        embed.add_field(name="ลิงก์ซองที่ส่ง", value=content, inline=False)

    if image:
        embed.set_image(url=image)

    await admin_ch.send(embed=embed, view=view)


# ---------------- MODAL ----------------

class ReasonModal(ui.Modal, title="เหตุผลในการไม่อนุมัติ"):
    reason = ui.TextInput(label="กรุณากรอกเหตุผล", required=True)

    def __init__(self, invoice_id, user_id):
        super().__init__()
        self.invoice_id = invoice_id
        self.user_id = user_id

    async def on_submit(self, interaction):
        text = self.reason.value

        cur.execute("UPDATE invoices SET status='rejected' WHERE invoice_id=?", (self.invoice_id,))
        conn.commit()

        try:
            user = await bot.fetch_user(int(self.user_id))
            await user.send(
                f"⛔ คำสั่งซื้อ `{self.invoice_id}` ถูกปฏิเสธ\n"
                f"เหตุผล: {text}"
            )
        except:
            pass

        await interaction.response.send_message("✅ ส่งเหตุผลให้ลูกค้าเรียบร้อยแล้ว", ephemeral=True)


# ---------------- ADMIN VIEW ----------------

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

        await give_role(self.user_id, ROLE_IDS[self.plan], DAYS[self.plan])
        await interaction.response.send_message("✅ อนุมัติแล้ว และมอบยศให้ลูกค้า", ephemeral=True)

    @ui.button(label="❌ ไม่อนุมัติ", style=discord.ButtonStyle.red)
    async def reject(self, interaction, button):
        await interaction.response.send_modal(ReasonModal(self.invoice_id, self.user_id))


# ---------------- BUY ----------------

@bot.command()
async def buy(ctx):
    class BuyButtons(ui.View):
        def __init__(self):
            super().__init__()
            for p, price in PRICES.items():
                self.add_item(
                    ui.Button(
                        label=f"{p} วัน • {price}฿",
                        custom_id=f"buy_{p}",
                        style=discord.ButtonStyle.green
                    )
                )

    embed = discord.Embed(
        title="🛒 ระบบซื้อแพ็ก",
        description="เลือกแพ็กด้านล่าง",
        color=0x00ffcc
    )
    embed.set_image(url=QR_IMAGE)
    embed.add_field(name="TrueMoney", value=TRUEWALLET_PHONE)

    await ctx.send(embed=embed, view=BuyButtons())


# ---------------- BUTTON ----------------

@bot.event
async def on_interaction(inter):
    if not inter.data:
        return

    cid = inter.data.get("custom_id")
    if cid and cid.startswith("buy_"):

        plan = cid.replace("buy_", "")
        invoice_id = make_invoice_id()

        cur.execute("INSERT INTO invoices VALUES (?,?,?,?,?,?,?)",
                    (invoice_id, str(inter.user.id), plan,
                     PRICES[plan], ROLE_IDS[plan], "pending", int(time.time())))
        conn.commit()

        embed = discord.Embed(
            title="🧾 ใบสั่งซื้อ",
            description=(
                f"แพ็ก: {plan} วัน\n"
                f"ราคา: {PRICES[plan]} บาท\n"
                f"Invoice: `{invoice_id}`\n\n"
                "✅ กรุณาส่ง **ซอง TrueMoney** หรือ **สลิปธนาคาร**\n"
                f"ในห้อง <#{SCAN_CHANNEL_ID}> เท่านั้น"
            ),
            color=0x00ffcc
        )
        embed.set_image(url=QR_IMAGE)

        await inter.response.send_message(embed=embed, ephemeral=True)


# ---------------- MESSAGE HANDLER ----------------

@bot.event
async def on_message(msg):

    await bot.process_commands(msg)

    if msg.author.bot:
        return

    if msg.channel.id != SCAN_CHANNEL_ID:
        return

    row = cur.execute(
        "SELECT invoice_id, plan FROM invoices WHERE discord_id=? ORDER BY created_at DESC",
        (str(msg.author.id),)
    ).fetchone()

    if not row:
        return await msg.delete()

    invoice_id, plan = row
    plan = str(plan)

    # ---------- กรณีส่งลิงก์ซอง ----------
    if "gift.truemoney.com" in (msg.content or ""):
        await send_to_admin(invoice_id, msg.author.id, plan, content=msg.content)
        await msg.author.send("✅ ส่งลิงก์ให้แอดมินตรวจสอบแล้ว")
        return await msg.delete()

    # ---------- กรณีส่งรูปสลิป ----------
    if msg.attachments:
        att = msg.attachments[0]
        await send_to_admin(invoice_id, msg.author.id, plan, image=att.url)
        await msg.author.send("✅ ส่งสลิปให้แอดมินตรวจสอบแล้ว")
        return await msg.delete()

    # ข้อความอื่นลบทันที
    await msg.delete()


# ---------------- AUTO REMOVE ROLE ----------------

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

            cur.execute("DELETE FROM subs WHERE user_id=? AND role_id=?", (uid, rid))
            conn.commit()


# ---------------- READY ----------------

@bot.event
async def on_ready():
    print("✅ Bot Online:", bot.user)
    check_expired.start()
    threading.Thread(target=run_keep_alive, daemon=True).start()


bot.run(TOKEN)
