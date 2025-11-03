# ---------- IMPORT ----------
import discord
from discord.ext import commands, tasks
import sqlite3, time, threading
from dotenv import load_dotenv
from flask import Flask
from waitress import serve
import os

load_dotenv()

# ---------- ENV ----------
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))

SCAN_CHANNEL_ID = 1433762345058041896      # ลูกค้าส่งสลิป
ADMIN_CHANNEL_ID = 1433789961403895999     # แอดมินอนุมัติ

# ---------- PAYMENT ----------
TRUEWALLET_PHONE = "0808432571"
QR_BANK_URL = "https://img2.pic.in.th/pic/b3353abf-04b1-4d82-a806-9859e0748f24-13025bdde0f821678.webp"

# ---------- PRICE & ROLE ----------
PRICES = {"1":20, "3":40, "7":80, "15":150, "30":300}
DURATIONS = {"1":1, "3":3, "7":7, "15":15, "30":30}
ROLE_IDS = {
    "1":1433747080660258867,
    "3":1433747173039804477,
    "7":1433747209475719332,
    "15":1433747247295889489,
    "30":1433747281932189826
}

# ---------- BOT ----------
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

    # ✅ DM ลูกค้าเมื่ออนุมัติสำเร็จ
    try:
        await member.send(f"✅ ยืนยันการชำระเงินเรียบร้อย!\nคุณได้รับยศ **{role.name}** เป็นเวลา **{days} วัน**")
    except:
        pass

# ---------- REMOVE EXPIRED ----------
@tasks.loop(seconds=60)
async def check_expired():
    guild = bot.get_guild(GUILD_ID)
    rows = cur.execute("SELECT user_id, role_id, expires_at FROM subs").fetchall()
    now = int(time.time())

    for user_id, role_id, exp in rows:
        if now >= exp:
            member = guild.get_member(int(user_id))
            role = guild.get_role(int(role_id))

            if member and role in member.roles:
                await member.remove_roles(role)
                try:
                    await member.send("⛔ ยศของคุณหมดอายุแล้ว")
                except:
                    pass

            cur.execute("DELETE FROM subs WHERE user_id=? AND role_id=?", (user_id, role_id))
            conn.commit()

# ---------- ADMIN PANEL ----------
class ApprovePanel(discord.ui.View):
    def __init__(self, user_id, plan):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.plan = plan

    # ✅ ปุ่มอนุมัติ
    @discord.ui.button(label="✅ อนุมัติ", style=discord.ButtonStyle.green)
    async def approve(self, interaction, button):

        await give_role(self.user_id, ROLE_IDS[self.plan], DURATIONS[self.plan])

        await interaction.response.send_message("✅ อนุมัติสำเร็จ ให้ยศลูกค้าแล้ว", ephemeral=True)

    # ✅ ปุ่มไม่อนุมัติ + เหตุผล
    @discord.ui.button(label="❌ ไม่อนุมัติ", style=discord.ButtonStyle.red)
    async def reject(self, interaction, button):

        class RejectModal(discord.ui.Modal, title="ระบุเหตุผลการปฏิเสธ"):
            reason = discord.ui.TextInput(label="เหตุผล", style=discord.TextStyle.paragraph)

            async def on_submit(self, modal_interaction):

                user = bot.get_user(int(self.user_id))

                # ✅ DM ลูกค้าพร้อมเหตุผล
                if user:
                    try:
                        await user.send(
                            f"❌ คำสั่งซื้อของคุณไม่ได้รับการอนุมัติ\n"
                            f"เหตุผลจากแอดมิน:\n**{self.reason.value}**"
                        )
                    except:
                        pass

                await modal_interaction.response.send_message("✅ ส่งเหตุผลให้ลูกค้าแล้ว", ephemeral=True)

        await interaction.response.send_modal(RejectModal())

    # ✅ ปุ่มดูข้อมูล
    @discord.ui.button(label="🔎 ดูข้อมูลผู้ซื้อ", style=discord.ButtonStyle.secondary)
    async def info(self, interaction, button):
        await interaction.response.send_message(
            f"🧾 ผู้ซื้อ: <@{self.user_id}>\nแพ็กที่เลือก: {self.plan} วัน",
            ephemeral=True
        )

# ---------- BUY CMD ----------
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
        title="🛒 เลือกแพ็กที่ต้องการ",
        description=f"✅ TrueMoney: **{TRUEWALLET_PHONE}**\n✅ หรือใช้ QR ธนาคารด้านล่าง",
        color=0x00ffcc
    )
    embed.set_image(url=QR_BANK_URL)

    await ctx.send(embed=embed, view=BuyButtons())

# ---------- HANDLE BUY BUTTON ----------
@bot.event
async def on_interaction(interaction):
    if not interaction.data:
        return

    cid = interaction.data.get("custom_id", "")
    if cid.startswith("buy_"):
        plan = cid.split("_")[1]

        embed = discord.Embed(
            title="📤 ส่งหลักฐานการชำระเงิน",
            description=(
                "✅ ส่งสลิปธนาคาร หรือ\n"
                "✅ ส่งซอง TrueMoney\n\n"
                f"ส่งเฉพาะในห้องที่กำหนดเท่านั้น\nเบอร์ TrueMoney: **{TRUEWALLET_PHONE}**"
            ),
            color=0x00ffcc
        )
        embed.set_image(url=QR_BANK_URL)

        await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------- HANDLE SLIP ----------
@bot.event
async def on_message(msg):
    await bot.process_commands(msg)

    if msg.author.bot:
        return

    if msg.channel.id != SCAN_CHANNEL_ID:
        return

    if not msg.attachments:
        return

    # ✅ ลบสลิปทันที
    file = await msg.attachments[0].to_file()
    try:
        await msg.delete()
    except:
        pass

    # ✅ ส่งให้แอดมินตรวจ
    admin_ch = bot.get_channel(ADMIN_CHANNEL_ID)

    await admin_ch.send(
        f"📥 หลักฐานใหม่จาก <@{msg.author.id}>",
        file=file,
        view=ApprovePanel(str(msg.author.id), "1")  # ✅ default 1 วัน
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
    print("✅ Bot Ready:", bot.user)
    check_expired.start()
    threading.Thread(target=run_flask, daemon=True).start()

# ---------- START ----------
bot.run(TOKEN)
