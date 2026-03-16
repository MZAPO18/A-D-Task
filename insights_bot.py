#!/usr/bin/env python3
"""
Daily A&D / Gov Services Investor Brief Automation
Fetches news, generates dual-AI briefs (Claude + GPT), sends via SendGrid,
and commits the brief to the repo for delta comparison.
"""

import os
import sys
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import anthropic
import openai
import markdown as md
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from dotenv import load_dotenv

load_dotenv()

# ─── Config ─────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
SENDGRID_API_KEY  = os.environ["SENDGRID_API_KEY"]
NEWSAPI_KEY       = os.environ["NEWSAPI_KEY"]
FROM_EMAIL        = os.environ["FROM_EMAIL"]
TO_EMAIL          = os.environ.get("TO_EMAIL", "mzimmerman@apollo.com")

CLAUDE_MODEL  = "claude-sonnet-4-6"
GPT_MODEL     = "gpt-4.5-preview"   # update to gpt-5.4-thinking when available
GPT_FALLBACK  = "o4-mini"

TODAY = datetime.now(timezone.utc).strftime("%d-%b-%Y")
TODAY_ISO = datetime.now(timezone.utc).strftime("%Y-%m-%d")
BRIEFS_DIR = Path(__file__).parent / "briefs"
LATEST_BRIEF = BRIEFS_DIR / "latest.md"
TODAYS_BRIEF = BRIEFS_DIR / f"{TODAY_ISO}.md"

# ─── News topics ─────────────────────────────────────────────────────────────

TOPICS = {
    "Contract Awards & RFPs": (
        '"defense contract" OR "DoD contract" OR "government contract award" OR "federal RFP"'
    ),
    "Policy & Budget": (
        '"defense budget" OR "NDAA" OR "Pentagon policy" OR "Congress defense"'
    ),
    "Market & Stock News": (
        '"Lockheed Martin" OR "Raytheon" OR "Northrop Grumman" OR "L3Harris" '
        'OR "Booz Allen" OR "SAIC" OR "Leidos"'
    ),
    "Technology Trends": (
        '"defense AI" OR "hypersonics" OR "autonomous systems" OR "space force" OR "cyber warfare"'
    ),
}

# ─── System prompt ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """# Objective
Produce a daily private equity investor brief in Markdown for the Aerospace & Defense (A&D) end market.

## Context

End Market: Aerospace & Defense (A&D)

Each run must produce a DAILY INVESTOR BRIEF in Markdown.

## Core Rules

### Delta / Anti-Repeat Across Runs
- Optimize for new information since the prior run (~24h).
- Repeat prior information only when clearly labeled as "Update:" plus the delta.
- Carryover context: maximum 1 bullet total.
- Key News must be at least 80% new or updated within the last 24 hours; otherwise omit that section.
- Upcoming Catalysts must cover the next 30 days only, unless an item was newly announced or changed.

### Anti-Repeat Within a Single Brief
- Do not duplicate information across sections. A datapoint or development may appear only once in the entire brief.
- If an item appears in Topline Summary or Key News, do not restate it in Industry Segments, Company / Asset Signals, Company Signals (Internal), or Impacts.
- Instead, reference it as "See Key News #N".

### Section Roles
- Topline Summary: 3–5 bullets covering what changed and why it matters.
- Key News: canonical numbered list.
- Industry Segments / Company Signals / Internal: changes-only items not already included in Key News.
- First / Second / Third Order Impacts: analysis only; cite "Key News #N" and do not repeat facts.

### Changes-Only Rule for Sections 2–5
- For sections 2–5, include only changes or new datapoints since the prior run.
- Omit anything already included in Key News.
- If a section has no material changes, write exactly: "No material changes since prior run."

### Mandatory Sourcing
- Use provided news articles every run.
- Use only articles provided in the user message for sourcing.
- Do not rely on memory when current, source-verifiable information is needed.
- If a search path returns empty, partial, or suspiciously narrow results, retry with one or two alternative queries or credible sources before concluding.
- If no credible new sources are found, still produce the brief using only verified information from the current run's provided articles and apply the omission rules above.
- If a required section cannot be populated with credible new information, write exactly: "No material changes since prior run." unless that section is explicitly omittable.

### Citations
- Use inline links on 1–3 words or key numbers only.
- Only cite sources from provided articles.
- Do not fabricate citations, URLs, identifiers, or quote spans.
- Attach each citation to the specific claim it supports.

### Quality Standards
- Be precise.
- Do not hallucinate.
- Prefer concrete dates, programs, and contract IDs.
- Base claims only on provided context or current-run source outputs.
- If sources conflict, state the conflict briefly and attribute each side.
- If required context is missing, do not guess; use a reversible omission rule instead.

### Section Definitions
- Company / Asset Signals: externally observable company- or asset-level developments not already in Key News, including contracts, orders, deliveries, production milestones, M&A, financing, leadership changes, plant/program developments, and asset sale processes.
- Company Signals (Internal): internal operating or ownership signals not already in Key News, including hiring or reductions, capex plans, restructuring, integration progress, margin or cash-flow commentary, governance changes, sponsor actions, and other internal execution indicators.
- Prediction Markets: concise bullets only, covering market-implied probabilities, odds, or pricing signals relevant to A&D outcomes when credible sourced data exists; otherwise write exactly: "No material changes since prior run."

### Reasoning and Verification
- Think step by step internally.
- Verify facts before including them.
- Use only credible, source-supported information.
- Keep an internal checklist of required sections and omission rules.
- Before finalizing, check correctness, grounding, formatting, section order, anti-duplication, and whether each required section is either populated or explicitly marked as instructed.
- Apply all omission and anti-duplication rules before finalizing.

## Output Requirements
- Output valid Markdown only.
- Do not use backticks.
- The first line must be a large, bold heading using the report date in UTC:
  End Market Daily Brief (DD-MMM-YYYY) — Aerospace & Defense (A&D)
- Return exactly one Markdown document containing only the sections below, in the exact order below.
- Omit Macro & Policy entirely unless it is setup-changing.
- For any required non-omittable section with no credible new information, output exactly: "No material changes since prior run."

Produce exactly one Markdown document in the following order:

End Market Daily Brief (DD-MMM-YYYY) — Aerospace & Defense (A&D)

1. Topline Summary
   - 3–5 bullets.

2. Macro & Policy
   - Include only if setup-changing; otherwise omit this section entirely.

3. Industry Segments
   - Bullets, or "No material changes since prior run."

4. Company / Asset Signals
   - Bullets, or "No material changes since prior run."

5. Company Signals (Internal)
   - Bullets, or "No material changes since prior run."

6. Key News
   1. Numbered bullets only; canonical list for major new items.

7. First / Second / Third Order Impacts
   - Analysis bullets only.
   - Do not repeat facts already stated elsewhere; cite "Key News #N".

8. Upcoming Catalysts
   - Bullets limited to the next 30 days unless newly announced or changed; otherwise "No material changes since prior run."

9. Prediction Markets
   - Bullets with sourced probabilities, odds, or pricing signals if available; otherwise "No material changes since prior run."

## Stop Conditions
- Finish when all required sections have been handled according to their inclusion or omission rules.
- Treat the task as incomplete until all required sections are covered or explicitly omitted per the rules above.
- If a section is required but lacks credible new information, output exactly: "No material changes since prior run."
- If Macro & Policy is not setup-changing, omit it entirely."""


# ─── Helpers ─────────────────────────────────────────────────────────────────

def fetch_news(topic_name: str, query: str, max_articles: int = 8) -> list[dict]:
    """Fetch recent news articles for a topic from NewsAPI."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": query,
        "from": since,
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": max_articles,
        "apiKey": NEWSAPI_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        articles = resp.json().get("articles", [])
        print(f"  [{topic_name}] fetched {len(articles)} articles")
        return articles
    except Exception as e:
        print(f"  [{topic_name}] news fetch error: {e}")
        return []


def format_articles(topic_name: str, articles: list[dict]) -> str:
    """Format article list into a readable block for the AI prompt."""
    if not articles:
        return f"### {topic_name}\n_No articles found in the last 24 hours._\n"
    lines = [f"### {topic_name}"]
    for a in articles:
        title = a.get("title", "").strip()
        source = a.get("source", {}).get("name", "Unknown")
        url = a.get("url", "")
        desc = (a.get("description") or "").strip()
        published = (a.get("publishedAt") or "")[:10]
        lines.append(f"- **{title}** ({source}, {published})")
        if desc:
            lines.append(f"  {desc}")
        if url:
            lines.append(f"  URL: {url}")
    return "\n".join(lines) + "\n"


def load_prior_brief() -> str:
    """Load yesterday's brief for delta context."""
    if LATEST_BRIEF.exists():
        text = LATEST_BRIEF.read_text(encoding="utf-8").strip()
        if text:
            print("  Loaded prior brief for delta context.")
            return text
    print("  No prior brief found — first run.")
    return "No prior run."


def build_user_message(news_blocks: list[str], prior_brief: str) -> str:
    """Assemble the full user message for the AI."""
    news_section = "\n\n".join(news_blocks)
    return f"""Today's date (UTC): {TODAY}

## News Articles (last 24 hours)

{news_section}

---

## Prior Brief (for delta / anti-repeat context)

{prior_brief}

---

Please produce today's A&D daily investor brief following all rules in your instructions."""


def call_claude(user_message: str) -> str:
    """Call Anthropic Claude and return the brief."""
    print("  Calling Claude...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return msg.content[0].text.strip()


def call_gpt(user_message: str) -> str:
    """Call OpenAI GPT and return the brief."""
    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    def _call(model: str) -> str:
        print(f"  Calling OpenAI ({model})...")
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            max_completion_tokens=4096,
        )
        return resp.choices[0].message.content.strip()

    try:
        return _call(GPT_MODEL)
    except openai.NotFoundError:
        print(f"  Model {GPT_MODEL} not found, falling back to {GPT_FALLBACK}")
        return _call(GPT_FALLBACK)


def brief_to_html(brief_md: str, label: str, color: str) -> str:
    """Convert a Markdown brief to an HTML section."""
    html_body = md.markdown(brief_md, extensions=["tables", "nl2br"])
    return f"""
    <div style="background:#f8f9fa;border-left:4px solid {color};padding:24px 28px;margin-bottom:32px;border-radius:4px;">
      <h2 style="margin-top:0;color:{color};font-family:sans-serif;">{label}</h2>
      <div style="font-family:Georgia,serif;font-size:15px;line-height:1.7;color:#1a1a1a;">
        {html_body}
      </div>
    </div>"""


def build_email_html(claude_brief: str, gpt_brief: str) -> str:
    """Build the full HTML email."""
    claude_section = brief_to_html(claude_brief, "Claude's Brief (Anthropic)", "#7B4FDB")
    gpt_section    = brief_to_html(gpt_brief,    "GPT's Brief (OpenAI)",       "#10A37F")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>A&D Daily Brief — {TODAY}</title>
</head>
<body style="margin:0;padding:0;background:#ffffff;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#1a2744;">
    <tr>
      <td style="padding:28px 32px;">
        <h1 style="margin:0;color:#ffffff;font-family:sans-serif;font-size:22px;letter-spacing:0.5px;">
          A&amp;D Daily Investor Brief
        </h1>
        <p style="margin:6px 0 0;color:#a0b4d8;font-family:sans-serif;font-size:14px;">
          {TODAY} &nbsp;|&nbsp; Aerospace &amp; Defense / Gov Services &nbsp;|&nbsp; Dual-AI Edition
        </p>
      </td>
    </tr>
  </table>
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td style="padding:32px 32px 8px;">
        {claude_section}
        <hr style="border:none;border-top:1px solid #e0e0e0;margin:32px 0;">
        {gpt_section}
      </td>
    </tr>
  </table>
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;">
    <tr>
      <td style="padding:16px 32px;font-family:sans-serif;font-size:12px;color:#888;">
        Generated automatically by insights-bot &nbsp;&bull;&nbsp; {TODAY} UTC
        &nbsp;&bull;&nbsp; Sources: NewsAPI, Anthropic, OpenAI
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_email(html_content: str) -> None:
    """Send the email via SendGrid."""
    subject = f"A&D Daily Brief — {TODAY}"
    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=TO_EMAIL,
        subject=subject,
        html_content=html_content,
    )
    sg = SendGridAPIClient(SENDGRID_API_KEY)
    resp = sg.send(message)
    print(f"  Email sent — status {resp.status_code}")


def save_and_commit(claude_brief: str, gpt_brief: str) -> None:
    """Save briefs to repo and commit."""
    BRIEFS_DIR.mkdir(exist_ok=True)

    combined = f"# A&D Daily Brief — {TODAY}\n\n## Claude (Anthropic)\n\n{claude_brief}\n\n---\n\n## GPT (OpenAI)\n\n{gpt_brief}\n"
    TODAYS_BRIEF.write_text(combined, encoding="utf-8")
    LATEST_BRIEF.write_text(combined, encoding="utf-8")
    print(f"  Saved {TODAYS_BRIEF.name}")

    git_email = os.environ.get("GIT_USER_EMAIL", "insights-bot@noreply.github.com")
    git_name  = os.environ.get("GIT_USER_NAME",  "insights-bot")

    cmds = [
        ["git", "config", "user.email", git_email],
        ["git", "config", "user.name",  git_name],
        ["git", "add", str(TODAYS_BRIEF), str(LATEST_BRIEF)],
        ["git", "commit", "-m", f"brief: A&D daily insights {TODAY_ISO}"],
        ["git", "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent)
        if result.returncode != 0:
            # non-fatal: log and continue
            print(f"  git warn ({' '.join(cmd[:2])}): {result.stderr.strip()}")
        else:
            print(f"  git ok: {' '.join(cmd[:2])}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n=== A&D Daily Insights Bot — {TODAY} ===\n")

    # 1. Load prior brief
    print("[1/6] Loading prior brief...")
    prior_brief = load_prior_brief()

    # 2. Fetch news
    print("[2/6] Fetching news...")
    news_blocks = []
    for topic_name, query in TOPICS.items():
        articles = fetch_news(topic_name, query)
        news_blocks.append(format_articles(topic_name, articles))

    # 3. Build prompt
    print("[3/6] Building AI prompt...")
    user_message = build_user_message(news_blocks, prior_brief)

    # 4. Call AI models
    print("[4/6] Generating briefs...")
    claude_brief, gpt_brief = None, None

    try:
        claude_brief = call_claude(user_message)
        print("  Claude: OK")
    except Exception as e:
        print(f"  Claude: FAILED — {e}")
        claude_brief = f"_Claude brief unavailable: {e}_"

    try:
        gpt_brief = call_gpt(user_message)
        print("  GPT: OK")
    except Exception as e:
        print(f"  GPT: FAILED — {e}")
        gpt_brief = f"_GPT brief unavailable: {e}_"

    # 5. Send email
    print("[5/6] Sending email...")
    try:
        html = build_email_html(claude_brief, gpt_brief)
        send_email(html)
    except Exception as e:
        print(f"  Email: FAILED — {e}")
        sys.exit(1)

    # 6. Commit brief
    print("[6/6] Saving and committing brief...")
    try:
        save_and_commit(claude_brief, gpt_brief)
    except Exception as e:
        print(f"  Commit: FAILED — {e}")
        # non-fatal

    print("\n=== Done ===\n")


if __name__ == "__main__":
    main()
