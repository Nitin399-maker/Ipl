"""
IPL Tadka — Instagram Auto-Poster
Runs every 2 hours from 6 AM to 6 PM IST via GitHub Actions.
Each slot posts one aggressive, visually explosive IPL news reel.
"""

import os
import sys
import re
import html
import json
import time
import random
import hashlib
import base64
from io import BytesIO
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import feedparser
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

try:
    from moviepy.video.io.ImageSequenceClip import ImageSequenceClip
    from moviepy.audio.io.AudioFileClip import AudioFileClip
    from moviepy.audio.AudioClip import concatenate_audioclips
    from moviepy.video.VideoClip import ImageClip
    MOVIEPY_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False

try:
    from instagrapi import Client as InstaClient
    INSTAGRAPI_AVAILABLE = True
except ImportError:
    INSTAGRAPI_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE    = "https://llmfoundry.straivedemo.com/openrouter/v1"

TEXT_MODEL  = "google/gemini-2.5-pro"
IMAGE_MODEL = "google/gemini-3-pro-image-preview"

BRAND_NAME     = "IPL Tadka"
BRAND_HANDLE   = "@ipl_tadka"
BRAND_HASHTAGS = (
    "#IPLTadka #IPL2026 #IPL #CricketNews #IPLUpdates #CricketLovers "
    "#IndianPremierLeague #CricketFever #IPLCricket #IPLLive #T20Cricket "
    "#CricketIndia #ViralCricket #IPLBreaking #CricketShorts"
)

DRY_RUN      = os.environ.get("DRY_RUN", "false").lower() == "true"
REEL_DURATION = 15  # seconds

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR   = os.path.join(BASE_DIR, "assets")
FONTS_DIR    = os.path.join(ASSETS_DIR, "fonts")
MUSIC_DIR    = os.path.join(ASSETS_DIR, "music")
OUTPUT_DIR   = os.path.join(BASE_DIR, "output")
TEAMS_DIR    = os.path.join(BASE_DIR, "ipl_teams")
POSTED_LOG   = os.path.join(ASSETS_DIR, "posted_log.json")
SESSION_FILE = os.path.join(ASSETS_DIR, "session.json")

for _d in [ASSETS_DIR, FONTS_DIR, MUSIC_DIR, OUTPUT_DIR]:
    os.makedirs(_d, exist_ok=True)

IMG_WIDTH  = 1080
IMG_HEIGHT = 1080
IST        = ZoneInfo("Asia/Kolkata")

# ═══════════════════════════════════════════════════════════════════════
# TIME SLOT LOGIC  (6 slots: 06, 08, 10, 12, 14, 16 IST)
# Each slot maps to a DIFFERENT IPL topic bucket so posts stay diverse.
# ═══════════════════════════════════════════════════════════════════════

# hour (IST) → (slot_index, topic_focus)
SLOT_MAP = {
    6:  (0, "match_preview"),       # ~06:00 — Morning hype, today's match preview
    13: (1, "player_form"),         # ~13:00 — Player in form / top performer news
    19: (2, "live_match_action"),   # ~19:30 — Live match action (7:30 PM start)
    20: (3, "live_match_action"),   # ~20:00 — Live match continues
    21: (4, "live_match_action"),   # ~21:00 — Live match continues
    22: (5, "live_match_action"),   # ~22:00 — Live match final overs
    23: (6, "standings_records"),   # ~23:00 — Post-match points table / records
}

# Base minute within each hour (19 → 30 means 7:30 PM base start)
_SLOT_BASE_MIN  = {6: 0, 13: 0, 19: 30, 20: 0, 21: 0, 22: 0, 23: 0}
_VARIATION_MINS = 15  # daily ±15 min shift per slot


def _daily_offsets():
    """Per-slot minute offsets seeded by today's IST date.
    Stable throughout the day, different every day.
    """
    seed = int(datetime.now(IST).strftime("%Y%m%d"))
    rng  = random.Random(seed)
    return {h: rng.randint(-_VARIATION_MINS, _VARIATION_MINS) for h in SLOT_MAP}


def get_current_slot():
    now_ist   = datetime.now(IST)
    total_min = now_ist.hour * 60 + now_ist.minute
    offsets   = _daily_offsets()

    # Compute today's effective start time (in minutes) for each slot
    slots = sorted(
        [(h * 60 + _SLOT_BASE_MIN[h] + offsets[h], SLOT_MAP[h]) for h in SLOT_MAP],
        key=lambda x: x[0],
    )

    # The active slot is the last one whose start time has already passed
    active = slots[0][1]  # default: earliest slot
    for start_min, slot_data in slots:
        if total_min >= start_min:
            active = slot_data

    return active


# ═══════════════════════════════════════════════════════════════════════
# SECTION 1 — RSS FEEDS (IPL / Cricket focused)
# ═══════════════════════════════════════════════════════════════════════

RSS_FEEDS = [
    # Cricket-specific
    ("cricket", "https://www.espncricinfo.com/rss/content/story/feeds/0.xml"),
    ("cricket", "https://www.cricbuzz.com/rss-feeds/cricket-news"),
    ("cricket", "https://feeds.bbci.co.uk/sport/cricket/rss.xml"),
    ("cricket", "https://timesofindia.indiatimes.com/rssfeeds/4719148.cms"),   # TOI Cricket
    ("cricket", "https://www.hindustantimes.com/feeds/rss/cricket/rssfeed.xml"),
    ("cricket", "https://indianexpress.com/section/sports/cricket/feed/"),
    ("cricket", "https://sports.ndtv.com/cricket/rss"),
    ("cricket", "https://www.crictracker.com/feed/"),
    # General sports (may contain IPL content)
    ("sports",  "https://feeds.bbci.co.uk/sport/rss.xml"),
    ("sports",  "https://rss.cnn.com/rss/edition_sport.rss"),
    ("sports",  "https://www.thehindu.com/sport/cricket/feeder/default.rss"),
    # Fallback: general India news (IPL gets heavy coverage)
    ("india",   "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"),
    ("india",   "https://www.ndtv.com/rss/top-stories"),
]

# IPL-specific viral keywords (aggressive cricket context)
IPL_KEYWORDS = [
    # Match action
    "ipl", "t20", "six", "sixes", "wicket", "wickets", "century", "fifty",
    "hat-trick", "bowled", "caught", "run-out", "lbw", "no ball", "wide",
    "overthrow", "super over", "last ball", "final over", "chase", "target",
    "powerplay", "death over", "yorker", "bouncer", "slower ball", "googly",
    "doosra", "carrom ball", "reverse sweep", "scoop", "ramp", "helicopter",
    "upper cut", "slog", "miscue", "top edge", "caught behind", "stumped",
    "run rate", "required rate", "net run rate", "dls", "super 8",
    "first innings", "second innings", "batting first", "chasing",
    "powerplay wicket", "opening stand", "partnership", "50-run stand",
    "100-run stand", "last over", "last wicket", "tail", "tailender",
    # Teams
    "csk", "mi", "rcb", "kkr", "srh", "dc", "pbks", "rr", "gt", "lsg",
    "chennai", "mumbai", "bangalore", "bengaluru", "kolkata", "hyderabad",
    "delhi", "punjab", "rajasthan", "gujarat", "lucknow",
    "super kings", "indians", "challengers", "knight riders", "sunrisers",
    "capitals", "kings", "royals", "titans", "super giants",
    # Players — established stars
    "dhoni", "kohli", "rohit", "bumrah", "warner", "buttler", "rashid",
    "stokes", "maxwell", "pollard", "gayle", "de villiers", "hardik",
    "pandya", "jadeja", "chahal", "shami", "siraj", "suryakumar",
    "gill", "pant", "iyer", "samson", "smith", "williamson", "bairstow",
    "ABD", "SKY", "MSD", "hitman",
    # Players — emerging & current IPL 2026 stars
    "ruturaj", "gaikwad", "tilak varma", "yashasvi", "jaiswal",
    "rinku singh", "shubman", "klaasen", "stubbs", "brevis", "head",
    "travis head", "abhishek sharma", "nitish kumar", "riyan parag",
    "dhruv jurel", "anukul", "mayank yadav", "akash deep", "noor ahmad",
    "varun", "varun chakaravarthy", "mitchell starc", "pat cummins",
    "cameron green", "liam livingstone", "jonny bairstow", "nicholas pooran",
    "quinton de kock", "kl rahul", "krunal pandya", "axar patel",
    "washington sundar", "kuldeep yadav", "arshdeep", "avesh khan",
    "deepak chahar", "mohit sharma", "tushar deshpande", "shardul",
    "prabhsimran", "sai sudarshan", "pathirana",
    # Drama & virality
    "dropped", "injured", "injury", "sacked", "controversy", "fight",
    "row", "angry", "furious", "explosive", "stunning", "stunner",
    "record", "historic", "maiden", "fastest", "slowest", "most expensive",
    "brutal", "carnage", "demolish", "thrash", "collapse", "shocking",
    "heartbreak", "comeback", "upset", "clash", "rivalry", "dominate",
    "blitz", "blistering", "superb", "incredible", "unbelievable",
    "sensational", "outclassed", "hammered", "destroyed", "smashed",
    "tonked", "retired hurt", "concussion sub", "impact player",
    "auction", "retained", "released", "traded", "salary", "mega auction",
    "uncapped", "overseas", "squad", "playing xi", "team sheet",
]

GENERIC_VIRAL = [
    "win", "loss", "defeat", "champion", "championship", "trophy",
    "final", "semifinal", "qualifier", "eliminator", "playoffs",
    "qualified", "eliminated", "ban", "fine", "suspended", "suspension",
    "umpire", "drs", "review", "overturned", "rain", "duckworth",
    "pitch", "toss", "captain", "captaincy", "vice captain",
    "press conference", "interview", "celebration", "reaction",
    "highlight", "highlights", "viral", "trending", "breaking",
    "exclusive", "confirmed", "official", "announced", "signed",
    "hat trick", "milestone", "achievement", "stats", "numbers",
    "points table", "standings", "ranking", "form", "momentum",
    "stunning catch", "missed catch", "dropped catch", "direct hit",
    "brilliant", "masterclass", "clinical", "ruthless", "dominant",
]


def _clean_html(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(text).strip()


def _score_virality(title, summary=""):
    combined = (title + " " + summary).lower()
    return (
        sum(2 for kw in IPL_KEYWORDS if kw in combined) +
        sum(1 for kw in GENERIC_VIRAL if kw in combined)
    )


def _is_ipl_relevant(title, summary=""):
    """Return True if the article is cricket/IPL related."""
    combined = (title + " " + summary).lower()
    ipl_core = ["ipl", "t20", "cricket", "india", "match", "wicket", "run"]
    return any(kw in combined for kw in ipl_core)


# Rotate through realistic User-Agent strings to reduce 403s
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

def _fetch_full_article(url, timeout=12):
    """Fetch and extract plain text body from a news article URL.
    Returns empty string on 403/404/any error — caller falls back to RSS summary.
    """
    if not url:
        return ""
    ua = random.choice(_UA_POOL)
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://www.google.com/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if r.status_code in (403, 401, 429):
            # Site blocks bots — silently skip, use RSS summary instead
            return ""
        r.raise_for_status()
        # Strip scripts, styles, nav, footer, ads
        text = re.sub(r"(?i)<(script|style|nav|footer|header|aside|form|button|noscript)[^>]*>[\s\S]*?</\1>", "", r.text)
        # Remove all remaining HTML tags
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        # Collapse whitespace
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()
        # Return up to ~3000 chars to keep LLM context focused
        return text[:3000]
    except Exception as e:
        print(f"[Article Fetch] Failed for {url}: {e}")
        return ""


def fetch_news(max_articles=30):
    """Fetch articles from all RSS feeds, filter for IPL relevance, rank by virality."""
    articles = []
    for category, feed_url in RSS_FEEDS:
        try:
            feed   = feedparser.parse(feed_url)
            source = feed.feed.get("title", feed_url)
            for entry in feed.entries[:8]:
                title   = _clean_html(entry.get("title", ""))
                summary = _clean_html(entry.get("summary", entry.get("description", "")))
                if not title:
                    continue
                link = entry.get("link", "")
                full_content = _fetch_full_article(link)
                articles.append({
                    "title":        title,
                    "summary":      summary,
                    "full_content": full_content,
                    "link":         link,
                    "source":       source,
                    "category":     category,
                    "published":    entry.get("published", entry.get("updated", "")),
                    "score":        _score_virality(title, summary),
                })
        except Exception as e:
            print(f"[RSS] Error fetching {feed_url}: {e}")

    # Prefer IPL/cricket articles; keep others as fallback
    ipl_articles  = [a for a in articles if _is_ipl_relevant(a["title"], a["summary"])]
    other_articles = [a for a in articles if not _is_ipl_relevant(a["title"], a["summary"])]

    ipl_articles.sort(key=lambda x: x["score"], reverse=True)
    other_articles.sort(key=lambda x: x["score"], reverse=True)

    combined = ipl_articles + other_articles

    # Deduplicate by title prefix
    seen, unique = set(), []
    for a in combined:
        key = a["title"].lower()[:60]
        if key not in seen:
            seen.add(key)
            unique.append(a)

    return unique[:max_articles]


def pick_slot_article(articles, posted, slot_index, topic_focus):
    """
    Pick the single best fresh article for this time slot.
    Uses slot_index as a seed offset so different slots pick different articles
    even if the RSS feed hasn't refreshed, ensuring distinct content across the day.
    """
    fresh = [a for a in articles if _article_hash(a) not in posted]

    if not fresh:
        return None

    # Try to match topic_focus keywords first
    topic_keywords = {
        "match_preview":     ["preview", "today", "match", "playing xi", "squad", "pitch report"],
        "player_form":       ["century", "fifty", "form", "performance", "player", "batsman", "bowler"],
        "team_drama":        ["dropped", "replaced", "injured", "sacked", "row", "drama", "controversy"],
        "live_match_action": ["innings", "wickets", "runs", "score", "chase", "target", "powerplay"],
        "controversy":       ["controversy", "fight", "angry", "drs", "umpire", "fine", "ban", "appeal"],
        "standings_records": ["points table", "record", "standings", "qualify", "eliminated", "fastest"],
    }.get(topic_focus, [])

    # Try topic-matched fresh articles first (weighted by score)
    topic_matched = [a for a in fresh if any(kw in (a["title"] + " " + a["summary"]).lower() for kw in topic_keywords)]

    pool = topic_matched if topic_matched else fresh

    # Use slot_index as a pseudo-random offset within pool for variety
    if len(pool) > 1:
        # Sort by score descending, then use slot_index to pick from top-N
        pool_sorted = sorted(pool, key=lambda x: x["score"], reverse=True)
        top_n = min(len(pool_sorted), 5)
        idx = slot_index % top_n
        return pool_sorted[idx]

    return pool[0]


# ═══════════════════════════════════════════════════════════════════════
# SECTION 2 — GEMINI TEXT REWRITE
# ═══════════════════════════════════════════════════════════════════════

# Aggressive visual style themes — rotated by slot to keep variety
VISUAL_THEMES = [
    {
        "color_grade": "hyper-saturated fiery orange and blazing gold",
        "atmosphere":  "stadium at night, floodlights blazing, massive crowd roaring",
        "mood":        "electric, explosive, victory celebration",
    },
    {
        "color_grade": "blood red and deep black with neon highlights",
        "atmosphere":  "close-up of a furious bowler mid-delivery, sweat flying",
        "mood":        "intense, aggressive, battle-ready",
    },
    {
        "color_grade": "electric blue and white with dramatic shadows",
        "atmosphere":  "batsman smashing a six with a shattered stump effect",
        "mood":        "dominant, powerful, crowd going insane",
    },
    {
        "color_grade": "emerald green pitch under storm clouds with lightning",
        "atmosphere":  "dramatic team huddle, captain rallying players",
        "mood":        "tense, high-stakes, last-over thriller",
    },
    {
        "color_grade": "golden hour dust and chaos on the cricket pitch",
        "atmosphere":  "player celebrating with arms wide, jersey flying",
        "mood":        "triumphant, euphoric, historic moment",
    },
    {
        "color_grade": "cinematic dark purple and crimson with smoke effects",
        "atmosphere":  "controversy on the field — players surrounding umpire",
        "mood":        "heated, controversial, fans in uproar",
    },
]


def rewrite_news_content(article, slot_index=0, topic_focus="match_preview"):
    """
    Call Gemini 2.5 Pro to generate aggressive IPL caption + viral headline + image prompt.
    """
    if not OPENROUTER_API_KEY:
        print("[Gemini Text] OPENROUTER_API_KEY not set. Add it as a GitHub Actions secret named OPENROUTER_API_KEY.")
        return None
    if not OPENROUTER_API_KEY.startswith(("sk-", "eyJ")):
        print(f"[Gemini Text] OPENROUTER_API_KEY looks invalid (starts with: {OPENROUTER_API_KEY[:6]!r}). Check the secret value.")
        return None

    title        = article["title"].replace('"', '\\"').replace('\n', ' ')
    summary      = (article.get("summary", "") or "").replace('"', '\\"').replace('\n', ' ')
    full_content = (article.get("full_content", "") or "").replace('"', '\\"').replace('\n', ' ')
    source       = article.get("source", "Cricket News").replace('"', '\\"')
    theme        = VISUAL_THEMES[slot_index % len(VISUAL_THEMES)]

    # Build the full news context block sent to the LLM
    news_context = f"Title: {title}\nSummary: {summary}"
    if full_content:
        news_context += f"\nFull Article Content:\n{full_content}"
    news_context += f"\nSource: {source}\nTopic focus for this post: {topic_focus}"

    prompt = f"""You are the most AGGRESSIVE, VIRAL cricket news writer on Instagram for the brand "IPL Tadka" (@ipl_tadka).

Given this IPL/cricket news, produce a JSON response with exactly these 6 keys:

1. "rewritten_summary": Rewrite the news in 3-5 sentences. Use SIMPLE, DIRECT English. Be AGGRESSIVE and PUNCHY. Use fire emojis, cricket emojis. Make fans feel HYPE. Every sentence must land like a punch.

2. "viral_headline": ONE ultra-short headline (MAX 10 WORDS). Rules:
   - ALL CAPS
   - Use words like: DESTROYED, FIRED, STUNNED, EXPOSED, CARNAGE, BEAST MODE, BRUTAL, GONE, SCREAMING, EXPLODED, DEMOLISHED, LEGEND
   - Must be about the actual story
   - Must feel like a cricket commentator screaming at 150 decibels
   - NO clickbait — must reflect the real story
   - Example style: "DHONI DESTROYS 30 BALLS — CROWD GOES INSANE" or "KOHLI DROPS BOMBSHELL — RCB IN CHAOS"

3. "caption": Full Instagram caption (max 2200 chars):
   - Start with 3 fire/cricket emojis and "IPL TADKA BREAKING 🔥"
   - Explosive opening line in ALL CAPS
   - 3-4 lines of punchy cricket commentary with emojis
   - Call-to-action: "DROP a 🔥 if you're hyped! Tag your cricket squad below!"
   - Credit: "📡 Source: {source}"
   - End with these exact hashtags: {BRAND_HASHTAGS}

4. "image_prompt": A detailed prompt for AI image generation. Start with: "Generate and return an image."
   Then describe:

   MAIN SCENE: Ultra-photorealistic, hyper-detailed cricket photograph capturing the most EXPLOSIVE, DRAMATIC moment related to this story: "{summary}". The scene MUST feature the SPECIFIC PLAYERS mentioned in this article — use their actual real-world face likeness, skin tone, hair, and physical features exactly as they appear in real life. If multiple players are mentioned, show AT LEAST TWO of them from DIFFERENT IPL teams, one from each side, clearly identifiable by their distinct team jerseys/kits. Both players must be the focal point of the composition. CRITICAL: The two players must NOT be facing each other or looking at each other — each player is independently showing raw aggression. The scene must be CINEMATIC and AGGRESSIVE. Use {theme['atmosphere']}. Shot at f/1.4 with a 200mm telephoto lens — BOTH players' faces MUST be hyper-realistic, lifelike, every skin pore, sweat drop, muscle tension, and expression visible at 8K resolution. Eyes sharp and intense. Expressions match the emotion of the story — one dominant, one under pressure. Contrasting team jersey colors must be vivid and accurate to real IPL kits. Do NOT use cartoon or illustration — photorealistic press photography only. Lens flare from stadium floodlights. Motion blur on crowd background. {theme['color_grade']} color grading. Deep cinematic vignette on all four edges. The final image MUST look like a real AFP/Reuters/BCCI press photo with two rival players — zero AI artifacts.

   BOTTOM BAR (pixels 810–1080, full width): Solid black overlay (90% opacity). A bold 5px bright orange horizontal line runs the full width at the very top of this bar. Inside the bar: viral headline in LARGE BOLD white Impact/Oswald font (62pt), ALL CAPS, word-wrapped to 2 lines max, left-aligned with 44px margin. Below headline: "📡 {source.upper()}" in light gray condensed font (28pt).

   TOP-LEFT CORNER (0,0 anchored): Bold rounded-rectangle badge, fiery orange gradient fill (#FF6B00 to #FF0000), corner radius 10px, 14px 28px padding, 2px white inner border, strong drop shadow. Badge text: "⚡ IPL TADKA BREAKING" in white bold uppercase condensed font (30pt).

   TOP-RIGHT CORNER (top-right anchored, 18px margins): Brand watermark "{BRAND_HANDLE}" in white bold font (28pt), black 3px outline, drop shadow. Must be pinned flush to top-right corner.

   OVERALL: {theme['mood']} aesthetic. 8K photorealistic. Professional sports broadcast look. Zero compression artifacts. Looks like a real live-TV sports news screenshot. Instagram-ready square (1080x1080).

   IMPORTANT: Generate and return the actual image. All text and overlays must be burned into the image at the exact positions. Face of any player must be hyper-realistic and identifiable.

5. "teams": JSON array of EXACTLY 2 IPL team codes. Choose ONLY from: ["CSK", "MI", "RCB", "KKR", "SRH", "DC", "PBKS", "RR", "GT", "LSG"].
   - First, identify any teams directly mentioned in the article.
   - If only 1 team is found, infer the second from player names (e.g. Rohit/Bumrah/Hardik → MI, Kohli/Jadeja → RCB, Dhoni/Ruturaj → CSK, etc.).
   - If no teams are identifiable at all, pick the 2 most likely teams based on the story context.
   - ALWAYS return exactly 2 codes. Never return [] or a single-item array.

6. "players": JSON array of up to 4 player full names explicitly mentioned or strongly implied in the article (e.g. ["Rohit Sharma", "Virat Kohli"]). These are the players whose faces must appear in the generated image. Return [] only if no specific players are mentioned.

News article:
{news_context}

Respond with ONLY valid JSON. No markdown fences. No extra text."""

    try:
        resp = requests.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type":  "application/json",
                "HTTP-Referer":  "https://github.com/ipl-tadka-bot",
                "X-Title":       "IPL Tadka Bot",
            },
            json={
                "model":       TEXT_MODEL,
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": 0.85,
            },
            timeout=90,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)
        print("[Gemini Text] Content rewritten successfully.")
        viral_headline = data.get("viral_headline", title.upper())
        players        = data.get("players", [])
        teams          = data.get("teams", [])

        raw_img_prompt = data.get("image_prompt", "")
        if raw_img_prompt:
            raw_img_prompt = raw_img_prompt.replace("viral_headline", viral_headline)
            # Inject player names explicitly into image prompt so the model knows who to draw
            if players:
                player_str = " and ".join(players[:4])
                raw_img_prompt = raw_img_prompt.replace(
                    "MAIN SCENE:",
                    f"PLAYERS TO DEPICT (use their real face likeness): {player_str}.\n\n   MAIN SCENE:",
                    1,
                )
        image_prompt = raw_img_prompt if raw_img_prompt else _default_image_prompt(article, viral_headline, slot_index)

        if players:
            print(f"[Gemini Text] Players identified: {', '.join(players)}")
        if teams:
            print(f"[Gemini Text] Teams identified: {', '.join(teams)}")

        return {
            "rewritten_summary": data.get("rewritten_summary", summary),
            "viral_headline":    viral_headline,
            "caption":           data.get("caption", ""),
            "image_prompt":      image_prompt,
            "teams":             teams,
            "players":           players,
        }
    except Exception as e:
        print(f"[Gemini Text] API call failed: {e}")
        return None


def _default_image_prompt(article, viral_headline, slot_index=0):
    title   = article["title"]
    summary = article.get("summary", title)
    source  = article.get("source", "Cricket News")
    theme   = VISUAL_THEMES[slot_index % len(VISUAL_THEMES)]

    return (
        f"Generate and return an image. Create a viral IPL Tadka Instagram post (1080x1080px square).\n\n"
        f"MAIN SCENE: Ultra-photorealistic cricket action photograph. Story context: {summary}. "
        f"MANDATORY: Include AT LEAST TWO players from DIFFERENT IPL teams in the same frame — "
        f"identifiable by their contrasting team jerseys/kits (e.g., RCB red vs SRH orange, CSK yellow vs MI blue). "
        f"Compose the shot so both players are the focal subjects — CRITICAL: the two players must NOT face each other or look at each other. "
        f"Each player independently shows raw aggression: one roaring at the crowd/sky, the other pumping a fist or staring at the camera — both in the same frame but each in their own intense solo moment. "
        f"Both faces hyper-realistic at 8K — skin pores, sweat, muscle tension, intense eyes all visible. "
        f"{theme['atmosphere']}. Shot with 200mm telephoto at f/1.4. {theme['color_grade']} color grading. "
        f"Cinematic dark vignette. Stadium floodlights with dramatic lens flare. "
        f"Motion blur on crowd. Looks like AFP/Reuters press photo. 8K resolution. Zero AI artifacts.\n\n"
        f"BOTTOM BAR (rows 810-1080, full width): Black overlay 90% opacity. "
        f"5px bold orange line at very top of bar. "
        f"White bold Impact font viral headline: \"{viral_headline}\" (62pt, ALL CAPS, 2 lines). "
        f"Below: \"📡 {source.upper()}\" gray 28pt.\n\n"
        f"TOP-LEFT (0,0 flush): Orange gradient rounded badge (corner radius 10px, 2px white border). "
        f"Text: \"⚡ IPL TADKA BREAKING\" white bold 30pt.\n\n"
        f"TOP-RIGHT (flush, 18px margin): \"{BRAND_HANDLE}\" white bold 28pt, 3px black outline.\n\n"
        f"STYLE: {theme['mood']}. Sports broadcast aesthetic. Instagram-ready. "
        f"IMPORTANT: Return the actual image. All overlays burned in at exact positions."
    )


def _load_team_images(team_codes):
    """Load team player-face images as base64 dicts for multimodal API requests."""
    images = []
    for code in team_codes:
        for ext in (".PNG", ".jpg", ".jpeg", ".png"):
            path = os.path.join(TEAMS_DIR, f"{code}{ext}")
            if os.path.exists(path):
                mime = "image/jpeg" if ext.lower() in (".jpg", ".jpeg") else "image/png"
                with open(path, "rb") as fh:
                    b64 = base64.b64encode(fh.read()).decode("utf-8")
                images.append({"team": code, "mime": mime, "data": b64})
                print(f"[Team Images] Loaded {code} player faces from {os.path.basename(path)}")
                break
        else:
            print(f"[Team Images] No image found for team: {code}")
    return images


# ═══════════════════════════════════════════════════════════════════════
# SECTION 3 — GEMINI IMAGE GENERATION
# ═══════════════════════════════════════════════════════════════════════

def generate_image_with_gemini(image_prompt, team_images=None):
    if not OPENROUTER_API_KEY:
        print("[Gemini Image] OPENROUTER_API_KEY not set.")
        return None

    print("[Gemini Image] Generating aggressive IPL image...")
    try:
        # Build multimodal content: team face reference images + text prompt
        if team_images:
            team_labels = " and ".join(ti["team"] for ti in team_images)
            content_parts = [
                {
                    "type": "text",
                    "text": (
                        f"MANDATORY FACE REFERENCE INSTRUCTIONS — READ BEFORE GENERATING:\n"
                        f"The images below are the OFFICIAL current squad face references for {team_labels}. "
                        f"These are the ONLY faces you are allowed to use for players from these teams. "
                        f"You MUST copy the exact face, skin tone, hair, jawline, and features from these reference photos. "
                        f"DO NOT use your training data face of any player — only the faces shown in these reference images. "
                        f"If a player's face in your training data differs from the reference image, the reference image ALWAYS wins.\n"
                    ),
                }
            ]
            for ti in team_images:
                content_parts.append({
                    "type": "text",
                    "text": (
                        f"▼ REFERENCE IMAGE FOR {ti['team']} PLAYERS — "
                        f"Use ONLY these faces for any {ti['team']} player that appears in the generated image. "
                        f"Match each player's face exactly as shown below:"
                    ),
                })
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{ti['mime']};base64,{ti['data']}"},
                })
            # Append a reinforcement line then the main image prompt
            reinforcement = (
                f"\n\nCRITICAL REMINDER: The faces shown in the reference images above are the ground truth. "
                f"Reproduce them pixel-perfectly in the generated image. "
                f"Do not substitute any player face with a face from your training memory.\n\n"
            )
            content_parts.append({"type": "text", "text": reinforcement + image_prompt})
            message_content = content_parts
        else:
            message_content = image_prompt

        resp = requests.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type":  "application/json",
                "HTTP-Referer":  "https://github.com/ipl-tadka-bot",
                "X-Title":       "IPL Tadka Bot",
            },
            json={
                "model":    IMAGE_MODEL,
                "messages": [{"role": "user", "content": message_content}],
            },
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()

        def _extract_image_url(msg):
            images = msg.get("images")
            if images and isinstance(images, list) and images[0]:
                url = (images[0].get("image_url") or {}).get("url", "")
                if url:
                    return url
            content = msg.get("content", "")
            if isinstance(content, str) and "data:image" in content:
                return content
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "image_url":
                        url = (part.get("image_url") or {}).get("url", "")
                        if url:
                            return url
                    if part.get("type") == "image" or "inline_data" in part:
                        inline = part.get("inline_data", part)
                        b64    = inline.get("data", "")
                        mime   = inline.get("mime_type", "image/png")
                        if b64:
                            return f"data:{mime};base64,{b64}"
            return None

        def _url_to_image(url):
            if url.startswith("data:image"):
                b64 = url.split(",", 1)[1]
                raw = base64.b64decode(b64)
            elif url.startswith("http"):
                r = requests.get(url, timeout=30)
                r.raise_for_status()
                raw = r.content
            else:
                return None
            img = Image.open(BytesIO(raw)).convert("RGB")
            img = img.resize((IMG_WIDTH, IMG_HEIGHT), Image.LANCZOS)
            return img

        for choice in data.get("choices", []):
            msg = choice.get("message", {})
            image_url = _extract_image_url(msg)
            if image_url:
                img = _url_to_image(image_url)
                if img:
                    print("[Gemini Image] Image generated successfully.")
                    return img

        print("[Gemini Image] No image found in response.")
        if data.get("choices"):
            msg = data["choices"][0].get("message", {})
            print(f"[Gemini Image] Message keys: {list(msg.keys())}")
            content = msg.get("content", "")
            print(f"[Gemini Image] Content preview: {str(content)[:300]}")
        return None

    except Exception as e:
        print(f"[Gemini Image] Generation failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════
# SECTION 4 — SAVE IMAGE
# ═══════════════════════════════════════════════════════════════════════

def build_final_image(article, gemini_image):
    if gemini_image is None:
        print("[Image] Gemini image generation failed. No fallback.")
        return None

    safe     = "".join(c if c.isalnum() else "_" for c in article["title"][:30])
    out_path = os.path.join(OUTPUT_DIR, f"ipl_post_{safe}.jpg")
    gemini_image.convert("RGB").save(out_path, "JPEG", quality=95)
    print(f"[Image] Saved: {out_path}")
    return out_path


# ═══════════════════════════════════════════════════════════════════════
# SECTION 5 — MUSIC + VIDEO
# ═══════════════════════════════════════════════════════════════════════

def _get_music_track():
    """Pick a random audio file from assets/music/ to keep every reel unique."""
    supported = {".mp3", ".wav", ".ogg", ".m4a", ".aac"}
    tracks    = [str(p) for p in Path(MUSIC_DIR).iterdir() if p.suffix.lower() in supported]
    if tracks:
        track = random.choice(tracks)
        print(f"[Music] Selected: {os.path.basename(track)}")
        return track
    print("[Music] No tracks in assets/music/ — creating silent video.")
    return None


def create_reel(image_path, duration=REEL_DURATION):
    if not MOVIEPY_AVAILABLE:
        print("[Reel] moviepy not installed. Posting as image.")
        return image_path

    base  = os.path.splitext(os.path.basename(image_path))[0]
    out   = os.path.join(OUTPUT_DIR, f"{base}.mp4")
    music = _get_music_track()

    try:
        clip = ImageClip(image_path, duration=duration).set_fps(30)

        if music:
            audio = AudioFileClip(music)
            if audio.duration < duration:
                loops = int(duration / audio.duration) + 1
                audio = concatenate_audioclips([audio] * loops)
            # Trim and normalize volume
            audio = audio.subclip(0, duration)
            # Slightly duck the music so it feels like background hype
            audio = audio.fl(lambda gf, t: gf(t) * 0.75, keep_duration=True)
            clip  = clip.set_audio(audio)

        clip.write_videofile(
            out,
            codec="libx264",
            audio_codec="aac",
            fps=30,
            bitrate="4000k",      # High quality for Instagram
            logger=None,
        )
        print(f"[Reel] Video saved: {out}")
        return out
    except Exception as e:
        print(f"[Reel] Video creation failed: {e}. Falling back to image.")
        return image_path


# ═══════════════════════════════════════════════════════════════════════
# SECTION 6 — ANTI-DETECTION HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _human_delay(min_s=8, max_s=20):
    """Random realistic delay to mimic human pacing between actions."""
    t = random.uniform(min_s, max_s)
    print(f"[Human Delay] Waiting {t:.1f}s...")
    time.sleep(t)


def _add_invisible_variation(caption: str) -> str:
    """
    Insert invisible Unicode variation selectors (U+FE0F) into the caption.
    These are visually invisible but make each post's text hash unique,
    reducing the chance Instagram's duplicate-content detector flags the account.
    """
    variation_char = "\uFE0F"
    words = caption.split(" ")
    # Add variation selector after ~15% of words randomly
    result = []
    for word in words:
        result.append(word)
        if random.random() < 0.15:
            result.append(variation_char)
    return " ".join(result)


def _randomize_caption_spacing(caption: str) -> str:
    """Add random newlines / spaces between paragraphs for uniqueness."""
    lines = caption.split("\n")
    out   = []
    for line in lines:
        out.append(line)
        # Randomly insert 1 or 2 blank lines after each paragraph
        if line.strip() == "":
            out.append("")  # double blank
    return "\n".join(out)


def _apply_anti_detection(caption: str) -> str:
    """Apply all anti-detection mutations to caption."""
    caption = _add_invisible_variation(caption)
    caption = _randomize_caption_spacing(caption)
    return caption


# ═══════════════════════════════════════════════════════════════════════
# SECTION 7 — INSTAGRAM POSTING
# ═══════════════════════════════════════════════════════════════════════

def _instagrapi_post(media_path, caption):
    if not INSTAGRAPI_AVAILABLE:
        raise ImportError("instagrapi not installed.")

    username = os.environ.get("INSTAGRAM_USERNAME", "")
    password = os.environ.get("INSTAGRAM_PASSWORD", "")

    cl = InstaClient()
    # Random delay_range simulates human-like posting rhythm
    cl.delay_range = [random.randint(4, 8), random.randint(12, 22)]

    # Restore session from base64-encoded env var (for GitHub Actions)
    session_b64 = os.environ.get("INSTAGRAM_SESSION_B64", "")
    if not os.path.exists(SESSION_FILE) and session_b64:
        try:
            session_data = base64.b64decode(session_b64).decode("utf-8")
            with open(SESSION_FILE, "w") as f:
                f.write(session_data)
            print("[Instagram] Session restored from INSTAGRAM_SESSION_B64.")
        except Exception as e:
            print(f"[Instagram] Failed to decode session: {e}")

    if os.path.exists(SESSION_FILE):
        try:
            cl.load_settings(SESSION_FILE)
            # If we have credentials, revalidate session with them.
            # If not, use session cookie directly (no re-login needed).
            if username and password:
                cl.login(username, password)
            else:
                # Session-only mode: just verify the loaded settings are alive
                # by making a lightweight API call.
                cl.get_timeline_feed()
            cl.dump_settings(SESSION_FILE)
            print("[Instagram] Logged in via saved session.")
        except Exception as session_err:
            print(f"[Instagram] Session login failed ({session_err}).")
            if username and password:
                print("[Instagram] Falling back to fresh credential login...")
                cl = InstaClient()
                cl.delay_range = [random.randint(4, 8), random.randint(12, 22)]
                _human_delay(5, 12)
                cl.login(username, password)
                cl.dump_settings(SESSION_FILE)
                print("[Instagram] Fresh login successful.")
            else:
                raise RuntimeError(
                    "Session is invalid/expired and no credentials provided to re-login. "
                    "Run login_helper.py to generate a fresh session.json."
                )
    elif username and password:
        print("[Instagram] No session file. Performing fresh login...")
        _human_delay(3, 8)
        cl.login(username, password)
        cl.dump_settings(SESSION_FILE)
        print("[Instagram] Login successful. Session saved.")
    else:
        raise ValueError(
            "No session.json found and no credentials set. "
            "Run login_helper.py to generate assets/session.json first."
        )

    # Human-like delay before posting
    _human_delay(8, 18)

    # Extra metadata to look organic
    extra_data = {
        "custom_accessibility_caption": "",
        "like_and_view_counts_disabled": 0,
        "disable_comments": 0,
    }

    is_video = str(media_path).lower().endswith((".mp4", ".mov"))
    if is_video:
        media = cl.clip_upload(str(media_path), caption=caption, extra_data=extra_data)
        print(f"[Instagram] Reel posted. ID: {media.pk}")
    else:
        media = cl.photo_upload(str(media_path), caption=caption, extra_data=extra_data)
        print(f"[Instagram] Photo posted. ID: {media.pk}")

    return media.pk


def _upload_to_imgur(file_path):
    cid = os.environ.get("IMGUR_CLIENT_ID", "")
    if not cid:
        return None
    try:
        with open(file_path, "rb") as f:
            r = requests.post(
                "https://api.imgur.com/3/image",
                headers={"Authorization": f"Client-ID {cid}"},
                files={"image": f.read()},
                timeout=30,
            )
            r.raise_for_status()
            return r.json()["data"]["link"]
    except Exception as e:
        print(f"[Imgur] Upload failed: {e}")
        return None


def _graph_api_post(media_path, caption):
    token      = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    account_id = os.environ.get("INSTAGRAM_ACCOUNT_ID", "")
    if not token or not account_id:
        raise ValueError("INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_ACCOUNT_ID not set.")

    media_url = _upload_to_imgur(media_path)
    if not media_url:
        raise RuntimeError("Could not get public media URL. Set IMGUR_CLIENT_ID.")

    is_video = str(media_path).lower().endswith((".mp4", ".mov"))
    base     = f"https://graph.facebook.com/v19.0/{account_id}"
    payload  = {"caption": caption, "access_token": token}

    if is_video:
        payload.update({"media_type": "REELS", "video_url": media_url})
    else:
        payload["image_url"] = media_url

    r = requests.post(f"{base}/media", data=payload, timeout=30)
    r.raise_for_status()
    container_id = r.json().get("id")

    if is_video:
        for _ in range(18):
            time.sleep(10)
            s = requests.get(
                f"https://graph.facebook.com/v19.0/{container_id}",
                params={"fields": "status_code", "access_token": token},
                timeout=15,
            ).json().get("status_code", "")
            if s == "FINISHED":
                break
            if s == "ERROR":
                raise RuntimeError("Video processing failed via Graph API.")

    pub = requests.post(
        f"{base}/media_publish",
        data={"creation_id": container_id, "access_token": token},
        timeout=30,
    )
    pub.raise_for_status()
    media_id = pub.json().get("id")
    print(f"[Instagram] Posted via Graph API. ID: {media_id}")
    return media_id


def post_to_instagram(media_path, caption):
    """Try instagrapi first, fall back to Graph API."""
    # Use instagrapi if available AND we have either a session file or credentials
    has_session = os.path.exists(SESSION_FILE) or os.environ.get("INSTAGRAM_SESSION_B64")
    has_creds   = os.environ.get("INSTAGRAM_USERNAME") and os.environ.get("INSTAGRAM_PASSWORD")
    if INSTAGRAPI_AVAILABLE and (has_session or has_creds):
        try:
            return _instagrapi_post(media_path, caption)
        except Exception as e:
            print(f"[Instagram] instagrapi failed: {e}")

    if os.environ.get("INSTAGRAM_ACCESS_TOKEN"):
        try:
            return _graph_api_post(media_path, caption)
        except Exception as e:
            print(f"[Instagram] Graph API failed: {e}")

    print("[Instagram] No credentials set. DRY RUN output:")
    print(f"  Media: {media_path}")
    print(f"  Caption preview:\n{caption[:400]}...")
    return None


# ═══════════════════════════════════════════════════════════════════════
# SECTION 8 — POSTED LOG
# ═══════════════════════════════════════════════════════════════════════

def _load_log():
    if os.path.exists(POSTED_LOG):
        try:
            with open(POSTED_LOG) as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def _save_log(posted):
    with open(POSTED_LOG, "w") as f:
        json.dump(list(posted)[-500:], f)


def _article_hash(article):
    key = article.get("link", "") or article.get("title", "")
    return hashlib.md5(key.encode()).hexdigest()


# ═══════════════════════════════════════════════════════════════════════
# SECTION 9 — MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════════

def process_article(article, slot_index, topic_focus, dry_run=False):
    """Full pipeline: rewrite → image → reel → post."""
    title = article["title"]
    print(f"\n{'='*65}")
    print(f"  IPL TADKA — {topic_focus.upper().replace('_', ' ')}")
    print(f"  Article : {title[:65]}")
    print(f"  Score   : {article.get('score', 0)}  |  Source: {article.get('source', '?')}")
    print(f"{'='*65}")

    # Step 1: Rewrite + generate prompts
    print(f"\n[1/5] Rewriting with Gemini 2.5 Pro (slot {slot_index}, topic: {topic_focus})...")
    content = rewrite_news_content(article, slot_index, topic_focus)
    if content is None:
        print("[Pipeline] Content rewriting failed. Skipping.")
        return None

    # Step 2: Generate image (with team face references if teams detected)
    print("\n[2/5] Generating explosive IPL image with Gemini...")
    team_codes  = content.get("teams", [])
    team_images = _load_team_images(team_codes) if team_codes else []
    if team_codes:
        print(f"[Pipeline] Detected teams: {', '.join(team_codes)} — injecting player face references")
    gemini_img = generate_image_with_gemini(content["image_prompt"], team_images=team_images or None)

    # Step 3: Save image
    print("\n[3/5] Saving image...")
    image_path = build_final_image(article, gemini_img)
    if image_path is None:
        print("[Pipeline] Image save failed. Skipping.")
        return None

    # Step 4: Create reel with random music
    print("\n[4/5] Creating reel with random music track...")
    media_path = create_reel(image_path)

    # Step 5: Post
    caption = content.get("caption", "")
    if not caption:
        print("[Pipeline] No caption generated. Skipping.")
        return None

    # Apply anti-detection mutations
    caption = _apply_anti_detection(caption)

    print(f"\n[5/5] {'DRY RUN — skipping post' if dry_run else 'Posting to Instagram...'}")
    print(f"Caption preview:\n{caption[:350]}\n...")

    if dry_run:
        print(f"[DRY RUN] Media: {media_path}")
        return "DRY_RUN"

    return post_to_instagram(media_path, caption)


def run(dry_run=False, force_slot=None):
    ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    print(f"\n{'#'*65}")
    print(f"  IPL TADKA AUTO-POSTER  —  {ts}")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE POST'}")
    print(f"{'#'*65}\n")

    # Determine time slot
    if force_slot is not None and force_slot in SLOT_MAP:
        slot_index, topic_focus = SLOT_MAP[force_slot]
    else:
        slot_index, topic_focus = get_current_slot()

    print(f"[Slot] Index={slot_index}, Topic={topic_focus}")

    print("[Bot] Fetching IPL news from RSS feeds...")
    articles = fetch_news(max_articles=40)
    if not articles:
        print("[Bot] No articles fetched. Exiting.")
        return

    print(f"[Bot] {len(articles)} articles fetched. Top stories:")
    for i, a in enumerate(articles[:8]):
        print(f"  [{i}] score={a['score']:2d}  {a['title'][:70]}")

    posted  = _load_log()
    article = pick_slot_article(articles, posted, slot_index, topic_focus)

    if article is None:
        print("[Bot] No fresh articles for this slot. All already posted today.")
        return

    print(f"\n[Bot] Selected: {article['title'][:72]}")

    media_id = process_article(article, slot_index, topic_focus, dry_run=dry_run)

    if media_id:
        posted.add(_article_hash(article))
        if not dry_run:
            _save_log(posted)
        print(f"\n[Bot] ✓ Posted successfully! Media ID: {media_id}")
    else:
        print("\n[Bot] ✗ Posting failed.")


# ═══════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="IPL Tadka Instagram Auto-Poster")
    parser.add_argument("--dry-run",    action="store_true", help="Skip actual Instagram posting")
    parser.add_argument("--slot-hour",  type=int, default=None, choices=[6, 10, 19, 20, 21, 22, 23],
                        help="Force a specific slot hour (6/10/19/20/21/22/23)")
    args = parser.parse_args()

    run(dry_run=args.dry_run or DRY_RUN, force_slot=args.slot_hour)
