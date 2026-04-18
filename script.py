"""
events_agent.py
───────────────
Weekly events digest for Cambridge/Boston, MA.
Scans specific websites in batches, asks Claude to extract events,
then sends a clean formatted email.

SETUP
─────
1. Install deps:
   pip install anthropic requests python-dotenv

2. Create a .env file next to this script:
   ANTHROPIC_API_KEY=sk-ant-...
   SENDGRID_API_KEY=SG....       # OR use RESEND_API_KEY
   # RESEND_API_KEY=re_...

3. Run manually:
   python events_agent.py

4. Schedule (cron — 8am daily):
   0 8 * * * cd /path/to/script && python3 events_agent.py >> events.log 2>&1
"""

import os
import time
import datetime
import requests
from dotenv import load_dotenv
import anthropic

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────
# Rate-limit tuning: web_search can return 15-25k input tokens per call.
# With a 30k TPM cap, only 1 site per call is safe.  Adjust BATCH_DELAY_SECONDS
# (env var, default 90) if you still hit limits:
#   BATCH_SIZE=1, DELAY=90  → safe for 30k TPM (default)
#   BATCH_SIZE=1, DELAY=120 → extra headroom if still hitting limits

CITY = "Cambridge, MA"
RADIUS_MILES = 5
CUSTOM_SITES = [
    "eventbrite.com",
    "bandsintown.com",
    "timeout.com/boston",
    "https://ra.co/events/us/boston",
    "https://www.thestoopmedia.com/",
    "https://www.meetup.com/find/us--ma--cambridge/",
    "https://luma.com/boston",
    "https://massaicoalition.com/events",
]
EMAIL_TO = "maxchapin430@gmail.com"
EMAIL_FROM = "events-digest@yourdomain.com"  # ← change to your verified sender
FORMAT = "week"   # week | digest | top5
BATCH_SIZE = 1    # one site per Claude call — keeps input tokens well under the 30k TPM limit
BATCH_DELAY = int(os.environ.get("BATCH_DELAY_SECONDS", 90))  # seconds between API calls to stay under TPM
EMAIL_PROVIDER = "sendgrid"  # "sendgrid" or "resend"

# ── DATE HELPERS ──────────────────────────────────────────────────────────────

def get_date_window():
    today = datetime.date.today()
    if FORMAT == "week":
        end = today + datetime.timedelta(days=7)
        label = f"the week of {today.strftime('%B %d')}–{end.strftime('%B %d, %Y')}"
    elif FORMAT == "top5":
        end = today + datetime.timedelta(days=3)
        label = f"the next few days ({today.strftime('%B %d')}–{end.strftime('%B %d')})"
    else:
        end = today + datetime.timedelta(days=2)
        label = f"today and tomorrow ({today.strftime('%B %d')}–{end.strftime('%B %d')})"
    return today, end, label

# ── PROMPTS ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an event researcher for the Boston/Cambridge area.
Your job is to scan websites and extract real upcoming events.
Only include events with confirmed dates. Do not invent events.
Include ALL types of events: music, concerts, comedy, art, theater, film, food,
sports, fitness, community, networking, tech, education, family, cultural,
nightlife, festivals, markets, fairs, workshops, lectures, and anything else.
Do NOT filter by category — return every event you find.
Return each event on its own line using this exact plain format:

DATE | TIME | EVENT NAME | VENUE | NEIGHBORHOOD | DESCRIPTION (1 sentence) | URL | PRICE

If any field is unknown, write "?" for it.
Return nothing else — no headers, no intro, no markdown."""


def build_prompt(today, end, label, sites):
    sites_formatted = "\n".join(f"  - {s}" for s in sites)
    return f"""Scan these websites and extract ALL events happening in or near {CITY} for {label}:

{sites_formatted}

Also search broadly for events in Boston/Cambridge during this period.

Return EVERY event you find — do not limit to any particular category, type, or theme.
Include concerts, sports, food, art, theater, comedy, community events, workshops, lectures, festivals, and all other types.
Today is {today.strftime('%A, %B %d, %Y')}.
Only include events from today through {end.strftime('%B %d, %Y')}.
Use the pipe-delimited format specified in your instructions."""

# ── CLAUDE AGENT ──────────────────────────────────────────────────────────────

MAX_RETRIES = 5
INITIAL_BACKOFF = 30  # seconds; doubles each retry


def fetch_batch(client, today, end, label, sites):
    prompt = build_prompt(today, end, label, sites)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=8000,
                system=SYSTEM_PROMPT,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content": prompt}],
            )
            text = ""
            for block in response.content:
                if block.type == "text":
                    text += block.text
            return text.strip()
        except anthropic.RateLimitError as exc:
            retry_after = getattr(exc.response, "headers", {}).get("retry-after")
            wait = int(retry_after) if retry_after else INITIAL_BACKOFF * (2 ** (attempt - 1))
            if attempt == MAX_RETRIES:
                raise
            print(f"  ⚠️  Rate-limited (attempt {attempt}/{MAX_RETRIES}), retrying in {wait}s…")
            time.sleep(wait)

    return ""


def fetch_all_events(today, end, label):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    batches = [CUSTOM_SITES[i:i+BATCH_SIZE] for i in range(0, len(CUSTOM_SITES), BATCH_SIZE)]
    raw_lines = []

    for i, batch in enumerate(batches):
        if i > 0:
            print(f"  ⏳ Waiting {BATCH_DELAY}s to stay under TPM limit…")
            time.sleep(BATCH_DELAY)
        print(f"  🔍 Batch {i+1}/{len(batches)}: {', '.join(batch)}")
        result = fetch_batch(client, today, end, label, batch)
        if result:
            raw_lines.extend([l for l in result.splitlines() if "|" in l])

    return raw_lines

# ── EVENT PARSING ─────────────────────────────────────────────────────────────

def parse_events(raw_lines):
    events = []
    seen = set()
    for line in raw_lines:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 7:
            continue
        date, time, name, venue, neighborhood, description, url, *rest = parts + ["?"] * 8
        price = rest[0] if rest else "?"

        # Deduplicate by name + date
        key = (name.lower(), date.lower())
        if key in seen:
            continue
        seen.add(key)

        events.append({
            "date": date,
            "time": time,
            "name": name,
            "venue": venue,
            "neighborhood": neighborhood,
            "description": description,
            "url": url,
            "price": price,
        })

    # Sort by date string (best effort)
    events.sort(key=lambda e: e["date"])
    return events

# ── EMAIL BUILDING ────────────────────────────────────────────────────────────

def event_card_html(e):
    meta_parts = []
    if e["time"] and e["time"] != "?":
        meta_parts.append(e["time"])
    location = " · ".join(p for p in [e["venue"], e["neighborhood"]] if p and p != "?")
    if location:
        meta_parts.append(f"📍 {location}")
    meta = " · ".join(meta_parts)

    price_html = ""
    if e["price"] and e["price"].lower() == "free":
        price_html = '<span style="color:#2a7a2a;font-size:12px;font-weight:600;background:#eaf6ea;padding:2px 7px;border-radius:4px;margin-left:6px;">Free</span>'
    elif e["price"] and e["price"] != "?":
        price_html = f'<span style="color:#666;font-size:13px;"> · {e["price"]}</span>'

    link_html = ""
    if e["url"] and e["url"] != "?":
        link_html = f'<a href="{e["url"]}" style="display:inline-block;margin-top:8px;color:#0066cc;font-size:13px;text-decoration:none;">More info →</a>'

    return f"""<tr>
      <td style="padding:14px 0;border-bottom:1px solid #f0f0f0;vertical-align:top;">
        <div style="font-size:15px;font-weight:600;color:#1a1a1a;margin-bottom:3px;">
          {e["name"]}{price_html}
        </div>
        <div style="font-size:13px;color:#888888;margin-bottom:6px;">{meta}</div>
        <div style="font-size:14px;color:#444444;line-height:1.55;">{e["description"]}</div>
        {link_html}
      </td>
    </tr>"""


def group_by_date(events):
    groups = {}
    for e in events:
        groups.setdefault(e["date"], []).append(e)
    return groups


def build_email_html(events, label):
    today_str = datetime.date.today().strftime("%B %d, %Y")
    groups = group_by_date(events)

    sections_html = ""
    for date, date_events in groups.items():
        cards = "".join(event_card_html(e) for e in date_events)
        sections_html += f"""
        <tr>
          <td style="padding-top:22px;padding-bottom:2px;">
            <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;color:#aaaaaa;border-bottom:1px solid #eeeeee;padding-bottom:6px;">{date}</div>
          </td>
        </tr>
        {cards}"""

    if not sections_html:
        sections_html = """<tr><td style="padding:24px 0;color:#888;font-size:14px;text-align:center;">
            No events found this week. Try running again or check the sites directly.
        </td></tr>"""

    total = len(events)

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#efefef;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="padding:28px 0;">
    <tr><td align="center">
      <table width="620" cellpadding="0" cellspacing="0" style="max-width:620px;width:100%;background:#ffffff;border-radius:10px;overflow:hidden;">

        <!-- Header -->
        <tr>
          <td style="background:#111111;padding:30px 36px 28px;">
            <div style="font-size:11px;letter-spacing:0.14em;text-transform:uppercase;color:#666666;margin-bottom:10px;">Cambridge &amp; Boston</div>
            <div style="font-size:24px;font-weight:700;color:#ffffff;line-height:1.2;margin-bottom:6px;">What's on this week</div>
            <div style="font-size:13px;color:#888888;">{label} &nbsp;·&nbsp; {today_str} &nbsp;·&nbsp; {total} events</div>
          </td>
        </tr>

        <!-- Events -->
        <tr>
          <td style="padding:4px 36px 28px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              {sections_html}
            </table>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f7f7f7;padding:18px 36px;border-top:1px solid #eeeeee;">
            <div style="font-size:12px;color:#bbbbbb;line-height:1.7;">
              Generated by your events agent · Claude AI + web search<br>
              Sources: {', '.join(CUSTOM_SITES[:5])}{'…' if len(CUSTOM_SITES) > 5 else ''}
            </div>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""

# ── EMAIL SENDING ─────────────────────────────────────────────────────────────

def send_via_sendgrid(subject, html_body):
    api_key = os.environ.get("SENDGRID_API_KEY")
    if not api_key:
        raise ValueError("SENDGRID_API_KEY not set")
    resp = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "personalizations": [{"to": [{"email": EMAIL_TO}]}],
            "from": {"email": EMAIL_FROM},
            "subject": subject,
            "content": [{"type": "text/html", "value": html_body}],
        },
        timeout=15,
    )
    if resp.status_code not in (200, 202):
        raise RuntimeError(f"SendGrid error {resp.status_code}: {resp.text}")
    print(f"  ✅ Email sent via SendGrid ({resp.status_code})")


def send_via_resend(subject, html_body):
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise ValueError("RESEND_API_KEY not set")
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": EMAIL_FROM, "to": [EMAIL_TO], "subject": subject, "html": html_body},
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Resend error {resp.status_code}: {resp.text}")
    print(f"  ✅ Email sent via Resend ({resp.status_code})")


def send_email(subject, html_body):
    if EMAIL_PROVIDER == "resend":
        send_via_resend(subject, html_body)
    else:
        send_via_sendgrid(subject, html_body)

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    today, end, label = get_date_window()

    print(f"📅 {label}")
    print(f"📍 {CITY} · {len(CUSTOM_SITES)} sites · batch size {BATCH_SIZE}\n")

    # 1. Fetch raw event lines from Claude (batched)
    raw_lines = fetch_all_events(today, end, label)
    print(f"\n  📋 Raw lines returned: {len(raw_lines)}")

    # 2. Parse and deduplicate
    events = parse_events(raw_lines)
    print(f"  🎯 Unique events after dedup: {len(events)}")

    if not events:
        print("  ⚠️  No events parsed. Check output above.")
        return

    # 3. Build email
    subject = f"📍 Cambridge & Boston — {today.strftime('%b %d')} · {len(events)} events this week"
    html = build_email_html(events, label)

    # 4. Save local preview
    preview_path = f"preview_{today.isoformat()}.html"
    with open(preview_path, "w") as f:
        f.write(html)
    print(f"  💾 Preview saved: {preview_path}")

    # 5. Send
    print(f"  📧 Sending to {EMAIL_TO}...")
    send_email(subject, html)
    print("\n✅ Done.")


if __name__ == "__main__":
    main()


# ══════════════════════════════════════════════════════════════════════════════
# GITHUB ACTIONS WORKFLOW  (.github/workflows/main.yml)
# ══════════════════════════════════════════════════════════════════════════════
#
# name: Daily Events Digest
#
# on:
#   schedule:
#     - cron: '0 13 * * *'   # 8am EST = 1pm UTC
#   workflow_dispatch:
#
# jobs:
#   send-digest:
#     runs-on: ubuntu-latest
#     steps:
#       - uses: actions/checkout@v4
#       - uses: actions/setup-python@v5
#         with:
#           python-version: '3.11'
#       - run: pip install anthropic requests python-dotenv
#       - run: python events_agent.py
#         env:
#           ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
#           SENDGRID_API_KEY: ${{ secrets.SENDGRID_API_KEY }}
#       - uses: actions/upload-artifact@v4
#         with:
#           name: email-preview
#           path: preview_*.html
#           retention-days: 7