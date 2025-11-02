# main.py
# Discord Payment Bot — Auto OCR/QR bank slip scanner (Auto mode)
# Auto approve when invoice matches (room-limited)
# Environment variables required:
#   DISCORD_TOKEN, GUILD_ID, TRUEWALLET_PHONE, ADMIN_CHANNEL_ID, QR_IMAGE_URL
# NOTE: This file expects OpenCV, imagehash, Pillow installed. pytesseract optional (for OCR).
# If you want OCR, install tesseract-ocr on the host.

import os
import io
import re
import time
import sqlite3
import threading
import requests
import traceback
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

import discord
from discord.ext import tasks, commands

# Imaging
from PIL import Image
import numpy as np
import cv2

# optional libraries
try:
    import imagehash
except Exception:
    imagehash = None

try:
    import pytesseract
except Exception:
    pytesseract = None

# ---------- CONFIG ----------
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID") or 0)
TRUEWALLET_PHONE = os.getenv("TRUEWALLET_PHONE", "0808432571")
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID") or 1433789961403895999)
QR_IMAGE_URL = os.getenv("QR_IMAGE_URL", "")  # optional canonical QR image

# room to listen to (ONLY this channel will be used to submit slips)
SLIP_CHANNEL_ID = 1433762345058041896  # <-- fixed channel as requested

PRICES = {"1":20, "3":40, "7":80, "15":150, "30":300}
ROLE_IDS = {
    "1":"1433747080660258867",
    "3":"1433747173039804477",
    "7":"1433747209475719332",
    "15":"1433747247295889489",
    "30":"1433747281932189826"
}
DURATIONS = {"1":1, "3":3, "7":7, "15":15, "30":30}

# ---------- Discord setup ----------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- Database ----------
DB_PATH = "database.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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

# ---------- Utility: download image -> numpy array ----------
def download_image_bytes(url_or_bytes):
    if isinstance(url_or_bytes, (bytes, bytearray)):
        return bytes(url_or_bytes)
    # url
    r = requests.get(url_or_bytes, timeout=15)
    r.raise_for_status()
    return r.content

def pil_to_cv2(img_pil):
    arr = np.array(img_pil)
    # RGB to BGR
    if arr.ndim == 3:
        arr = arr[:, :, ::-1].copy()
    return arr

def read_image_from_bytes(bts):
    img = Image.open(io.BytesIO(bts)).convert("RGB")
    return pil_to_cv2(img)

# ---------- QR decode using OpenCV ----------
qr_detector = cv2.QRCodeDetector()

def decode_qr_text_from_bytes(bts):
    try:
        img = read_image_from_bytes(bts)
        data, points, _ = qr_detector.detectAndDecode(img)
        if data:
            return data.strip()
    except Exception:
        pass
    return None

# ---------- OCR fallback (pytesseract) ----------
def ocr_text_from_bytes(bts):
    if pytesseract is None:
        return ""
    try:
        img = Image.open(io.BytesIO(bts)).convert("L")
        text = pytesseract.image_to_string(img, lang='tha+eng')  # attempt Thai + English if available
        return text
    except Exception:
        return ""

# ---------- Amount extraction ----------
def extract_amount_from_text(text):
    if not text:
        return None
    # look for patterns like 20.00, 20, 20 บาท
    # find all numbers with optional decimal
    # search for 'บาท' nearby preferred
    text = text.replace(',', '').replace('฿', ' ')
    m = re.search(r'([0-9]+(?:\.[0-9]{1,2})?)\s*(?:บาท|THB)?', text, flags=re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except:
            return None
    # fallback: find any integer number
    m2 = re.search(r'\b([0-9]{1,7})\b', text)
    if m2:
        try:
            return float(m2.group(1))
        except:
            return None
    return None

# ---------- Company matching ----------
COMPANY_KEYWORDS = ["บริษัท วันดีดี คอร์ปอเรชั่น จำกัด", "วันดีดี คอร์ปอเรชั่น", "WandDD", "วันดีดี"]

def company_found_in_text(text):
    if not text:
        return False
    t = text.lower()
    for kw in COMPANY_KEYWORDS:
        if kw.lower() in t:
            return True
    return False

# ---------- REF QR hash (optional) ----------
REF_QR_HASH = None
if QR_IMAGE_URL and imagehash is not None:
    try:
        b = download_image_bytes(QR_IMAGE_URL)
        ref_img = Image.open(io.BytesIO(b)).convert("L")
        REF_QR_HASH = imagehash.phash(ref_img)
        print("Loaded reference QR hash")
    except Exception as e:
        print("Could not load reference QR:", e)

def is_similar_hash(h1, h2, max_dist=6):
    try:
        return (h1 - h2) <= max_dist
    except Exception:
        return False

# ---------- Role management ----------
async def give_role(user_id, role_id, days):
    guild = bot.get_guild(GUILD_ID)
    member = guild.get_member(int(user_id))
    if not member:
        return False
    role = guild.get_role(int(role_id))
    if not role:
        return False
    try:
        await member.add_roles(role)
    except Exception:
        pass
    expires = int(time.time() + int(days) * 86400)
    cur.execute("INSERT INTO subs VALUES (?,?,?)", (str(user_id), str(role_id), expires))
    conn.commit()
    try:
        await member.send(f"✅ คุณได้รับยศ {role.name} ({days} วัน)")
    except:
        pass
    return True

# ---------- Expiry checker ----------
@tasks.loop(seconds=60)
async def check_expired():
    guild = bot.get_guild(GUILD_ID)
    rows = cur.execute("SELECT user_id, role_id, expires_at FROM subs").fetchall()
    now = int(time.time())
    for uid, rid, exp in rows:
        if now >= exp:
            member = guild.get_member(int(uid))
            role = guild.get_role(int(rid))
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role)
                except:
                    pass
                try:
                    await member.send("⛔ ยศของคุณหมดอายุแล้ว")
                except:
                    pass
            cur.execute("DELETE FROM subs WHERE user_id=? AND role_id=?", (uid, rid))
            conn.commit()

# ---------- Helper: find pending invoice for user ----------
def find_latest_pending_invoice_for_user(user_id):
    row = cur.execute(
        "SELECT invoice_id, role_id, plan, price, status FROM invoices WHERE discord_id=? ORDER BY created_at DESC",
        (str(user_id),)
    ).fetchone()
    if not row:
        return None
    invoice_id, role_id, plan, price, status = row
    return {"invoice_id": invoice_id, "role_id": role_id, "plan": plan, "price": price, "status": status}

# ---------- Buy command ----------
class BuyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for plan, price in PRICES.items():
            days = DURATIONS[plan]
            label = f"{days} วัน • {price}฿"
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.green, custom_id=f"buy_{plan}")
            self.add_item(button)

@bot.command()
async def buy(ctx):
    embed = discord.Embed(title="🛒 เลือกแพ็กเกจ", description="กดเลือกแพ็กที่ต้องการด้านล่าง", color=0x00ffcc)
    embed.set_footer(text="ระบบออโต้ — ส่งสลิปหรือ QR ในช่องเฉพาะเพื่อรับยศโดยอัตโนมัติ")
    await ctx.send(embed=embed, view=BuyView())

# ---------- Interaction handler (button clicks) ----------
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if not interaction.data:
        return
    cid = interaction.data.get("custom_id")
    if not cid or not cid.startswith("buy_"):
        return
    plan = cid.split("_", 1)[1]
    if plan not in PRICES:
        await interaction.response.send_message("แพ็กไม่ถูกต้อง", ephemeral=True)
        return
    price = PRICES[plan]
    days = DURATIONS[plan]
    role_id = ROLE_IDS.get(plan)
    invoice_id = f"INV{int(time.time())}"
    cur.execute("INSERT OR REPLACE INTO invoices VALUES (?,?,?,?,?,?,?)",
                (invoice_id, str(interaction.user.id), role_id, plan, price, "pending", int(time.time())))
    conn.commit()

    tmn_link = f"https://pay.example.com/truewallet?to={TRUEWALLET_PHONE}&amount={price}&ref={invoice_id}"

    embed = discord.Embed(
        title="🧾 ใบคำสั่งซื้อ",
        description=(
            f"**แพ็ก:** {days} วัน\n"
            f"**ราคา:** {price} บาท\n"
            f"**Wallet (TrueMoney):** {TRUEWALLET_PHONE}\n"
            f"**เลขอ้างอิง (Invoice):** `{invoice_id}`\n\n"
            f"✅ สามารถชำระผ่าน QR ธนาคาร (ด้านล่าง) หรือ TrueMoney (ลิงก์ด้านล่าง)\n\n"
            f"📌 กรุณาส่งสลิป/รูปภาพการชำระในช่องข้อความของบอทเท่านั้น (channel id: {SLIP_CHANNEL_ID})"
        ),
        color=0x00ffcc
    )
    if QR_IMAGE_URL:
        embed.set_image(url=QR_IMAGE_URL)
    embed.add_field(name="ชำระด้วย TrueMoney (ลิงก์)", value=f"[คลิกเพื่อชำระ]({tmn_link})", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------- Core: handle images in the designated slip channel ----------
@bot.event
async def on_message(message: discord.Message):
    # process commands too
    await bot.process_commands(message)

    if message.author.bot:
        return

    # Only accept slips in the configured slip channel
    if message.channel.id != SLIP_CHANNEL_ID:
        return

    # find pending invoice for user
    invoice = find_latest_pending_invoice_for_user(message.author.id)
    if not invoice or invoice.get("status") != "pending":
        try:
            await message.author.send("❌ ไม่พบคำสั่งซื้อค้างอยู่ (invoice) กรุณาใช้คำสั่ง `!buy` ก่อนชำระ")
        except:
            pass
        return

    invoice_id = invoice["invoice_id"]
    expected_price = float(invoice["price"])
    role_id = invoice["role_id"]
    plan = invoice["plan"]

    # If attachments
    if message.attachments:
        att = message.attachments[0]
        try:
            bts = await att.read()
        except Exception:
            await message.author.send("❌ ไม่สามารถดาวน์โหลดรูป ลองอีกครั้ง")
            return

        # 1) Try QR decode
        qr_text = decode_qr_text_from_bytes(bts)
        ocr_text = ""
        amount = None
        company_ok = False
        invoice_found = False

        if qr_text:
            # check if invoice id is in QR or phone or amount
            txt = qr_text.lower()
            if invoice_id.lower() in txt:
                invoice_found = True
            if TRUEWALLET_PHONE in txt:
                company_ok = True
            # some QR encode amount like 'amount=20' or '20.00'
            amt = extract_amount_from_text(qr_text)
            if amt:
                amount = amt

        # 2) If no decisive QR match, try OCR
        if not invoice_found or not company_ok or not amount:
            if pytesseract is not None:
                try:
                    ocr_text = ocr_text_from_bytes(bts)
                except Exception:
                    ocr_text = ""
                # find invoice id
                if invoice_id.lower() in ocr_text.lower():
                    invoice_found = True
                # find company name
                if company_found_in_text(ocr_text):
                    company_ok = True
                # find amount
                if amount is None:
                    amt = extract_amount_from_text(ocr_text)
                    if amt:
                        amount = amt

        # 3) Try to accept if amount matches and company ok OR invoice id found
        accepted = False
        reason = []
        if invoice_found:
            accepted = True
            reason.append("พบเลข Invoice ในภาพ")
        else:
            # amount check tolerant: allow small rounding
            if amount is not None:
                # compare to expected price
                if abs(amount - expected_price) < 0.5:  # tolerant 0.5 baht
                    if company_ok or company_found_in_text(ocr_text):
                        accepted = True
                        reason.append(f"ยอดตรง ({amount} == {expected_price}) และชื่อบริษัทตรง")
                    else:
                        # maybe QR is TrueMoney numeric ref that contains phone
                        if TRUEWALLET_PHONE in (qr_text or "") or TRUEWALLET_PHONE in ocr_text:
                            accepted = True
                            reason.append("ยอดตรงและพบเบอร์ TrueMoney ของร้าน")
                        else:
                            reason.append("ยอดตรงแต่ชื่อบริษัท/เบอร์ไม่ชัดเจน")
                else:
                    reason.append(f"ยอดไม่ตรง: พบ {amount} แต่ต้อง {expected_price}")
            else:
                reason.append("ไม่พบยอดในภาพ")

        # 4) If REF_QR_HASH available, try imagehash compare
        if not accepted and REF_QR_HASH is not None and imagehash is not None:
            try:
                img = Image.open(io.BytesIO(bts)).convert("L")
                h = imagehash.phash(img)
                if is_similar_hash(h, REF_QR_HASH, max_dist=8):
                    accepted = True
                    reason.append("QR ภาพใกล้เคียงกับ QR ต้นฉบับ")
            except Exception:
                pass

        # 5) Finalize
        if accepted:
            # update invoices -> paid
            try:
                cur.execute("UPDATE invoices SET status='paid' WHERE invoice_id=?", (invoice_id,))
                conn.commit()
            except:
                pass

            # delete the message (remove customer image from chat)
            try:
                await message.delete()
            except:
                pass

            # give role
            days = DURATIONS.get(plan, 1)
            ok = await give_role(message.author.id, role_id, days)
            # notify admin channel
            admin_ch = bot.get_channel(ADMIN_CHANNEL_ID)
            msg_admin = f"✅ AUTO APPROVED: <@{message.author.id}> invoice `{invoice_id}` ชำระแล้ว ({expected_price} บาท). สาเหตุ: {'; '.join(reason)}"
            try:
                if admin_ch:
                    await admin_ch.send(msg_admin)
            except:
                pass

            try:
                await message.author.send(f"✅ ชำระเรียบร้อยแล้ว (invoice {invoice_id}) — ระบบอนุมัติอัตโนมัติ: {'; '.join(reason)}")
            except:
                pass
            return
        else:
            # Not accepted
            admin_ch = bot.get_channel(ADMIN_CHANNEL_ID)
            report = f"🔔 สลิปถูกส่งโดย <@{message.author.id}> (invoice {invoice_id}) แต่ไม่ผ่านการตรวจอัตโนมัติ.\nเหตุผล: {'; '.join(reason)}\n--- OCR excerpt ---\n{(ocr_text[:800] + '...') if ocr_text else 'ไม่มี OCR ข้อความ'}"
            try:
                if admin_ch:
                    # send image + report to admin for manual review
                    view = AdminApproveView(message.author.id, role_id, DURATIONS.get(plan,1), invoice_id)
                    await admin_ch.send(report, view=view)
                    await message.author.send("⚠️ สลิปถูกส่งแล้ว แต่ไม่ผ่านการตรวจอัตโนมัติ แอดมินจะตรวจสอบภายในเร็วๆ นี้")
                else:
                    await message.author.send("⚠️ สลิปไม่ผ่านการตรวจอัตโนมัติ และไม่มีช่องทางแจ้งแอดมิน")
            except:
                pass
            return

    # If no attachments but user typed link or code, we can try to parse the message text for links/qr text
    if message.content:
        # try to decode if they pasted a URL containing invoice id or truemoney link
        txt = message.content.strip()
        if "gift.truemoney.com" in txt.lower() or "truemoney" in txt.lower():
            # forward to admin for review + remove from channel
            try:
                await message.delete()
            except:
                pass
            admin_ch = bot.get_channel(ADMIN_CHANNEL_ID)
            if admin_ch:
                view = AdminApproveView(message.author.id, None, DURATIONS.get(invoice.get("plan", "1"),1), invoice_id)
                await admin_ch.send(f"🔔 TRUE MONEY link from <@{message.author.id}> invoice {invoice_id}:\n{txt}", view=view)
                try:
                    await message.author.send("✅ ลิงก์ได้รับแล้ว กำลังรอแอดมินตรวจสอบ (หาก auto-approve ถูกตั้งไว้จะอนุมัติอัตโนมัติ)")
                except:
                    pass
            return

# ---------- Admin approve view (in case fallback to admin) ----------
class AdminApproveView(discord.ui.View):
    def __init__(self, user_id, role_id, days, invoice_id):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.role_id = role_id
        self.days = days
        self.invoice_id = invoice_id

    @discord.ui.button(label="✅ อนุมัติ (Auto)", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # mark paid and give role
        cur.execute("UPDATE invoices SET status='paid' WHERE invoice_id=?", (self.invoice_id,))
        conn.commit()
        await give_role(self.user_id, self.role_id, self.days)
        await interaction.response.send_message("✅ อนุมัติเรียบร้อย", ephemeral=True)

    @discord.ui.button(label="❌ ปฏิเสธ", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ask admin for reason (modal not available in some libs) -> fallback to simple message
        await interaction.response.send_message("โปรดพิมพ์เหตุผลการปฏิเสธในช่องแชทนี้", ephemeral=True)

# ---------- On ready ----------
@bot.event
async def on_ready():
    print("✅ Bot Online:", bot.user)
    check_expired.start()

# ---------- Start ----------
if __name__ == "__main__":
    bot.run(TOKEN)
