"""
Bangladesh e-Procurement — ICT Tender Scraper + Telegram Notifier
Finds Live ICT/Software tenders still open and sends each one to a Telegram group.
"""

import asyncio
import csv
import json
import os
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from playwright.async_api import async_playwright

# ── Telegram Config ────────────────────────────────────────────────────────────
# Step 1: Create a bot via @BotFather on Telegram → copy the token here
# Step 2: Add the bot to your group as admin
# Step 3: Send any message in the group, then open:
#         https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
#         Look for "chat": { "id": -100XXXXXXXXX }  ← that's your CHAT_ID
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# File to track which tenders have already been sent (prevents re-sending on repeated runs)
SENT_LOG_FILE = "sent_tenders.json"

# ── e-GP Config ───────────────────────────────────────────────────────────────
LOGIN_URL    = "https://www.eprocure.gov.bd/Index.jsp"
SEARCH_URL   = "https://www.eprocure.gov.bd/resources/common/StdTenderSearch.jsp"
SERVLET_URL  = "https://www.eprocure.gov.bd/TenderDetailsServlet"
VIEW_URL     = "https://www.eprocure.gov.bd/resources/common/ViewTender.jsp"
USERNAME     = os.environ.get("EGP_USERNAME", "firoze@polygontechlimited.com")
PASSWORD     = os.environ.get("EGP_PASSWORD", "#EGP@2345")
BASE_URL     = "https://www.eprocure.gov.bd"

# ── Search / Filter Config ────────────────────────────────────────────────────
SEARCH_TERMS = [
    "ICT", "software", "information technology",
    "laptop", "server", "database", "ERP",
    "cyber", "CCTV", "fiber optic", "data center",
    "network device", "networking equipment",
    "IT equipment", "IT system", "IT maintenance",
    "software development", "system development",
    "web application", "mobile application",
]

TITLE_KEYWORDS = [
    "software", "laptop", "server", "database", "erp", "cctv",
    "cybersecurity", "cyber security",
    "ict equipment", "ict apparatus", "ict system", "ict infrastructure",
    "ict related", "ict goods", "ict services", "ict solution",
    "ict maintenance", "ict device", "supply of ict", "procurement of ict",
    "bio-ict",
    "desktop computer", "laptop computer", "high performance computer",
    "computer equipment", "computer accessories", "computer and accessories",
    "supply of computer", "procurement of computer", "purchase of computer",
    "supply of computers", "procurement of computers",
    "network device", "network equipment", "networking device",
    "networking equipment", "campus network", "network expansion",
    "network infrastructure", "network maintenance", "network management",
    "fiber optic", "fibre optic", "optical network", "lan network",
    "it equipment", "it system", "it infrastructure", "it maintenance",
    "it support", "it service", "it solution",
    "information technology",
    "cloud computing", "cloud service", "data center", "data centre",
    "cloud storage", "cloud platform",
    "ip camera", "ip surveillance", "surveillance system", "cctv camera",
    "cctv system", "security camera",
    "management information system", "mis system",
    "erp system", "erp software", "clearing house system",
    "software development", "system development",
    "web application", "web development", "mobile application",
    "arcgis", "gis software", "gis system",
]

CIVIL_PREFIXES = [
    "construction of ", "remaining construction", "remaining work of ",
    "rehabilitation of ", "improvement of ", "repair of ", "repair and renovation",
    "upgradation ", "renovation of ", "procurement of retaining",
    "partial floor", "vertical extension",
]

EXCLUSION_TERMS = [
    "glassware", "chemicals for ", "hessian", "gunnies",
    "human-elephant", "elephant conflict",
    "soybean oil", "edible oil", "lentil", "firewood",
    "polao rice", "pesticide", "fertilizer",
    " ration items", " ration goods", "ration of ",
    "manpower service", "outsourcing service (front", "outsourcing manpower",
    "driver, mlss", "driver,mlss", "driver and mlss",
    " erplc",
]

# Hardware-only tenders to exclude (word-boundary matched against lowercase title)
HARDWARE_EXCLUSION_PATTERNS = [
    r"\bdesktop\b",
    r"\bcomputer\b",
    r"\bups\b",
    r"laser printer",
    r"cc camera",
    r"\bserver\b",
]

TODAY = datetime.now().date()
OUTPUT_FILE = f"ict_tenders_{TODAY}.csv"
RESULTS_PER_PAGE = 50


# ── Filter helpers ─────────────────────────────────────────────────────────────
def title_matches(title: str) -> bool:
    t = title.lower()
    if any(t.startswith(p) for p in CIVIL_PREFIXES):
        return False
    if any(ex in t for ex in EXCLUSION_TERMS):
        return False
    if any(re.search(pat, t) for pat in HARDWARE_EXCLUSION_PATTERNS):
        return False
    for kw in TITLE_KEYWORDS:
        if kw == "erp":
            if re.search(r'\berp\b', t):
                return True
        else:
            if kw in t:
                return True
    return False


def parse_closing_date(date_str: str):
    try:
        return datetime.strptime(date_str.strip(), "%d-%b-%Y %H:%M").date()
    except Exception:
        return None


# ── Sent-log helpers ───────────────────────────────────────────────────────────
def load_sent_log() -> set:
    if os.path.exists(SENT_LOG_FILE):
        with open(SENT_LOG_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_sent_log(sent: set):
    with open(SENT_LOG_FILE, "w") as f:
        json.dump(list(sent), f)


# ── Telegram helpers ───────────────────────────────────────────────────────────
def extract_ministry(org: str) -> str:
    parts = [p.strip() for p in org.split("|") if p.strip()]
    return parts[0] if parts else org


def extract_organization(org: str) -> str:
    parts = [p.strip() for p in org.split("|") if p.strip()]
    if len(parts) >= 3:
        return parts[-2]
    elif len(parts) == 2:
        return parts[0]
    return parts[0] if parts else org


def extract_pe(org: str) -> str:
    parts = [p.strip() for p in org.split("|") if p.strip()]
    return parts[-1] if parts else org


def tender_type_label(method: str) -> str:
    """Return a readable label for the tender type/method."""
    m = method.strip().rstrip(",").strip()
    return m if m else "N/A"


def notice_link(tender_id: str) -> str:
    """Direct link to the tender notice page."""
    return f"{VIEW_URL}?id={tender_id}&h=t"


def format_telegram_message(r: dict) -> str:
    title     = r["Title"].strip()
    ministry  = extract_ministry(r["Organisation"])
    org_name  = extract_organization(r["Organisation"])
    pe        = extract_pe(r["Organisation"])
    closing   = r["Closing Date"]
    days      = r["Days Left"]
    t_type    = tender_type_label(r["Type / Method"])
    link      = notice_link(r["Tender ID"])
    tender_id = r["Tender ID"]

    urgency = "🔴" if int(days) <= 3 else "🟡" if int(days) <= 7 else "🟢"

    return (
        f"{urgency} <b>ICT Tender Notice</b>\n\n"
        f"📌 <b>Title:</b> {title}\n\n"
        f"🏛 <b>Ministry:</b> {ministry}\n\n"
        f"🏢 <b>Organization:</b> {org_name}\n\n"
        f"👤 <b>Authority (PE):</b> {pe}\n\n"
        f"🆔 <b>Tender ID:</b> {tender_id}\n\n"
        f"📅 <b>Closing Date:</b> {closing}  ({days} day(s) left)\n\n"
        f"🏷 <b>Type:</b> {t_type}\n\n"
        f"🔗 <b>Notice Link:</b> {link}"
    )


def send_telegram(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        print("  [Telegram] Bot token not configured — skipping send.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in range(3):
        try:
            resp = requests.post(url, data=payload, timeout=15)
            if resp.status_code == 429:
                wait = resp.json().get("parameters", {}).get("retry_after", 30)
                print(f"  [Telegram] Rate limited — waiting {wait}s...")
                time.sleep(wait + 1)
                continue
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"  [Telegram] Send failed (attempt {attempt+1}): {e}")
            time.sleep(5)
    return False


# ── e-GP scraping ──────────────────────────────────────────────────────────────
async def get_session_cookies() -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(LOGIN_URL, timeout=60000)
        await page.wait_for_load_state("networkidle")
        await page.fill("#txtEmailId", USERNAME)
        await page.fill("#txtPassword", PASSWORD)
        await page.click("#btnLogin")
        await page.wait_for_load_state("networkidle", timeout=30000)
        print(f"  Logged in → {page.url}")
        cookies = await page.context.cookies()
        await browser.close()
    return {c["name"]: c["value"] for c in cookies}


def fetch_page(session: requests.Session, keyword: str, page_no: int) -> str:
    data = {
        "funName": "AllTenders",
        "keyword": keyword,
        "pageNo": str(page_no),
        "size": str(RESULTS_PER_PAGE),
        "homeWSearch": "homeWSearch",
        "approve": "true",
        "h": "t",
    }
    resp = session.post(SERVLET_URL, data=data, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_html(html: str, keyword: str, seen: set) -> list:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    if "noRecordFound" in html or "No records found" in html:
        return results

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 6:
            continue
        s_no = cells[0].get_text(strip=True)
        if not s_no.isdigit():
            continue

        tender_cell   = cells[1]
        status_label  = tender_cell.find("label")
        status        = status_label.get_text(strip=True) if status_label else "Unknown"
        tender_text   = tender_cell.get_text(" ", strip=True)
        tender_id     = tender_text.split()[0].rstrip(",") if tender_text else ""

        if status.lower() != "live":
            continue

        title_text = cells[2].get_text("\n", strip=True)
        lines = [l.strip() for l in title_text.splitlines() if l.strip()]
        title = lines[1] if len(lines) > 1 else lines[0] if lines else ""

        if not title_matches(title):
            continue

        org    = cells[3].get_text(" | ", strip=True)
        method = cells[4].get_text(", ", strip=True)

        dates_clean  = cells[5].get_text(",", strip=True)
        dates_parts  = [d.strip() for d in re.split(r"[,\n]", dates_clean) if d.strip()]
        publish_date = dates_parts[0] if dates_parts else ""
        closing_date = dates_parts[1] if len(dates_parts) > 1 else ""

        closing_dt = parse_closing_date(closing_date)
        if closing_dt is None or closing_dt < TODAY:
            continue

        if tender_id in seen:
            continue
        seen.add(tender_id)

        results.append({
            "Tender ID":       tender_id,
            "Status":          status,
            "Title":           title,
            "Organisation":    org,
            "Type / Method":   method,
            "Publishing Date": publish_date,
            "Closing Date":    closing_date,
            "Days Left":       (closing_dt - TODAY).days,
            "Search Term":     keyword,
            "Notice Link":     notice_link(tender_id),
        })
    return results


def scrape_keyword(session: requests.Session, keyword: str, seen: set) -> list:
    all_results = []
    page_no = 1
    MAX_PAGES = 200

    while page_no <= MAX_PAGES:
        html = fetch_page(session, keyword, page_no)
        if "noRecordFound" in html or "No records found" in html:
            break

        batch = parse_html(html, keyword, seen)
        all_results.extend(batch)

        soup  = BeautifulSoup(html, "html.parser")
        ti    = soup.find("input", {"id": "totalTender"})
        total = int(ti["value"]) if ti and ti.get("value", "0").isdigit() else 0

        if total and page_no * RESULTS_PER_PAGE >= total:
            break
        if total == 0 and page_no > 1 and not batch:
            break
        page_no += 1

    return all_results


# ── Main ───────────────────────────────────────────────────────────────────────
async def main():
    print("Step 1: Logging in...")
    cookies = await get_session_cookies()

    print("\nStep 2: Scraping ICT tenders...")
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": SEARCH_URL})

    all_results = []
    seen_ids: set = set()

    for term in SEARCH_TERMS:
        print(f"  '{term}' ...", end=" ", flush=True)
        try:
            batch = scrape_keyword(session, term, seen_ids)
            all_results.extend(batch)
            print(f"{len(batch)} new")
        except Exception as e:
            print(f"ERROR: {e}")

    all_results.sort(key=lambda r: parse_closing_date(r["Closing Date"]) or TODAY)

    print(f"\n{'='*55}")
    print(f"  Total Live ICT Tenders : {len(all_results)}")
    print(f"{'='*55}")

    if not all_results:
        print("  No matching tenders found.")
        return

    # Save CSV
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_results[0].keys()))
        writer.writeheader()
        writer.writerows(all_results)
    print(f"\n  CSV saved → {OUTPUT_FILE}")

    # ── Send new tenders to Telegram ──────────────────────────────────────────
    sent_log  = load_sent_log()
    new_count = 0
    skip_count = 0

    print(f"\nStep 3: Sending to Telegram group...")
    for r in all_results:
        if r["Tender ID"] in sent_log:
            skip_count += 1
            continue

        msg = format_telegram_message(r)
        ok  = send_telegram(msg)

        if ok:
            sent_log.add(r["Tender ID"])
            new_count += 1
            print(f"  ✓ Sent: {r['Title'][:65]}")
            time.sleep(3)  # stay under Telegram group limit (~20 msg/min)
        else:
            # Still mark as "sent" if token not configured — just print
            if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
                print(f"  [preview] {r['Title'][:65]}")

    save_sent_log(sent_log)

    print(f"\n  ✅ Done — {new_count} new tenders sent, {skip_count} already sent before.")
    print(f"  Sent log → {SENT_LOG_FILE}")

    # Print table summary
    print(f"\n{'─'*70}")
    print(f"  {'#':<4} {'DAYS':<5} {'CLOSING':<14} TITLE")
    print(f"{'─'*70}")
    for i, r in enumerate(all_results, 1):
        print(f"  {i:<4} {r['Days Left']:>3}d  {r['Closing Date'][:11]}   {r['Title'][:45]}")
    print(f"{'─'*70}")


if __name__ == "__main__":
    asyncio.run(main())
