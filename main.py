@bot.event
async def on_message(msg):
    await bot.process_commands(msg)

    if msg.author.bot:
        return

    # ✅ ตรวจเฉพาะห้องนี้
    if msg.channel.id != SCAN_CHANNEL_ID:
        return

    has_slip_image = False
    has_tmw_link = False
    file_to_send = None

    # ✅ ตรวจรูปสลิป / ซอง
    if msg.attachments:
        attachment = msg.attachments[0]
        file_to_send = await attachment.to_file()
        has_slip_image = True

    # ✅ ตรวจลิงก์ TrueMoney
    if "gift.truemoney.com" in msg.content.lower():
        has_tmw_link = True

    # ✅ ถ้าไม่มีทั้งสลิปและลิงก์ → ไม่ต้องทำอะไร
    if not has_slip_image and not has_tmw_link:
        return

    # ✅ ลบข้อความของลูกค้าทันที
    try:
        await msg.delete()
    except:
        pass

    # ✅ ส่งให้แอดมินตรวจสอบ
    admin_ch = bot.get_channel(ADMIN_CHANNEL_ID)

    embed = discord.Embed(
        title="📥 หลักฐานใหม่จากลูกค้า",
        description=f"จาก: <@{msg.author.id}>",
        color=0xffcc00
    )

    if has_tmw_link:
        embed.add_field(name="🔗 ลิงก์ TrueMoney", value=msg.content, inline=False)

    # ✅ ส่งภาพถ้ามี
    if file_to_send:
        await admin_ch.send(
            embed=embed,
            file=file_to_send,
            view=ApprovePanel(str(msg.author.id), "1")
        )
    else:
        await admin_ch.send(
            embed=embed,
            view=ApprovePanel(str(msg.author.id), "1")
        )
