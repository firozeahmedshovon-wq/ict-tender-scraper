"""
Telegram Bot Responder — replies when anyone writes "ClaudeTender" in the group.
Runs on GitHub Actions every 5 minutes; tracks offset in telegram_offset.json.
"""

import json, os, re, requests
from datetime import datetime

TOKEN        = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OFFSET_FILE  = "telegram_offset.json"
VIEW_URL     = "https://www.eprocure.gov.bd/resources/common/ViewTender.jsp"
KEYWORD      = "claudetender"

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
def build_reply(query: str) -> str:
    q = query.strip()
    ql = q.lower()

    # Tender ID lookup (6–8 digit number)
    id_match = re.search(r'\b(\d{6,8})\b', q)
    if id_match:
        tid = id_match.group(1)
        link = f"{VIEW_URL}?id={tid}&h=t"
        return (
            f"🔍 <b>Tender ID: {tid}</b>\n\n"
            f"🔗 <b>Notice Link:</b>\n{link}\n\n"
            f"<i>Click the link to view full details on the e-GP portal.</i>"
        )

    # Help / commands
    if any(w in ql for w in ["help", "কি করে", "কিভাবে", "what can", "commands", "?"]):
        return (
            "🤖 <b>ClaudeTender Bot — Available Commands</b>\n\n"
            "📌 <code>ClaudeTender [Tender ID]</code>\n"
            "   Get the direct link for any tender by its ID\n"
            "   Example: <code>ClaudeTender 1286017</code>\n\n"
            "📌 <code>ClaudeTender help</code>\n"
            "   Show this help message\n\n"
            "📌 <code>ClaudeTender status</code>\n"
            "   Check bot status\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🕗 New ICT tenders are sent automatically every day at <b>8:00 AM BST</b>."
        )

    # Status check
    if any(w in ql for w in ["status", "ok", "alive", "running", "working"]):
        return (
            f"✅ <b>ClaudeTender Bot is running!</b>\n\n"
            f"📅 Date: {TODAY}\n"
            f"🕗 Daily tender alerts: 8:00 AM BST\n"
            f"⏱ Response time: within 5 minutes"
        )

    # Default — greeting / unknown
    return (
        f"👋 Hello! I'm <b>ClaudeTender Bot</b>.\n\n"
        f"I monitor Bangladesh e-GP and send ICT tender notices to this group daily.\n\n"
        f"💡 Try:\n"
        f"• <code>ClaudeTender help</code> — see all commands\n"
        f"• <code>ClaudeTender 1286017</code> — look up a tender by ID"
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

        if KEYWORD not in text.lower():
            continue

        chat_id    = msg["chat"]["id"]
        message_id = msg["message_id"]
        sender     = msg.get("from", {}).get("first_name", "Unknown")

        # Extract everything after "ClaudeTender"
        idx   = text.lower().find(KEYWORD)
        query = text[idx + len(KEYWORD):].strip()

        print(f"  ↳ ClaudeTender from {sender!r}: {text[:80]!r}")

        reply = build_reply(query)
        send_reply(chat_id, reply, message_id)
        replied += 1
        print(f"  ✓ Replied to message {message_id}")

    save_offset(offset)
    print(f"\nDone — {replied} reply(s) sent. Offset saved: {offset}")


if __name__ == "__main__":
    main()
