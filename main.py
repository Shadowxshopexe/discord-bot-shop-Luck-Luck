# ---------- IMPORT ----------
import discord
from discord.ext import commands, tasks
import sqlite3, time, threading
from dotenv import load_dotenv
from flask import Flask
from waitress import serve
import os

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
SCAN_CHANNEL_ID = 1433762345058041896        # ห้องลูกค้าส่งสลิป
ADMIN_CHANNEL_ID = 1433789961403895999       # ห้องแอดมินตรวจสอบ

# ---------- PRICE / ROLE ----------
PRICES = { "1":20, "3":40, "7":80, "15":150, "30":300 }
DURATIONS = { "1":1, "3":3, "7":7, "15":15, "30":30 }
ROLE_IDS = {
    "1":1433747080660258867,
    "3":1433747173039804477,
    "7":1433747209475719332,
    "15":1433747247295889489,
    "30":1433747281932189826
}

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- DATABASE ----------
conn = sqlite3.connect("subs.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS subs(
    user_id TEXT,
    role_id TEXT,
    expires_at INTEGER
)
""")
conn.commit()

# ---------- GIVE ROLE ----------
async def give_role(user_id, role_id, days):
    guild = bot.get_guild(GUILD_ID)
    member = guild.get_member(int(user_id))
    role = guild.get_role(int(role_id))
    if not member or not role:
        return

    await member.add_roles(role)
    expires = int(time.time() + days * 86400)
    cur.execute("INSERT INTO subs VALUES (?,?,?)", (user_id, role_id, expires))
    conn.commit()

    try:
        await member.send(f"✅ ระบบอนุมัติแล้ว คุณได้รับยศ {role.name} ({days} วัน)")
    except:
        pass

# ---------- REMOVE EXPIRED ----------
@tasks.loop(seconds=60)
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

# ---------- ADMIN PANEL ----------
class ApprovePanel(discord.ui.View):
    def __init__(self, user_id, plan):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.plan = plan

    @discord.ui.button(label="✅ อนุมัติ", style=discord.ButtonStyle.green)
    async def approve(self, interaction, button):
        await give_role(self.user_id, ROLE_IDS[self.plan], DURATIONS[self.plan])
        await interaction.response.send_message("✅ อนุมัติสำเร็จและให้ยศแล้ว", ephemeral=True)

    @discord.ui.button(label="❌ ปฏิเสธ", style=discord.ButtonStyle.red)
    async def reject(self, interaction, button):

        class RejectModal(discord.ui.Modal, title="เหตุผลการปฏิเสธ"):
            reason = discord.ui.TextInput(label="เหตุผล", style=discord.TextStyle.paragraph)

            async def on_submit(self, modal_interaction):
                user = bot.get_user(int(self.user_id))
                if user:
                    try:
                        await user.send(f"❌ คำสั่งซื้อถูกปฏิเสธ\nเหตุผล: {self.reason.value}")
                    except:
                        pass
                await modal_interaction.response.send_message("✅ ปฏิเสธแล้วและแจ้งลูกค้า", ephemeral=True)

        await interaction.response.send_modal(RejectModal())

    @discord.ui.button(label="🔎 ดูข้อมูลผู้ซื้อ", style=discord.ButtonStyle.secondary)
    async def info(self, interaction, button):
        await interaction.response.send_message(
            f"👤 User ID: {self.user_id}\nแพ็ก: {self.plan} วัน",
            ephemeral=True
        )

# ---------- BUY COMMAND ----------
@bot.command()
async def buy(ctx):

    class BuyButtons(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            for plan, price in PRICES.items():
                self.add_item(discord.ui.Button(
                    label=f"{plan} วัน • {price}฿",
                    custom_id=f"buy_{plan}",
                    style=discord.ButtonStyle.green
                ))

    embed = discord.Embed(
        title="🛒 เลือกแพ็ก",
        description="เลือกแพ็กที่ต้องการ",
        color=0x00ffcc
    )
    await ctx.send(embed=embed, view=BuyButtons())

# ---------- BUY BUTTON ----------
@bot.event
async def on_interaction(interaction):
    if not interaction.data:
        return

    cid = interaction.data.get("custom_id", "")
    if cid.startswith("buy_"):
        plan = cid.split("_")[1]

        embed = discord.Embed(
            title="📤 ส่งหลักฐานชำระเงิน",
            description="ส่งสลิปหรือซองได้ที่ห้องที่กำหนด\nระบบจะส่งไปให้แอดมินตรวจต่อ",
            color=0x00ffcc
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------- MAIN SLIP HANDLER ----------
@bot.event
async def on_message(msg):
    await bot.process_commands(msg)

    if msg.author.bot:
        return

    # ✅ อนุญาตให้ส่งเฉพาะ "ห้องลูกค้าส่งสลิป"
    if msg.channel.id != SCAN_CHANNEL_ID:
        return

    # ✅ ไม่ใช่สลิป = ไม่สนใจ
    if not msg.attachments:
        return

    # ✅ ลบสลิปลูกค้าทันที (กันโดนขโมยหลักฐาน)
    try:
        await msg.delete()
    except:
        pass

    # ✅ ส่งให้ห้องแอดมินตรวจสอบ
    admin_ch = bot.get_channel(ADMIN_CHANNEL_ID)

    # plan default = 1 วัน (ถ้าไม่เจอคำไหน)
    plan = "1"
    for p in PRICES.keys():
        if p in msg.content:
            plan = p

    await admin_ch.send(
        f"📥 หลักฐานใหม่จาก <@{msg.author.id}>\nแพ็กที่เลือก: {plan} วัน",
        files=[await msg.attachments[0].to_file()],
        view=ApprovePanel(str(msg.author.id), plan)
    )

# ---------- KEEP ALIVE ----------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot Running"

def run_flask():
    serve(app, host="0.0.0.0", port=3000)

# ---------- READY ----------
@bot.event
async def on_ready():
    print("✅ Bot Online:", bot.user)
    check_expired.start()
    threading.Thread(target=run_flask, daemon=True).start()

# ---------- RUN BOT ----------
bot.run(TOKEN)
