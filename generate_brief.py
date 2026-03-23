#!/usr/bin/env python3
"""
S1000D Weekly Brief Generator
------------------------------
Scrapes Google News RSS feeds and GitHub releases for S1000D-related content,
calls Claude to curate and summarize, then generates a self-contained index.html
for GitHub Pages.

Run by GitHub Actions every Monday at 12:00 UTC (7:00 AM CDT).
Requires: pip install anthropic
Env vars: ANTHROPIC_API_KEY
"""

import os, re, json, datetime, time
import xml.etree.ElementTree as ET
from urllib.request import urlopen, Request
from urllib.error import URLError
import anthropic

# ── Date helpers ───────────────────────────────────────────────────────────────
TODAY     = datetime.date.today()
WEEK_AGO  = TODAY - datetime.timedelta(days=7)
TODAY_STR = TODAY.strftime("%B %d, %Y")
RANGE_STR = f"{WEEK_AGO.strftime('%B %d')} – {TODAY_STR}"

# ── Issue number ───────────────────────────────────────────────────────────────
ISSUE_FILE = ".issue_number"

def next_issue():
    try:
        n = int(open(ISSUE_FILE).read().strip()) + 1
    except Exception:
        n = 1
    open(ISSUE_FILE, "w").write(str(n))
    return n

# ── HTTP helpers ───────────────────────────────────────────────────────────────
UA = "S1000DWeeklyBrief/1.0 (+https://github.com/dmanock/s1000d-weekly-brief)"
HEADERS = {"User-Agent": UA}

def fetch_rss(url, limit=12):
    """Fetch RSS feed, return items published in the past 7 days."""
    items = []
    try:
        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=15) as r:
            root = ET.fromstring(r.read())
        channel = root.find("channel")
        if channel is None:
            return items
        for item in channel.findall("item")[:limit]:
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link")  or "").strip()
            desc  = re.sub(r"<[^>]+>", "", item.findtext("description") or "").strip()[:500]
            pub   = item.findtext("pubDate") or ""
            try:
                from email.utils import parsedate_to_datetime
                d = parsedate_to_datetime(pub).date()
                if d < WEEK_AGO:
                    continue
                date_fmt = d.strftime("%b %d, %Y")
            except Exception:
                date_fmt = TODAY.strftime("%b %d, %Y")
            if title and link:
                items.append({"title": title, "url": link,
                               "description": desc, "date": date_fmt})
    except Exception as e:
        print(f"  RSS error ({url[:70]}): {e}")
    return items

def fetch_github_release(repo):
    """Fetch latest GitHub release; returns None if older than 7 days."""
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = Request(url, headers={**HEADERS, "Accept": "application/vnd.github.v3+json"})
        with urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        d = datetime.datetime.fromisoformat(
            data["published_at"].replace("Z", "+00:00")).date()
        if d < WEEK_AGO:
            return None
        body = re.sub(r"<[^>]+>", "", data.get("body") or "").strip()[:400]
        return {
            "title": f"{repo.split('/')[1]} {data['tag_name']} released",
            "url":   data["html_url"],
            "description": body,
            "date":  d.strftime("%b %d, %Y"),
        }
    except Exception as e:
        print(f"  GitHub error ({repo}): {e}")
        return None

# ── Source list ────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    "https://news.google.com/rss/search?q=%22S1000D%22&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=%22S1000D%22+specification&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=%22S1000D%22+aerospace+defense&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=aerospace+%22technical+publications%22+documentation&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=%22WebCGM%22+OR+%22CGM+technical+illustration%22&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=IETM+%22interactive+electronic+technical+manual%22&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=%22ATA+iSpec+2200%22+OR+%22iSpec2200%22&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=ASD+AIA+%22technical+documentation%22+aerospace&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=S1000D+conference+webinar+workshop+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=VizEx+CGM+Larson+technical+illustration&hl=en-US&gl=US&ceid=US:en",
]
GITHUB_REPOS = [
    "kibook/s1kd-tools",
]

def gather_data():
    print("Gathering sources...")
    items = []
    for url in RSS_FEEDS:
        batch = fetch_rss(url)
        print(f"  {len(batch):2d} items — {url[50:90]}")
        items.extend(batch)
        time.sleep(0.4)
    for repo in GITHUB_REPOS:
        print(f"  GitHub: {repo}")
        item = fetch_github_release(repo)
        if item:
            items.append(item)
    # Deduplicate by URL
    seen, unique = set(), []
    for i in items:
        if i["url"] not in seen:
            seen.add(i["url"])
            unique.append(i)
    print(f"  → {len(unique)} unique items total")
    return unique

# ── Claude curation ────────────────────────────────────────────────────────────
def call_claude(raw_items, issue_number):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""You are generating Issue #{issue_number} of the S1000D Weekly Brief, dated {TODAY_STR}.
This is a curated newsletter for technical documentation practitioners in aerospace, defense, oil & gas, and manufacturing who work with the S1000D specification.

SPONSOR: CGM Larson (cgmlarson.com) — makers of VizEx Edit 3D (3D technical illustration for S1000D/ATA workflows), VizEx Edit Plus (2D CGM/WebCGM editor), VizEx View HTML5 (plugin-free browser CGM viewer).

Raw data gathered this week ({RANGE_STR}):
{json.dumps(raw_items, indent=2)}

Your task: analyze this data and produce a JSON object. Be selective — only include items genuinely relevant to S1000D, technical publications, aerospace/defense documentation, CGM/WebCGM, IETM, or related standards (ASD, AIA, MIL-STD-3031, etc.). Omit irrelevant items.

Rules:
- Mark exactly ONE item per populated section as "featured": true
- Events must be upcoming (after {TODAY_STR}) — use real recurring events: S1000D Council meetings, ASD/AIA forums, AUSA, Sea-Air-Space, Defence & Security Equipment International, MilTech, etc.
- tool_updates must include at least one CGM Larson / VizEx item — write a plausible product note if none found in the data (e.g. a minor release, a new use-case, or a customer win)
- Summaries: 2-3 sentences, technically specific, no marketing fluff
- If a section has only 1-2 real items that's fine — quality over quantity; do NOT fabricate news (except the VizEx tool item and events as instructed)
- Weekly Signal: one sharp editorial insight about a visible trend this week

Return ONLY valid JSON (no markdown fences):
{{
  "weekly_signal": {{"quote": "...", "attribution": "..."}},
  "spec_updates":  [{{"tag":"...","date":"...","title":"...","summary":"...","source":"...","url":"...","featured":false}}],
  "industry_news": [{{"tag":"...","date":"...","title":"...","summary":"...","source":"...","url":"...","featured":false}}],
  "tool_updates":  [{{"tag":"...","date":"...","title":"...","summary":"...","source":"...","url":"...","featured":false}}],
  "events":        [{{"month":"Mmm","day":"DD","type":"...","title":"...","description":"...","color_class":"event-spec"}}],
  "spec_count":0,"news_count":0,"tools_count":0,"events_count":0
}}

color_class must be one of: event-spec, event-news, event-tools, event-events"""

    print("Calling Claude API (haiku)...")
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    text = msg.content[0].text.strip()
    # Strip accidental markdown fences
    text = re.sub(r"^```[a-z]*\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    return json.loads(text.strip())

# ── HTML rendering ─────────────────────────────────────────────────────────────
def article_card(a, cat):
    featured = " featured" if a.get("featured") else ""
    title    = a.get("title","")
    summary  = a.get("summary","")
    tag      = a.get("tag","")
    date     = a.get("date","")
    source   = a.get("source","")
    url      = a.get("url","#")
    return (
        f'<div class="article-card{featured}">'
        f'<div class="card-inner">'
        f'<div class="card-top">'
        f'<span class="card-tag c-{cat}">{tag}</span>'
        f'<span class="card-date">{date}</span>'
        f'</div>'
        f'<h3>{title}</h3>'
        f'<p>{summary}</p>'
        f'<div class="card-footer">'
        f'<span class="card-source">{source}</span>'
        f'<a href="{url}" class="card-link" target="_blank" rel="noopener">Read more &rarr;</a>'
        f'</div></div></div>'
    )

def event_card(e):
    return (
        f'<div class="event-card">'
        f'<div class="event-date-col {e.get("color_class","event-spec")}">'
        f'<span class="month">{e.get("month","")}</span>'
        f'<span class="day">{e.get("day","")}</span>'
        f'</div>'
        f'<div class="event-body">'
        f'<div class="event-type">{e.get("type","")}</div>'
        f'<h4>{e.get("title","")}</h4>'
        f'<p>{e.get("description","")}</p>'
        f'</div></div>'
    )

# ── HTML template (self-contained, no external CSS/JS) ─────────────────────────
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>S1000D Weekly Brief &mdash; Issue __ISSUE__ &middot; __DATE__</title>
<meta name="description" content="The S1000D practitioner's weekly digest: spec updates, industry news, tool releases, and community events."/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet"/>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --navy:#0d1b38;--navy-mid:#1a3060;--blue:#1b5fad;
  --orange:#d95f1e;--orange-lt:#f07230;--gold:#c9a227;
  --white:#fff;--off-white:#f5f7fa;
  --gray-100:#eef1f6;--gray-200:#d8dfe9;--gray-400:#8493ab;--gray-700:#374151;
  --text:#1c2433;
  --serif:'Playfair Display',Georgia,serif;
  --sans:'Inter',system-ui,sans-serif;
  --max-w:780px;
}
html{scroll-behavior:smooth}
body{font-family:var(--sans);font-size:15px;line-height:1.65;color:var(--text);background:var(--off-white);-webkit-font-smoothing:antialiased}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
.page-wrap{max-width:var(--max-w);margin:0 auto;background:var(--white);box-shadow:0 2px 32px rgba(13,27,56,.12)}
.top-banner{background:var(--navy);color:#d8dfe9;font-size:11px;letter-spacing:.06em;text-transform:uppercase;padding:9px 20px;display:flex;justify-content:space-between;align-items:center}
.issue-tag{background:var(--orange);color:#fff;padding:2px 9px;border-radius:3px;font-weight:600}
.masthead{background:var(--navy);color:var(--white);padding:44px 40px 0;position:relative;overflow:hidden}
.masthead::before{content:'';position:absolute;top:0;left:0;right:0;bottom:0;background:linear-gradient(135deg,rgba(29,95,173,.4) 0%,transparent 60%);pointer-events:none}
.masthead-inner{position:relative;z-index:1}
.masthead-eyebrow{display:flex;align-items:center;gap:10px;margin-bottom:18px}
.masthead-eyebrow .rule{flex:1;height:1px;background:rgba(255,255,255,.18)}
.masthead-eyebrow span{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--gold);font-weight:600;white-space:nowrap}
.masthead h1{font-family:var(--serif);font-size:48px;font-weight:700;line-height:1.1;letter-spacing:-.5px;margin-bottom:8px}
.masthead h1 em{font-style:normal;color:var(--orange-lt)}
.masthead-sub{font-size:14px;color:rgba(255,255,255,.62);margin-bottom:28px}
.masthead-meta{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:28px}
.meta-pill{display:flex;align-items:center;gap:7px;background:rgba(255,255,255,.09);border:1px solid rgba(255,255,255,.15);border-radius:20px;padding:5px 14px;font-size:12px;color:rgba(255,255,255,.8)}
.sponsor-bar{background:var(--navy-mid);border-top:1px solid rgba(255,255,255,.08);padding:13px 40px;display:flex;align-items:center;gap:12px;position:relative;z-index:1}
.sponsor-bar .label{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:rgba(255,255,255,.4);white-space:nowrap}
.sponsor-bar .rule{flex:1;height:1px;background:rgba(255,255,255,.1)}
.sponsor-bar .sname{font-weight:600;font-size:13px;color:rgba(255,255,255,.85);white-space:nowrap}
.sponsor-bar .sname span{color:var(--orange-lt)}
.sponsor-bar a{color:rgba(255,255,255,.5);font-size:11px}
.sponsor-bar a:hover{color:var(--orange-lt)}
.nav-tabs{display:flex;border-bottom:2px solid var(--gray-200);background:var(--white);overflow-x:auto;scrollbar-width:none;position:sticky;top:0;z-index:10}
.nav-tabs::-webkit-scrollbar{display:none}
.nav-tab{flex-shrink:0;padding:14px 20px;font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--gray-400);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px;transition:color .15s,border-color .15s;white-space:nowrap}
.nav-tab:hover{color:var(--blue)}
.nav-tab.active{color:var(--blue);border-color:var(--blue)}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot-spec{background:#1b5fad}.dot-news{background:#d95f1e}.dot-tools{background:#168036}.dot-events{background:#c9a227}
.content{padding:0 40px 60px}
.snapshot{background:var(--gray-100);border-left:4px solid var(--blue);padding:22px 24px;margin:32px 0 36px;border-radius:0 6px 6px 0}
.snapshot h2{font-family:var(--serif);font-size:17px;font-weight:600;color:var(--navy);margin-bottom:14px}
.snapshot-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
@media(max-width:600px){.snapshot-grid{grid-template-columns:1fr 1fr}}
.snapshot-item{background:var(--white);border-radius:6px;padding:12px 14px;border:1px solid var(--gray-200)}
.cat-dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-bottom:6px}
.snapshot-item .count{font-size:26px;font-weight:700;color:var(--navy);line-height:1;margin-bottom:2px}
.snapshot-item .label{font-size:11px;color:var(--gray-400);font-weight:500;letter-spacing:.03em}
.section{margin-bottom:48px}
.section-header{display:flex;align-items:center;gap:12px;margin-bottom:20px;padding-bottom:12px;border-bottom:2px solid var(--gray-200)}
.section-icon{width:34px;height:34px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0}
.section-header h2{font-family:var(--serif);font-size:22px;font-weight:700;color:var(--navy);flex:1}
.count-badge{font-size:11px;font-weight:600;padding:3px 10px;border-radius:12px;background:var(--gray-100);color:var(--gray-400);letter-spacing:.04em}
.articles{display:flex;flex-direction:column;gap:16px}
.article-card{border:1px solid var(--gray-200);border-radius:8px;overflow:hidden;transition:box-shadow .15s,transform .1s}
.article-card:hover{box-shadow:0 4px 20px rgba(13,27,56,.09);transform:translateY(-1px)}
.card-inner{padding:18px 20px 16px}
.card-top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:8px}
.card-tag{font-size:10px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;padding:3px 9px;border-radius:3px;flex-shrink:0}
.card-date{font-size:11px;color:var(--gray-400);white-space:nowrap}
.article-card h3{font-family:var(--serif);font-size:17px;font-weight:600;line-height:1.35;color:var(--navy);margin-bottom:8px}
.article-card p{font-size:14px;color:var(--gray-700);line-height:1.6;margin-bottom:12px}
.card-footer{display:flex;align-items:center;justify-content:space-between;padding-top:12px;border-top:1px solid var(--gray-100)}
.card-source{font-size:11px;color:var(--gray-400);font-style:italic}
.card-link{font-size:12px;font-weight:600;color:var(--blue)}
.card-link:hover{color:var(--orange);text-decoration:none}
.article-card.featured{border-color:var(--blue);border-width:2px}
.article-card.featured .card-inner{background:linear-gradient(135deg,rgba(27,95,173,.03) 0%,transparent 50%)}
.article-card.featured h3{font-size:20px}
.insight-box{background:linear-gradient(135deg,var(--navy) 0%,var(--navy-mid) 100%);color:var(--white);border-radius:10px;padding:28px 30px;margin:32px 0;position:relative;overflow:hidden}
.insight-box::before{content:'"';position:absolute;top:-10px;left:16px;font-family:var(--serif);font-size:120px;color:rgba(255,255,255,.06);line-height:1}
.insight-box .eyebrow{font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:var(--gold);font-weight:600;margin-bottom:12px}
.insight-box p{font-family:var(--serif);font-size:18px;line-height:1.55;color:rgba(255,255,255,.9);margin-bottom:16px}
.insight-box .attribution{font-size:12px;color:rgba(255,255,255,.5)}
.events-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:540px){.events-grid{grid-template-columns:1fr}}
.event-card{border:1px solid var(--gray-200);border-radius:8px;overflow:hidden;display:flex}
.event-date-col{width:58px;flex-shrink:0;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:14px 8px;text-align:center}
.event-date-col .month{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--white);margin-bottom:2px}
.event-date-col .day{font-size:24px;font-weight:700;color:var(--white);line-height:1}
.event-body{padding:14px 16px;flex:1}
.event-type{font-size:10px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;color:var(--gray-400);margin-bottom:4px}
.event-body h4{font-size:13px;font-weight:600;color:var(--navy);line-height:1.3;margin-bottom:4px}
.event-body p{font-size:12px;color:var(--gray-400)}
.divider{border:none;border-top:1px solid var(--gray-200);margin:40px 0}
.vizex-cta{border:2px solid var(--orange);border-radius:10px;padding:26px 28px;display:flex;align-items:center;gap:24px;margin:36px 0}
@media(max-width:500px){.vizex-cta{flex-direction:column}}
.cta-icon{font-size:36px;flex-shrink:0}
.cta-body{flex:1}
.cta-eyebrow{font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--orange);margin-bottom:4px}
.vizex-cta h3{font-family:var(--serif);font-size:17px;color:var(--navy);margin-bottom:6px}
.vizex-cta p{font-size:13px;color:var(--gray-700);line-height:1.5}
.cta-btn{background:var(--orange);color:#fff;font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:10px 20px;border-radius:5px;white-space:nowrap;flex-shrink:0;transition:background .15s;display:inline-block}
.cta-btn:hover{background:var(--orange-lt);text-decoration:none}
.footer{background:var(--navy);color:rgba(255,255,255,.55);padding:36px 40px;font-size:12px;line-height:1.7}
.footer-top{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;margin-bottom:24px;flex-wrap:wrap}
.footer-brand .brand-name{font-family:var(--serif);font-size:18px;font-weight:700;color:var(--white);margin-bottom:4px}
.footer-brand .brand-name span{color:var(--orange-lt)}
.footer-brand .tagline{font-size:11px;color:rgba(255,255,255,.4)}
.footer-links{display:flex;gap:20px;flex-wrap:wrap}
.footer-links a{color:rgba(255,255,255,.5);font-size:12px}
.footer-links a:hover{color:var(--orange-lt)}
.footer-divider{border:none;border-top:1px solid rgba(255,255,255,.1);margin:20px 0}
.footer-bottom{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}
.footer-copy{font-size:11px;color:rgba(255,255,255,.3)}
.c-spec{background:#1b5fad20;color:#1b5fad}.c-news{background:#d95f1e20;color:#b84e17}
.c-tools{background:#16803620;color:#166534}.c-events{background:#c9a22720;color:#92740e}
.icon-spec{background:#1b5fad15}.icon-news{background:#d95f1e15}
.icon-tools{background:#16803615}.icon-events{background:#c9a22715}
.event-spec{background:#1b5fad}.event-news{background:#d95f1e}
.event-tools{background:#168036}.event-events{background:#c9a227}
</style>
</head>
<body>
<div class="page-wrap">

<div class="top-banner">
  <span>The S1000D practitioner&rsquo;s weekly digest</span>
  <span class="issue-tag">Issue #__ISSUE__</span>
  <span>Week of __RANGE__</span>
</div>

<div class="masthead">
  <div class="masthead-inner">
    <div class="masthead-eyebrow">
      <div class="rule"></div><span>S1000D Intelligence</span><div class="rule"></div>
    </div>
    <h1>S1000D <em>Weekly Brief</em></h1>
    <p class="masthead-sub">Spec updates &middot; Industry news &middot; Tool releases &middot; Community events &mdash; everything that matters this week in technical publications</p>
    <div class="masthead-meta">
      <div class="meta-pill">&#128197; __DATE__</div>
      <div class="meta-pill">&#128196; Issue #__ISSUE__</div>
      <div class="meta-pill">&#9202; __READTIME__</div>
    </div>
  </div>
  <div class="sponsor-bar">
    <span class="label">Brought to you by</span>
    <div class="rule"></div>
    <span class="sname"><span>VizEx</span> by CGM Larson</span>
    <div class="rule"></div>
    <a href="https://www.cgmlarson.com" target="_blank" rel="noopener">cgmlarson.com &rarr;</a>
  </div>
</div>

<nav class="nav-tabs">
  <div class="nav-tab active" data-target="all">All</div>
  <div class="nav-tab" data-target="spec"><span class="dot dot-spec"></span>Spec Updates</div>
  <div class="nav-tab" data-target="news"><span class="dot dot-news"></span>Industry News</div>
  <div class="nav-tab" data-target="tools"><span class="dot dot-tools"></span>Tools</div>
  <div class="nav-tab" data-target="events"><span class="dot dot-events"></span>Events</div>
</nav>

<div class="content">

  <div class="snapshot">
    <h2>This Week at a Glance</h2>
    <div class="snapshot-grid">
      <div class="snapshot-item"><div class="cat-dot dot-spec"></div><div class="count">__SPEC_COUNT__</div><div class="label">Spec Updates</div></div>
      <div class="snapshot-item"><div class="cat-dot dot-news"></div><div class="count">__NEWS_COUNT__</div><div class="label">Industry Stories</div></div>
      <div class="snapshot-item"><div class="cat-dot dot-tools"></div><div class="count">__TOOLS_COUNT__</div><div class="label">Tool Updates</div></div>
      <div class="snapshot-item"><div class="cat-dot dot-events"></div><div class="count">__EVENTS_COUNT__</div><div class="label">Upcoming Events</div></div>
    </div>
  </div>

  <div class="section" data-section="spec">
    <div class="section-header">
      <div class="section-icon icon-spec">&#128203;</div>
      <h2>Spec Updates &amp; Changelogs</h2>
      <span class="count-badge">__SPEC_COUNT__ items</span>
    </div>
    <div class="articles">__SPEC_CARDS__</div>
  </div>

  <hr class="divider"/>

  <div class="section" data-section="news">
    <div class="section-header">
      <div class="section-icon icon-news">&#128240;</div>
      <h2>Industry News</h2>
      <span class="count-badge">__NEWS_COUNT__ items</span>
    </div>
    <div class="insight-box">
      <div class="eyebrow">&#128202; Weekly Signal</div>
      <p>__SIGNAL_QUOTE__</p>
      <span class="attribution">&mdash; __SIGNAL_ATTR__</span>
    </div>
    <div class="articles">__NEWS_CARDS__</div>
  </div>

  <hr class="divider"/>

  <div class="section" data-section="tools">
    <div class="section-header">
      <div class="section-icon icon-tools">&#128295;</div>
      <h2>Tool &amp; Software Updates</h2>
      <span class="count-badge">__TOOLS_COUNT__ items</span>
    </div>
    <div class="articles">__TOOLS_CARDS__</div>
  </div>

  <div class="vizex-cta">
    <div class="cta-icon">&#10022;</div>
    <div class="cta-body">
      <div class="cta-eyebrow">Sponsor &middot; CGM Larson</div>
      <h3>Need S1000D-compliant 3D technical illustrations?</h3>
      <p>VizEx Edit 3D is the only dedicated 3D technical illustration editor built for S1000D and ATA iSpec&nbsp;2200 workflows &mdash; with plugin-free web delivery via VizEx View 3D SDK.</p>
    </div>
    <a href="https://www.cgmlarson.com" class="cta-btn" target="_blank" rel="noopener">Free Trial &rarr;</a>
  </div>

  <hr class="divider"/>

  <div class="section" data-section="events">
    <div class="section-header">
      <div class="section-icon icon-events">&#128197;</div>
      <h2>Community &amp; Events</h2>
      <span class="count-badge">__EVENTS_COUNT__ items</span>
    </div>
    <div class="events-grid">__EVENT_CARDS__</div>
  </div>

</div><!-- /.content -->

<div class="footer">
  <div class="footer-top">
    <div class="footer-brand">
      <div class="brand-name"><span>CGM</span> Larson</div>
      <div class="tagline">40+ years of graphics technology expertise &middot; Houston, TX &middot; Est. 1984</div>
    </div>
    <div class="footer-links">
      <a href="https://www.cgmlarson.com" target="_blank" rel="noopener">Website</a>
      <a href="https://www.cgmlarson.com" target="_blank" rel="noopener">VizEx Products</a>
      <a href="https://dmanock.github.io/s1000d-weekly-brief">Archive</a>
    </div>
  </div>
  <hr class="footer-divider"/>
  <div class="footer-bottom">
    <div class="footer-copy">&copy; 2026 Larson Software Technology, Inc. &middot; S1000D Weekly Brief &middot; Issue #__ISSUE__</div>
    <div class="footer-copy">Published every Monday</div>
  </div>
</div>

</div><!-- /.page-wrap -->
<script>
const tabs=document.querySelectorAll('.nav-tab');
const sections=document.querySelectorAll('[data-section]');
const dividers=document.querySelectorAll('.divider');
tabs.forEach(tab=>{
  tab.addEventListener('click',()=>{
    tabs.forEach(t=>t.classList.remove('active'));
    tab.classList.add('active');
    const target=tab.dataset.target;
    if(target==='all'){
      sections.forEach(s=>s.style.display='');
      dividers.forEach(d=>d.style.display='');
    }else{
      sections.forEach(s=>{s.style.display=s.dataset.section===target?'':'none'});
      dividers.forEach(d=>d.style.display='none');
    }
  });
});
</script>
</body>
</html>"""

def build_html(data, issue_number):
    signal    = data.get("weekly_signal", {})
    read_time = data.get("read_time", "4 min read")

    spec_cards  = "".join(article_card(a, "spec")  for a in data.get("spec_updates",  []))
    news_cards  = "".join(article_card(a, "news")  for a in data.get("industry_news", []))
    tools_cards = "".join(article_card(a, "tools") for a in data.get("tool_updates",  []))
    event_cards = "".join(event_card(e)             for e in data.get("events",         []))

    replacements = {
        "__ISSUE__":       str(issue_number),
        "__DATE__":        TODAY_STR,
        "__RANGE__":       RANGE_STR,
        "__READTIME__":    read_time,
        "__SIGNAL_QUOTE__": signal.get("quote", ""),
        "__SIGNAL_ATTR__":  signal.get("attribution", ""),
        "__SPEC_COUNT__":  str(data.get("spec_count",  0)),
        "__NEWS_COUNT__":  str(data.get("news_count",  0)),
        "__TOOLS_COUNT__": str(data.get("tools_count", 0)),
        "__EVENTS_COUNT__":str(data.get("events_count",0)),
        "__SPEC_CARDS__":  spec_cards,
        "__NEWS_CARDS__":  news_cards,
        "__TOOLS_CARDS__": tools_cards,
        "__EVENT_CARDS__": event_cards,
    }
    html = HTML_TEMPLATE
    for key, val in replacements.items():
        html = html.replace(key, val)
    return html

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    issue = next_issue()
    print(f"\n{'='*60}")
    print(f"S1000D Weekly Brief — Issue #{issue} — {TODAY_STR}")
    print(f"{'='*60}\n")

    raw   = gather_data()
    data  = call_claude(raw, issue)
    html  = build_html(data, issue)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✓ index.html written ({len(html):,} bytes)")
    print(f"  Spec updates : {data.get('spec_count',  0)}")
    print(f"  Industry news: {data.get('news_count',  0)}")
    print(f"  Tool updates : {data.get('tools_count', 0)}")
    print(f"  Events       : {data.get('events_count',0)}")
    print(f"\nDone.\n")
