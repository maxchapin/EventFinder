"""
events_agent.py
───────────────
Daily (or weekly) events digest for Cambridge, MA.
Uses Claude with web search to find events, then sends a formatted email.

SETUP
─────
1. Install deps:
   pip install anthropic requests python-dotenv

2. Create a .env file next to this script:
   ANTHROPIC_API_KEY=sk-ant-...
   SENDGRID_API_KEY=SG....          # OR use RESEND_API_KEY below
   # RESEND_API_KEY=re_...          # Alternative to SendGrid

3. Run manually:
   python events_agent.py

4. Schedule daily (cron example — runs at 8am):
   0 8 * * * cd /path/to/script && python events_agent.py

   OR use GitHub Actions (see README section at bottom of this file).
"""

import os
import json
import datetime
import requests
from dotenv import load_dotenv
import anthropic

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────

CITY = "Cambridge, MA"
RADIUS_MILES = 30
CATEGORIES = [
    "live music",
    "festivals",
    "hiking & nature",
    "food & drink",
    "comedy",
    "markets & fairs",
]
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
EMAIL_FROM = "maxchapin430@gmail.com"  # ← change to your verified sender
SEND_TIME = "08:00"
FORMAT = "week"  # digest | top5 | week

# Email provider: "sendgrid" or "resend"
EMAIL_PROVIDER = "sendgrid"

# ── DATE HELPERS ──────────────────────────────────────────────────────────────

def get_date_window():
    today = datetime.date.today()
    if FORMAT == "week":
        # Next 7 days
        end = today + datetime.timedelta(days=7)
        label = f"the week of {today.strftime('%B %d')}–{end.strftime('%B %d, %Y')}"
    elif FORMAT == "top5":
        end = today + datetime.timedelta(days=3)
        label = f"the next few days ({today.strftime('%B %d')}–{end.strftime('%B %d')})"
    else:  # digest
        end = today + datetime.timedelta(days=2)
        label = f"today and tomorrow ({today.strftime('%B %d')}–{end.strftime('%B %d')})"
    return today, end, label

# ── CLAUDE AGENT ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a hyper-local events scout for the Boston/Cambridge area. 
Your job is to find real, specific, upcoming events and return them in clean HTML 
for an email digest. Only include events with confirmed dates, times, and venues.
Do not fabricate events. If you cannot find details for something, skip it.
Return ONLY the HTML body content — no markdown, no backticks, no preamble."""


def build_user_prompt(today, end, label):
    sites_formatted = "\n".join(f"  - {s}" for s in CUSTOM_SITES)
    cats_formatted = ", ".join(CATEGORIES)

    return f"""Find events happening in and around {CITY} (within ~{RADIUS_MILES} miles) 
for {label}.

Categories to cover: {cats_formatted}

Always check these specific sites/sources:
{sites_formatted}

Also search broadly for events in the Boston/Cambridge metro area across these categories.

For each event found, include:
- Event name
- Date & time
- Venue name and neighborhood
- A 1–2 sentence description
- Ticket/RSVP link (if available)
- Price (if known; otherwise omit)

Format the output as clean, readable HTML email body content. Use this structure:

<h2 style="font-family: Georgia, serif; color: #1a1a1a; border-bottom: 2px solid #e0e0e0; padding-bottom: 8px;">🎵 Live Music</h2>
[events in this category]

Use these category headers (only include a section if you found events):
- 🎵 Live Music
- 🎉 Festivals & Big Events
- 🌿 Hiking & Nature
- 🍽️ Food & Drink
- 😂 Comedy
- 🛍️ Markets & Fairs

Each event should be formatted as:
<div style="margin-bottom: 20px; padding: 14px; background: #f9f9f9; border-left: 3px solid #333; border-radius: 4px;">
  <strong style="font-size: 16px;">[Event Name]</strong><br>
  <span style="color: #555; font-size: 14px;">📅 [Day, Date] · ⏰ [Time] · 📍 [Venue, Neighborhood]</span><br>
  <p style="margin: 8px 0; font-size: 14px; color: #333;">[Description]</p>
  [<a href="[link]" style="color: #0066cc; font-size: 13px;">Tickets / More info →</a>]
  [<span style="font-size: 13px; color: #666;">💰 [Price]</span>]
</div>

Aim for 3–6 events per category. Prioritize variety across days of the week.
Today's date is {today.strftime('%A, %B %d, %Y')}. Only include events from today through {end.strftime('%B %d, %Y')}."""


def fetch_events_from_claude(today, end, label):
    """Call Claude with web search enabled to find events."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = build_user_prompt(today, end, label)

    print("🔍 Asking Claude to search for events...")

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
            }
        ],
        messages=[{"role": "user", "content": prompt}],
    )

    # Extract the final text response (after tool use)
    html_content = ""
    for block in response.content:
        if block.type == "text":
            html_content += block.text

    return html_content.strip()


# ── EMAIL BUILDING ────────────────────────────────────────────────────────────

def build_email_html(events_html, label):
    today_str = datetime.date.today().strftime("%B %d, %Y")
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Your Events Digest</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f4f4f4; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f4f4f4; padding: 24px 0;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="max-width: 600px; width: 100%; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
          
          <!-- Header -->
          <tr>
            <td style="background-color: #1a1a1a; padding: 28px 32px;">
              <h1 style="margin: 0; color: #ffffff; font-size: 22px; font-weight: 600; letter-spacing: -0.3px;">
                📍 Cambridge & Boston Events
              </h1>
              <p style="margin: 6px 0 0; color: #aaaaaa; font-size: 14px;">
                {label} · Sent {today_str}
              </p>
            </td>
          </tr>

          <!-- Events content -->
          <tr>
            <td style="padding: 28px 32px; color: #1a1a1a; font-size: 15px; line-height: 1.6;">
              {events_html}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background-color: #f9f9f9; padding: 20px 32px; border-top: 1px solid #e8e8e8;">
              <p style="margin: 0; font-size: 12px; color: #999999; line-height: 1.6;">
                This digest was generated by your personal events agent using Claude AI.<br>
                Sources checked: {', '.join(CUSTOM_SITES[:4])} + web search.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


# ── EMAIL SENDING ─────────────────────────────────────────────────────────────

def send_via_sendgrid(subject, html_body):
    api_key = os.environ.get("SENDGRID_API_KEY")
    if not api_key:
        raise ValueError("SENDGRID_API_KEY not set in environment")

    payload = {
        "personalizations": [{"to": [{"email": EMAIL_TO}]}],
        "from": {"email": EMAIL_FROM},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_body}],
    }

    resp = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )

    if resp.status_code not in (200, 202):
        raise RuntimeError(f"SendGrid error {resp.status_code}: {resp.text}")

    print(f"✅ Email sent via SendGrid (status {resp.status_code})")


def send_via_resend(subject, html_body):
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise ValueError("RESEND_API_KEY not set in environment")

    payload = {
        "from": EMAIL_FROM,
        "to": [EMAIL_TO],
        "subject": subject,
        "html": html_body,
    }

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Resend error {resp.status_code}: {resp.text}")

    print(f"✅ Email sent via Resend (status {resp.status_code})")


def send_email(subject, html_body):
    if EMAIL_PROVIDER == "resend":
        send_via_resend(subject, html_body)
    else:
        send_via_sendgrid(subject, html_body)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    today, end, label = get_date_window()

    print(f"📅 Finding events for: {label}")
    print(f"📍 Location: {CITY} ({RADIUS_MILES}-mile radius)")
    print(f"🏷️  Categories: {', '.join(CATEGORIES)}")
    print()

    # 1. Fetch events from Claude
    events_html = fetch_events_from_claude(today, end, label)

    if not events_html:
        print("⚠️  No events content returned from Claude. Aborting.")
        return

    print(f"✅ Got {len(events_html)} chars of event content from Claude")

    # 2. Build full email
    subject_map = {
        "week": f"📍 Your week in Cambridge & Boston — {today.strftime('%b %d')}",
        "top5": f"📍 Top picks near Cambridge this weekend",
        "digest": f"📍 Cambridge & Boston events — {today.strftime('%A, %b %d')}",
    }
    subject = subject_map.get(FORMAT, subject_map["week"])
    full_html = build_email_html(events_html, label)

    # 3. Optionally save a local preview
    preview_path = f"preview_{today.isoformat()}.html"
    with open(preview_path, "w") as f:
        f.write(full_html)
    print(f"💾 Local preview saved: {preview_path}")

    # 4. Send the email
    print(f"📧 Sending to {EMAIL_TO}...")
    send_email(subject, full_html)


if __name__ == "__main__":
    main()


# ══════════════════════════════════════════════════════════════════════════════
# DEPLOYMENT OPTIONS
# ══════════════════════════════════════════════════════════════════════════════
#
# OPTION A — Cron on your Mac/Linux machine
# ─────────────────────────────────────────
# Edit your crontab:   crontab -e
# Add this line (runs at 8am daily):
#   0 8 * * * cd /path/to/script && /usr/bin/python3 events_agent.py >> events.log 2>&1
#
#
# OPTION B — GitHub Actions (free, cloud, no machine needed)
# ──────────────────────────────────────────────────────────
# 1. Push this script to a private GitHub repo.
# 2. Add ANTHROPIC_API_KEY and SENDGRID_API_KEY (or RESEND_API_KEY) as
#    repository secrets (Settings → Secrets and variables → Actions).
# 3. Create .github/workflows/events.yml with this content:
#
# name: Daily Events Digest
# on:
#   schedule:
#     - cron: '0 13 * * *'   # 8am EST = 1pm UTC
#   workflow_dispatch:         # allows manual trigger from GitHub UI
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
#
#
# OPTION C — Run locally on demand
# ──────────────────────────────────
# pip install anthropic requests python-dotenv
# python events_agent.py
#
# ══════════════════════════════════════════════════════════════════════════════