# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Scrapes live ICT/software tenders from Bangladesh's e-Government Procurement portal (eprocure.gov.bd) and sends matching notices to a Telegram group. Two scripts run on GitHub Actions:

- **`tender_scraper.py`** — daily at 8:00 AM BST (2:00 AM UTC): logs in via Playwright, scrapes 20 keyword searches, filters results, saves a CSV, and sends new tenders to Telegram.
- **`telegram_responder.py`** — every 5 minutes: polls Telegram for messages containing `ClaudeTender` or `@tenderclaudebot` and replies with tender info.

## Running locally

```bash
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium

# Scraper (Telegram sends are skipped if token is unset)
python tender_scraper.py

# Bot responder (exits immediately if token is unset)
TELEGRAM_BOT_TOKEN=xxx python telegram_responder.py
```

Environment variables (all optional for dry runs):
- `EGP_USERNAME` / `EGP_PASSWORD` — e-GP login (defaults are hardcoded for dev)
- `TELEGRAM_BOT_TOKEN` — without this, scraper prints results but skips sending
- `TELEGRAM_CHAT_ID` — required for scraper to send messages

## Architecture

### tender_scraper.py flow

1. **Login** (`get_session_cookies`) — Playwright headless Chromium authenticates and captures session cookies.
2. **Scrape** — transfers cookies to a `requests.Session`, then iterates `SEARCH_TERMS`, POSTing to `TenderDetailsServlet` with pagination (50 results/page, up to 200 pages).
3. **Filter** (`title_matches`) — three-stage filter: civil-work prefix exclusion → exclusion term blocklist → keyword allowlist. `HARDWARE_EXCLUSION_PATTERNS` uses word-boundary regex for terms like `\bserver\b`.
4. **Dedup** — `seen_ids` set prevents duplicates across keyword searches.
5. **Output** — sorts by closing date, saves `ict_tenders_YYYY-MM-DD.csv`, sends via Telegram Bot API (HTML parse mode, 3-second delay between messages to avoid rate limits).
6. **Sent log** — `sent_tenders.json` persists a set of already-sent tender IDs; the GitHub Actions workflow commits this file back to the repo after each run.

### telegram_responder.py flow

Stateless poll-and-reply: loads offset from `telegram_offset.json`, calls `getUpdates`, processes any message containing a trigger word, calls `build_reply()`, sends a reply, saves the new offset back. The offset file is committed to the repo after each Actions run to prevent reprocessing.

### `build_reply()` dispatch logic

Keyword matching in priority order: eligibility → document/download → deadline → bare tender ID → help → status → default greeting. The organisation field in scraped data uses `|`-delimited hierarchy (Ministry | Department | PE); `extract_ministry/organization/pe` parse this.

## State files (committed to repo)

| File | Purpose |
|---|---|
| `sent_tenders.json` | Set of tender IDs already sent; prevents duplicate Telegram messages |
| `telegram_offset.json` | Last processed Telegram update ID; prevents reprocessing messages |
| `ict_tenders_YYYY-MM-DD.csv` | Daily output; one file per run date |

## GitHub Actions secrets required

`EGP_USERNAME`, `EGP_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — set in repo Settings → Secrets and variables → Actions.

## Tuning filters

- Add/remove search terms: `SEARCH_TERMS` (drives the POST keyword parameter)
- Add/remove title keywords: `TITLE_KEYWORDS` (allowlist substring matches)
- Exclude civil/unrelated tenders: `CIVIL_PREFIXES`, `EXCLUSION_TERMS`, `HARDWARE_EXCLUSION_PATTERNS`
- `ERP` in `TITLE_KEYWORDS` uses `\berp\b` regex (word boundary) to avoid false matches; other keywords use plain substring matching
