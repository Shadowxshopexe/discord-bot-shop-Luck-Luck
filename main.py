import discord
from discord.ext import commands, tasks
import sqlite3
import time
import io
import requests
from PIL import Image
import imagehash
import os
from dotenv import load_dotenv
from flask import Flask
from waitress import serve
import threading

load_dotenv()

# ---------------- CONFIG ----------------
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
SCAN_CHANNEL_ID = int(os.getenv("SCAN_CHANNEL_ID"))
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID"))
TRUEWALLET_PHONE = os.getenv("TRUEWALLET_PHONE")

# ✅ QR ธนาคาร
QR_IMAGE_URL = "https://img2.pic.in.th/pic/b3353abf-04b1-4d82-a806-9859e0748f24.webp"

# ✅ ราคา → ยศ
ROLE_IDS = {
    20: 1433747080660258867,
    40: 1433747173039804477,
    80: 1433747209475719332,
    150: 1433747247295889489,
    300: 1433747281932189826
}

# ✅ ราคา → จำนวนวัน
DURATIONS = {
    20: 1,
    40: 3,
    80: 7,
    150: 15,
    300: 30
}

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
conn.commit()

# ---------------- QR MATCHING ----------------
REF_QR_HASH = None

def load_qr():
    global REF_QR_HASH
    img = Image.open(io.BytesIO(requests.get(QR_IMAGE_URL).content)).convert("L")
    REF_QR_HASH = imagehash.phash(img)

load_qr()

def hash_img(bts):
    try:
        return imagehash.phash(Image.open(io.BytesIO(bts)).convert("L"))
    except:
        return None

# ---------------- GIVE ROLE ----------------
async def give_role(user, amount):
    guild = bot.get_guild(GUILD_ID)
    member = guild.get_member(user.id)
    role = guild.get_role(ROLE_IDS[amount])

    if not member or not role:
        return

    await member.add_roles(role)
    expires = int(time.time() + DURATIONS[amount] * 86400)

    cur.execute("INSERT INTO subs VALUES (?,?,?)",
                (str(user.id), str(role.id), expires))
    conn.commit()

    try:
        await user.send(f"✅ ได้รับยศ {role.name} ({DURATIONS[amount]} วัน)")
    except:
        pass

# ---------------- REMOVE ROLE ----------------
@tasks.loop(seconds=60)
async def check_expire():
    guild = bot.get_guild(GUILD_ID)
    rows = cur.execute("SELECT user_id, role_id, expires_at FROM subs").fetchall()
    now = int(time.time())

    for uid, rid, exp in rows:
        if now >= exp:
            member = guild.get_member(int(uid))
            role = guild.get_role(int(rid))
            if member and role:
                await member.remove_roles(role)

            cur.execute("DELETE FROM subs WHERE user_id=? AND role_id=?", (uid, rid))
            conn.commit()

# ---------------- FLASK KEEP ALIVE ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot Running OK"

def run_flask():
    serve(app, host="0.0.0.0", port=3000)

# ---------------- BOT READY ----------------
@bot.event
async def on_ready():
    print("✅ Bot Online:", bot.user)
    check_expire.start()
    threading.Thread(target=run_flask, daemon=True).start()

# ---------------- BUY COMMAND ----------------
@bot.command()
async def buy(ctx):
    class Buy(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            for amt in ROLE_IDS.keys():
                self.add_item(
                    discord.ui.Button(
                        label=f"{amt} บาท • {DURATIONS[amt]} วัน",
                        custom_id=f"buy_{amt}",
                        style=discord.ButtonStyle.green
                    )
                )

    embed = discord.Embed(
        title="🛒 ซื้อแพ็ก",
        description="เลือกแพ็กที่ต้องการจากปุ่มด้านล่าง",
        color=0x00ffcc
    )
    embed.set_image(url=QR_IMAGE_URL)
    embed.set_footer(text=f"TrueMoney: {TRUEWALLET_PHONE}")

    await ctx.send(embed=embed, view=Buy())

# ---------------- BUY BUTTON ----------------
@bot.event
async def on_interaction(interaction):
    if not interaction.data:
        return
    cid = interaction.data.get("custom_id", "")
    if cid.startswith("buy_"):
        amt = int(cid.split("_")[1])

        embed = discord.Embed(
            title="🧾 คำสั่งซื้อ",
            description=(
                f"**ยอดชำระ:** {amt} บาท\n"
                f"**ยศ:** {DURATIONS[amt]} วัน\n\n"
                "✅ ส่งซอง TrueMoney ที่นี่\n"
                "✅ หรือแนบรูปสลิปธนาคาร (จะตรวจอัตโนมัติ)"
            ),
            color=0x00ffcc
        )
        embed.set_image(url=QR_IMAGE_URL)

        await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------- DETECT AMOUNT ----------------
def _detect_amount_from_text(text):
    for amt in ROLE_IDS.keys():
        if str(amt) in text:
            return amt
    return None

# ---------------- MAIN SCAN HANDLER ----------------
@bot.event
async def on_message(msg):
    await bot.process_commands(msg)

    if msg.author.bot:
        return
    if msg.channel.id != SCAN_CHANNEL_ID:
        return

    # ✅ TrueMoney ซอง auto
    if "gift.truemoney.com" in msg.content:
        amt = _detect_amount_from_text(msg.content)
        if amt:
            await give_role(msg.author, amt)
            await msg.delete()
            return

    # ✅ สลิปธนาคาร auto-check QR
    if msg.attachments:
        bts = await msg.attachments[0].read()
        user_hash = hash_img(bts)

        # ✅ สลิปถูกต้อง
        if user_hash and (user_hash - REF_QR_HASH) <= 6:
            amt = _detect_amount_from_text(msg.content)
            if amt:
                await give_role(msg.author, amt)
                await msg.delete()
                return

        # ❌ สลิปผิด — ลบทันที + DM เตือน
        try:
            await msg.author.send("❌ สลิปไม่ถูกต้อง กรุณาส่งใหม่อีกครั้ง")
        except:
            pass

        try:
            await msg.delete()
        except:
            pass

        return

# ---------------- RUN BOT ----------------
bot.run(TOKEN)
