import os
import time
import sqlite3
import threading
import discord
from discord import ui
from discord.ext import commands, tasks
from dotenv import load_dotenv
from keep_alive import run_keep_alive

load_dotenv()

# ---------------- CONFIG ----------------
TOKEN = os.getenv("DISCORD_TOKEN")

GUILD_ID = int(os.getenv("GUILD_ID"))
SCAN_CHANNEL_ID = int(os.getenv("SCAN_CHANNEL_ID"))       # ห้องลูกค้าส่งลิงก์/รูป
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID"))     # ห้องแอดมินตรวจสอบ
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


# ---------------- HELPERS ----------------

def make_invoice_id():
    return f"INV{int(time.time())}"


async def give_role(user_id: str, role_id: str, days: int):

    guild = bot.get_guild(GUILD_ID)
    member = guild.get_member(int(user_id))
    role = guild.get_role(int(role_id))

    if not member or not role:
        return False

    await member.add_roles(role)

    expires = int(time.time() + days * 86400)
    cur.execute("INSERT INTO subs VALUES (?,?,?)", (user_id, role_id, expires))
    conn.commit()

    try:
        await member.send(f"✅ ยศของคุณได้รับการอนุมัติแล้ว ({days} วัน)")
    except:
        pass

    return True



# ---------------- ADMIN MODAL ----------------

class ReasonModal(ui.Modal, title="เหตุผลที่ไม่อนุมัติ"):
    reason = ui.TextInput(label="เหตุผล", required=True)

    def __init__(self, invoice_id, user_id, admin_msg):
        super().__init__()
        self.invoice_id = invoice_id
        self.user_id = user_id
        self.admin_msg = admin_msg

    async def on_submit(self, interaction):
        text = self.reason.value

        # อัปเดตเป็น rejected
        cur.execute("UPDATE invoices SET status='rejected' WHERE invoice_id=?", (self.invoice_id,))
        conn.commit()

        # DM ผู้ใช้
        try:
            user = await bot.fetch_user(int(self.user_id))
            await user.send(f"⛔ คำสั่งซื้อ `{self.invoice_id}` ถูกปฏิเสธ\nเหตุผล: {text}")
        except:
            pass

        # ลบข้อความแอดมิน
        try:
            await self.admin_msg.delete()
        except:
            pass

        await interaction.response.send_message("✅ ส่งเหตุผลแล้ว", ephemeral=True)


# ---------------- ADMIN VIEW ----------------

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

        await give_role(self.user_id, ROLE_IDS[self.plan], DAYS[self.plan])

        # ลบข้อความในห้องแอดมิน
        try:
            await self.admin_msg.delete()
        except:
            pass

        await interaction.response.send_message("✅ อนุมัติแล้ว", ephemeral=True)

    @ui.button(label="❌ ไม่อนุมัติ", style=discord.ButtonStyle.red)
    async def reject(self, interaction, button):
        await interaction.response.send_modal(
            ReasonModal(self.invoice_id, self.user_id, self.admin_msg)
        )


# ---------------- SEND TO ADMIN ----------------

async def send_to_admin(invoice_id, user_id, plan, content=None, image=None):
    guild = bot.get_guild(GUILD_ID)
    admin_ch = guild.get_channel(ADMIN_CHANNEL_ID)

    embed = discord.Embed(
        title="🔔 รายการรอตรวจสอบ",
        description=(
            f"ผู้ใช้: <@{user_id}>\n"
            f"แพ็ก: {plan} วัน ({PRICES[plan]}฿)\n"
            f"Invoice: `{invoice_id}`"
        ),
        color=0xffcc00
    )

    if content:
        embed.add_field(name="ข้อความที่ส่ง", value=content, inline=False)

    if image:
        embed.set_image(url=image)

    admin_msg = await admin_ch.send(embed=embed)
    await admin_msg.edit(view=AdminView(invoice_id, user_id, plan, admin_msg))


# ---------------- BUY COMMAND ----------------

@bot.command()
async def buy(ctx):

    class BuyButtons(ui.View):
        def __init__(self):
            super().__init__()
            for p in PRICES:
                self.add_item(ui.Button(
                    label=f"{p} วัน • {PRICES[p]}฿",
                    custom_id=f"buy_{p}",
                    style=discord.ButtonStyle.green
                ))

    embed = discord.Embed(
        title="🛒 ซื้อแพ็ก",
        description="เลือกแพ็กที่ต้องการ",
        color=0x00ffcc
    )
    embed.set_image(url=QR_IMAGE)
    embed.add_field(name="TrueMoney", value=TRUEWALLET_PHONE)

    await ctx.send(embed=embed, view=BuyButtons())


# ---------------- BUTTON HANDLER ----------------

@bot.event
async def on_interaction(inter):
    if not inter.data:
        return

    cid = inter.data.get("custom_id")

    if cid and cid.startswith("buy_"):

        plan = cid.replace("buy_", "")
        invoice_id = make_invoice_id()

        cur.execute("INSERT INTO invoices VALUES (?,?,?,?,?,?,?)",
                    (invoice_id, str(inter.user.id), plan, PRICES[plan],
                     ROLE_IDS[plan], "pending", int(time.time())))
        conn.commit()

        embed = discord.Embed(
            title="🧾 ใบคำสั่งซื้อ",
            description=(
                f"แพ็ก: {plan} วัน\n"
                f"ราคา: {PRICES[plan]} บาท\n"
                f"Invoice: `{invoice_id}`\n\n"
                f"✅ ส่งสลิป/ซอง ในห้อง <#{SCAN_CHANNEL_ID}> เท่านั้น"
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

    # ✅ ตรวจเฉพาะห้องลูกค้า
    if msg.channel.id != SCAN_CHANNEL_ID:
        return

    # หาคำสั่งซื้อ
    row = cur.execute(
        "SELECT invoice_id, plan FROM invoices WHERE discord_id=? ORDER BY created_at DESC",
        (str(msg.author.id),)
    ).fetchone()

    if not row:
        return await msg.delete()

    invoice_id, plan = row
    plan = str(plan)

    # ส่งข้อความไปแอดมิน
    if msg.attachments:
        await send_to_admin(invoice_id, msg.author.id, plan, image=msg.attachments[0].url)
        await msg.delete()
        return

    if msg.content:
        await send_to_admin(invoice_id, msg.author.id, plan, content=msg.content)
        await msg.delete()
        return


# ---------------- AUTO EXPIRE ----------------

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
