"""
Telegram Bot Responder — replies when anyone writes "ClaudeTender" in the group.
Runs on GitHub Actions every 5 minutes; tracks offset in telegram_offset.json.
"""

import json, os, re, requests
from datetime import datetime

TOKEN        = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OFFSET_FILE  = "telegram_offset.json"
VIEW_URL     = "https://www.eprocure.gov.bd/resources/common/ViewTender.jsp"

# Any of these in a message triggers the bot (case-insensitive)
TRIGGERS     = ["claudetender", "@tenderclaudebot", "claude tender"]

TODAY = datetime.now().strftime("%d-%b-%Y")


# ── Offset helpers ─────────────────────────────────────────────────────────────
def load_offset() -> int:
    if os.path.exists(OFFSET_FILE):
        with open(OFFSET_FILE) as f:
            return json.load(f).get("offset", 0)
    return 0


def save_offset(offset: int):
    with open(OFFSET_FILE, "w") as f:
        json.dump({"offset": offset}, f)


# ── Telegram helpers ───────────────────────────────────────────────────────────
def get_updates(offset: int) -> list:
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 0, "limit": 100},
            timeout=30,
        )
        return r.json().get("result", [])
    except Exception as e:
        print(f"  [getUpdates] Error: {e}")
        return []


def send_reply(chat_id, text: str, reply_to_id: int):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_to_message_id": reply_to_id,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if r.status_code != 200:
            print(f"  [sendMessage] Failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"  [sendMessage] Error: {e}")


# ── Response logic ─────────────────────────────────────────────────────────────
def build_reply(query: str, full_text: str = "") -> str:
    # Combine query + full message text for context matching
    combined = (query + " " + full_text).strip()
    q  = query.strip()
    ql = combined.lower()

    # Tender ID lookup (6–8 digit number anywhere in the message)
    id_match = re.search(r'\b(\d{6,8})\b', combined)

    # Eligibility / criteria query
    if any(w in ql for w in ["eligib", "criteria", "qualification", "requirement",
                              "eligible", "who can", "condition"]):
        link = f"{VIEW_URL}?id={id_match.group(1)}&h=t" if id_match else VIEW_URL
        tid_line = f"🆔 <b>Tender ID:</b> {id_match.group(1)}\n\n" if id_match else ""
        return (
            f"📋 <b>Eligibility Criteria</b>\n\n"
            f"{tid_line}"
            f"The eligibility and qualification requirements are stated in the <b>Tender Document (TD)</b>.\n\n"
            f"📥 <b>To get the full criteria:</b>\n"
            f"1. Open the notice link below\n"
            f"2. Click <b>\"Download Tender Document\"</b>\n"
            f"3. See Section: <i>Eligibility / Qualification Criteria</i>\n\n"
            f"🔗 <b>Notice Link:</b>\n{link}"
        )

    # Tender document / download query
    if any(w in ql for w in ["document", "download", "td ", "tender doc", "file"]):
        link = f"{VIEW_URL}?id={id_match.group(1)}&h=t" if id_match else VIEW_URL
        return (
            f"📄 <b>Tender Document</b>\n\n"
            f"Download the full Tender Document from the e-GP portal:\n\n"
            f"🔗 {link}\n\n"
            f"<i>Click the link → scroll to the Documents section → Download.</i>"
        )

    # Deadline / closing date query
    if any(w in ql for w in ["deadline", "closing", "last date", "submission", "when"]):
        link = f"{VIEW_URL}?id={id_match.group(1)}&h=t" if id_match else VIEW_URL
        return (
            f"📅 <b>Submission Deadline</b>\n\n"
            f"The exact closing date and time is shown on the tender notice.\n\n"
            f"🔗 <b>Notice Link:</b>\n{link}\n\n"
            f"<i>Closing date is listed near the top of the notice page.</i>"
        )

    # Tender ID standalone lookup
    if id_match and not q:
        tid  = id_match.group(1)
        link = f"{VIEW_URL}?id={tid}&h=t"
        return (
            f"🔍 <b>Tender ID: {tid}</b>\n\n"
            f"🔗 <b>Notice Link:</b>\n{link}\n\n"
            f"<i>Click the link to view full details on the e-GP portal.</i>"
        )

    if id_match:
        tid  = id_match.group(1)
        link = f"{VIEW_URL}?id={tid}&h=t"
        return (
            f"🔍 <b>Tender ID: {tid}</b>\n\n"
            f"🔗 <b>Notice Link:</b>\n{link}\n\n"
            f"For eligibility, closing date, and documents — open the link above.\n"
            f"Type <code>ClaudeTender eligibility {tid}</code> for criteria info."
        )

    # Help / commands
    if any(w in ql for w in ["help", "কি করে", "কিভাবে", "what can", "commands"]):
        return (
            "🤖 <b>ClaudeTender Bot — Commands</b>\n\n"
            "📌 <code>ClaudeTender [Tender ID]</code>\n"
            "   Direct link to any tender\n\n"
            "📌 <code>ClaudeTender eligibility [ID]</code>\n"
            "   How to find eligibility criteria\n\n"
            "📌 <code>ClaudeTender document [ID]</code>\n"
            "   How to download the tender document\n\n"
            "📌 <code>ClaudeTender status</code>\n"
            "   Check if bot is running\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🕗 New ICT tenders sent daily at <b>8:00 AM BST</b>."
        )

    # Status check
    if any(w in ql for w in ["status", "ok", "alive", "running", "working"]):
        return (
            f"✅ <b>ClaudeTender Bot is running!</b>\n\n"
            f"📅 Date: {TODAY}\n"
            f"🕗 Daily tender alerts: 8:00 AM BST\n"
            f"⏱ Response time: within 5 minutes"
        )

    # Default
    return (
        f"👋 Hello! I'm <b>ClaudeTender Bot</b>.\n\n"
        f"I monitor Bangladesh e-GP and send ICT tender notices daily.\n\n"
        f"💡 Try:\n"
        f"• <code>ClaudeTender help</code> — all commands\n"
        f"• <code>ClaudeTender 1286017</code> — look up a tender\n"
        f"• <code>ClaudeTender eligibility 1286017</code> — eligibility info"
    )


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    if not TOKEN:
        print("TELEGRAM_BOT_TOKEN not set — exiting.")
        return

    offset = load_offset()
    print(f"Polling updates from offset {offset} ...")
    updates = get_updates(offset)
    print(f"Got {len(updates)} update(s).")

    replied = 0
    for update in updates:
        new_offset = update["update_id"] + 1
        if new_offset > offset:
            offset = new_offset

        # Support both group messages and channel posts
        msg = update.get("message") or update.get("channel_post")
        if not msg:
            continue

        text = msg.get("text", "")
        if not text:
            continue

        tl = text.lower()
        if not any(trigger in tl for trigger in TRIGGERS):
            continue

        chat_id    = msg["chat"]["id"]
        message_id = msg["message_id"]
        sender     = msg.get("from", {}).get("first_name", "Unknown")

        # Extract query: everything after the matched trigger word
        query = text
        for trigger in TRIGGERS:
            idx = tl.find(trigger)
            if idx != -1:
                query = text[idx + len(trigger):].strip()
                break

        # Also pull in text from any quoted/replied-to message for context
        replied_text = ""
        if "reply_to_message" in msg:
            replied_text = msg["reply_to_message"].get("text", "")

        print(f"  ↳ Trigger from {sender!r}: {text[:80]!r}")

        reply = build_reply(query, full_text=text + " " + replied_text)
        send_reply(chat_id, reply, message_id)
        replied += 1
        print(f"  ✓ Replied to message {message_id}")

    save_offset(offset)
    print(f"\nDone — {replied} reply(s) sent. Offset saved: {offset}")


if __name__ == "__main__":
    main()
