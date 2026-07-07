"""
Bangladesh e-Procurement — ICT Tender Scraper + Telegram Notifier
Scrapes Live ICT tenders every hour, stores them in SQLite, and sends
only newly discovered tenders to Telegram (no duplicates).
"""

import asyncio
import csv
import logging
import os
import re
import sqlite3
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from playwright.async_api import async_playwright

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DB_FILE   = os.path.join(BASE_DIR, "tenders.db")
LOG_DIR   = os.path.join(BASE_DIR, "logs")
TODAY     = datetime.now().date()
OUTPUT_FILE = os.path.join(BASE_DIR, f"ict_tenders_{TODAY}.csv")

# ── Telegram Config ────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── e-GP Config ───────────────────────────────────────────────────────────────
LOGIN_URL    = "https://www.eprocure.gov.bd/Index.jsp"
SEARCH_URL   = "https://www.eprocure.gov.bd/resources/common/StdTenderSearch.jsp"
SERVLET_URL  = "https://www.eprocure.gov.bd/TenderDetailsServlet"
VIEW_URL     = "https://www.eprocure.gov.bd/resources/common/ViewTender.jsp"
USERNAME     = os.environ.get("EGP_USERNAME", "firoze@polygontechlimited.com")
PASSWORD     = os.environ.get("EGP_PASSWORD", "#EGP@2345")

# ── Search / Filter Config ────────────────────────────────────────────────────
SEARCH_TERMS = [
    "ICT", "software", "information technology",
    "database", "ERP",
    "cyber", "data center",
    "network device", "networking equipment",
    "IT equipment", "IT system", "IT maintenance",
    "software development", "system development",
    "web application", "mobile application",
]

TITLE_KEYWORDS = [
    "software", "database", "erp",
    "cybersecurity", "cyber security",
    "ict equipment", "ict apparatus", "ict system", "ict infrastructure",
    "ict related", "ict goods", "ict services", "ict solution",
    "ict maintenance", "ict device", "supply of ict", "procurement of ict",
    "bio-ict",
    "network device", "network equipment", "networking device",
    "networking equipment", "campus network", "network expansion",
    "network infrastructure", "network maintenance", "network management",
    "lan network", "optical network terminal",
    "it equipment", "it system", "it infrastructure", "it maintenance",
    "it support", "it service", "it solution",
    "information technology",
    "cloud computing", "cloud service", "data center", "data centre",
    "cloud storage", "cloud platform",
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

    # ── CCTV / Surveillance ─────────────────────────────────────────────────────
    "cctv", "cc tv", "cctv camera", "cctv system",
    "ip camera", "ip surveillance", "surveillance camera", "security camera",

    # ── Medical equipment ───────────────────────────────────────────────────────
    "videoscope", "laryngoscope", "pleuroscope", "endoscope", "colonoscope",
    "bronchoscope", "gastroscope", "cystoscope", "otoscope",
    "ultrasound", "x-ray", "mri", "ct scan",

    # ── Hardware supply (non-software) ──────────────────────────────────────────
    "laptops and printers", "laptop and printer",
    "intercom", "pabx", "walkie talkie",
    "cutting plotter", "3d foot scanner",

    # ── License / renewal (not software development) ────────────────────────────
    "oracle",          # Oracle license procurement — not bidding territory
    "renewal of ",     # License/subscription renewals
    "renewing of ",

    # ── IT Support / staffing (not development) ─────────────────────────────────
    "it support",      # IT support service contracts (manpower-based)
    "it support service",

    # ── Network hardware / infrastructure ───────────────────────────────────────
    "lan, wan",        # LAN/WAN infrastructure contracts
    "lan/wan",
    "wan, ip",
    "manpower for ships", "manpower for vessel",
]

# Hardware-only tenders to exclude (word-boundary matched against lowercase title)
HARDWARE_EXCLUSION_PATTERNS = [
    r"\bdesktop\b",
    r"\bcomputer\b",
    r"\bups\b",
    r"laser printer",
    r"cc camera",
    r"\bserver\b",
    r"\bprinter\b",
    r"\blaptop\b",
]

RESULTS_PER_PAGE = 50


# ── Logging setup ──────────────────────────────────────────────────────────────
def setup_logging() -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, f"scraper_{TODAY}.log")
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger(__name__)


log = setup_logging()


# ── SQLite database ────────────────────────────────────────────────────────────
def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tenders (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            tender_id          TEXT    UNIQUE NOT NULL,
            tender_title       TEXT,
            organisation       TEXT,
            tender_type        TEXT,
            publishing_date    TEXT,
            closing_date       TEXT,
            days_left          INTEGER,
            tender_notice_link TEXT,
            telegram_sent      INTEGER DEFAULT 0,
            telegram_sent_at   TEXT,
            first_seen_at      TEXT    DEFAULT (datetime('now', 'localtime')),
            last_updated_at    TEXT    DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.commit()
    return conn


def upsert_tender(conn: sqlite3.Connection, r: dict):
    """Insert new tender or update mutable fields if already exists.
    Never resets telegram_sent to 0 on update — preserves sent state.
    """
    conn.execute("""
        INSERT INTO tenders
            (tender_id, tender_title, organisation, tender_type,
             publishing_date, closing_date, days_left, tender_notice_link)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tender_id) DO UPDATE SET
            tender_title       = excluded.tender_title,
            closing_date       = excluded.closing_date,
            days_left          = excluded.days_left,
            last_updated_at    = datetime('now', 'localtime')
    """, (
        r["Tender ID"],
        r["Title"],
        r["Organisation"],
        r["Type / Method"],
        r["Publishing Date"],
        r["Closing Date"],
        r["Days Left"],
        r["Notice Link"],
    ))
    conn.commit()


def get_unsent_tenders(conn: sqlite3.Connection) -> list:
    cur = conn.execute("""
        SELECT * FROM tenders
        WHERE telegram_sent = 0
        ORDER BY closing_date ASC
    """)
    return [dict(row) for row in cur.fetchall()]


def mark_sent(conn: sqlite3.Connection, tender_id: str):
    conn.execute("""
        UPDATE tenders
        SET telegram_sent = 1,
            telegram_sent_at = datetime('now', 'localtime')
        WHERE tender_id = ?
    """, (tender_id,))
    conn.commit()


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


def notice_link(tender_id: str) -> str:
    return f"{VIEW_URL}?id={tender_id}&h=t"


def format_telegram_message(r: dict) -> str:
    # Accepts both scraper dict keys and DB row keys
    tender_id = r.get("Tender ID") or r.get("tender_id", "")
    title     = (r.get("Title") or r.get("tender_title", "")).strip()
    org       = r.get("Organisation") or r.get("organisation", "")
    closing   = r.get("Closing Date") or r.get("closing_date", "")
    days      = r.get("Days Left") or r.get("days_left", 0)
    t_type    = (r.get("Type / Method") or r.get("tender_type", "")).strip().rstrip(",").strip() or "N/A"
    link      = r.get("Notice Link") or r.get("tender_notice_link") or notice_link(tender_id)

    ministry = extract_ministry(org)
    org_name = extract_organization(org)
    pe       = extract_pe(org)
    urgency  = "🔴" if int(days) <= 3 else "🟡" if int(days) <= 7 else "🟢"

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
        log.warning("[Telegram] Bot token not configured — skipping send.")
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
                log.warning(f"[Telegram] Rate limited — waiting {wait}s...")
                time.sleep(wait + 1)
                continue
            resp.raise_for_status()
            return True
        except Exception as e:
            log.warning(f"[Telegram] Send failed (attempt {attempt + 1}/3): {e}")
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
        log.info(f"Logged in → {page.url}")
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

        tender_cell  = cells[1]
        status_label = tender_cell.find("label")
        status       = status_label.get_text(strip=True) if status_label else "Unknown"
        tender_text  = tender_cell.get_text(" ", strip=True)
        tender_id    = tender_text.split()[0].rstrip(",") if tender_text else ""

        if status.lower() != "live":
            continue

        title_text = cells[2].get_text("\n", strip=True)
        lines = [l.strip() for l in title_text.splitlines() if l.strip()]
        title = lines[1] if len(lines) > 1 else lines[0] if lines else ""

        if not title_matches(title):
            continue

        org    = cells[3].get_text(" | ", strip=True)
        method = cells[4].get_text(", ", strip=True)

        dates_clean = cells[5].get_text(",", strip=True)
        dates_parts = [d.strip() for d in re.split(r"[,\n]", dates_clean) if d.strip()]
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
    run_start = datetime.now()
    log.info("=" * 60)
    log.info(f"Scraper run started at {run_start.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    # ── Step 1: Init DB ───────────────────────────────────────────────────────
    conn = init_db()
    log.info(f"Database: {DB_FILE}")

    # ── Step 2: Login ─────────────────────────────────────────────────────────
    log.info("Logging in to e-GP portal...")
    try:
        cookies = await get_session_cookies()
    except Exception as e:
        log.error(f"Login failed: {e}")
        conn.close()
        return

    # ── Step 3: Scrape ────────────────────────────────────────────────────────
    log.info("Scraping ICT tenders...")
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": SEARCH_URL})

    all_results = []
    seen_ids: set = set()

    for term in SEARCH_TERMS:
        try:
            batch = scrape_keyword(session, term, seen_ids)
            all_results.extend(batch)
            log.info(f"  '{term}': {len(batch)} new tenders")
        except Exception as e:
            log.error(f"  '{term}': scrape failed — {e}")

    all_results.sort(key=lambda r: parse_closing_date(r["Closing Date"]) or TODAY)
    log.info(f"Total live ICT tenders found: {len(all_results)}")

    if not all_results:
        log.info("No matching tenders found. Exiting.")
        conn.close()
        return

    # ── Step 4: Upsert into DB ────────────────────────────────────────────────
    new_in_db = 0
    for r in all_results:
        before = conn.execute(
            "SELECT telegram_sent FROM tenders WHERE tender_id = ?", (r["Tender ID"],)
        ).fetchone()
        upsert_tender(conn, r)
        if before is None:
            new_in_db += 1
    log.info(f"DB upsert complete — {new_in_db} new tenders added to database")

    # ── Step 5: Save CSV ──────────────────────────────────────────────────────
    try:
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(all_results[0].keys()))
            writer.writeheader()
            writer.writerows(all_results)
        log.info(f"CSV saved → {OUTPUT_FILE}")
    except Exception as e:
        log.warning(f"CSV save failed: {e}")

    # ── Step 6: Send unsent tenders to Telegram ───────────────────────────────
    unsent = get_unsent_tenders(conn)
    log.info(f"Tenders pending Telegram send: {len(unsent)}")

    sent_count  = 0
    fail_count  = 0
    skip_count  = len(all_results) - len(unsent)

    for r in unsent:
        try:
            msg = format_telegram_message(r)
            ok  = send_telegram(msg)

            if ok:
                mark_sent(conn, r["tender_id"])
                sent_count += 1
                log.info(f"  ✓ Sent [{r['tender_id']}] {r['tender_title'][:60]}")
                time.sleep(3)   # stay under Telegram ~20 msg/min limit
            else:
                # telegram_sent stays 0 — will retry next run
                fail_count += 1
                log.warning(f"  ✗ Failed [{r['tender_id']}] {r['tender_title'][:60]} — will retry")

        except Exception as e:
            fail_count += 1
            log.error(f"  ✗ Error processing [{r.get('tender_id')}]: {e}")

    # ── Step 7: Summary ───────────────────────────────────────────────────────
    duration = (datetime.now() - run_start).seconds
    log.info("-" * 60)
    log.info(f"Run complete in {duration}s")
    log.info(f"  Tenders scraped : {len(all_results)}")
    log.info(f"  New in DB       : {new_in_db}")
    log.info(f"  Telegram sent   : {sent_count}")
    log.info(f"  Send failed     : {fail_count} (will retry next run)")
    log.info(f"  Already sent    : {skip_count}")
    log.info("=" * 60)

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
