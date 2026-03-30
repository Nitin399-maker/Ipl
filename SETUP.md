# IPL Tadka — Instagram Auto-Poster Setup Guide

Posts one explosive, aggressive IPL news reel every 2 hours from **6 AM to 6 PM IST** via GitHub Actions.
Each post uses a different topic focus, a random music track, and unique visual theming so no two posts ever feel the same.

---

## Directory Structure

```
ipl/
├── main.py                          # Main bot code
├── requirements.txt
├── SETUP.md                         # This file
├── assets/
│   ├── posted_log.json              # Tracks what has been posted (auto-created)
│   ├── session.json                 # Instagram session (auto-created on first login)
│   ├── fonts/                       # Drop custom .ttf fonts here (optional)
│   └── music/                       # Drop .mp3/.wav/.m4a files here (random pick per post)
├── output/                          # Generated images/reels (auto-created)
└── .github/
    └── workflows/
        └── ipl_poster.yml           # GitHub Actions schedule
```

---

## 1. Add Music Files

Drop any number of `.mp3` / `.wav` / `.m4a` files into `ipl/assets/music/`.
The bot picks **one at random** for each post — so the more you add, the more variety.

### For GitHub Actions (music in secrets)
Since binary files can't be stored as secrets directly, base64-encode each track:

**Linux/Mac:**
```bash
base64 -w0 your_hype_track.mp3
```

**Windows (PowerShell):**
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("your_hype_track.mp3"))
```

Copy the output and add it as a GitHub Secret named `MUSIC_01`, `MUSIC_02`, etc. (up to `MUSIC_05`).

---

## 2. GitHub Secrets — Required

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret Name               | Value                                                  |
|---------------------------|--------------------------------------------------------|
| `OPENROUTER_API_KEY`      | Your OpenRouter API key                                |
| `INSTAGRAM_USERNAME`      | Instagram username (no @)                              |
| `INSTAGRAM_PASSWORD`      | Instagram password                                     |
| `INSTAGRAM_SESSION_B64`   | Base64 of `assets/session.json` (after first login)    |

### Optional (Graph API fallback)

| Secret Name               | Value                                                  |
|---------------------------|--------------------------------------------------------|
| `INSTAGRAM_ACCESS_TOKEN`  | Facebook/Instagram Graph API token                     |
| `INSTAGRAM_ACCOUNT_ID`    | Instagram Business Account ID                          |
| `IMGUR_CLIENT_ID`         | Imgur app Client-ID (needed for Graph API media upload)|

### Music tracks

| Secret Name  | Value                        |
|--------------|------------------------------|
| `MUSIC_01`   | Base64-encoded MP3 file      |
| `MUSIC_02`   | Base64-encoded MP3 file      |
| `MUSIC_03`   | Base64-encoded MP3 file      |
| `MUSIC_04`   | Base64-encoded MP3 file      |
| `MUSIC_05`   | Base64-encoded MP3 file      |

---

## 3. First Login — Generate Session File

Run this **once locally** to generate the session file (avoids 2FA prompts in CI):

```bash
cd ipl
pip install -r requirements.txt
python - <<'EOF'
from instagrapi import Client
import json, base64

cl = Client()
cl.login("YOUR_USERNAME", "YOUR_PASSWORD")
cl.dump_settings("assets/session.json")
print("Session saved to assets/session.json")

# Print base64 to add as INSTAGRAM_SESSION_B64 secret
with open("assets/session.json") as f:
    b64 = base64.b64encode(f.read().encode()).decode()
print("\nCopy this as INSTAGRAM_SESSION_B64 secret:\n")
print(b64)
EOF
```

---

## 4. Post Schedule (IST)

| Time (IST) | Topic Focus             | Visual Theme                          |
|------------|-------------------------|---------------------------------------|
| 06:00      | Match Preview           | Fiery orange & gold stadium           |
| 08:00      | Player in Form          | Blood red & black, bowler close-up    |
| 10:00      | Team Drama / Selection  | Electric blue, batsman smashing six   |
| 12:00      | Live Match Action       | Emerald green, last-over thriller     |
| 14:00      | Controversy             | Cinematic purple, heated field drama  |
| 16:00      | Standings & Records     | Golden dust, record celebration       |

Each slot uses a different visual color grade and atmosphere prompt, so posts look completely different throughout the day.

---

## 5. Run Locally

```bash
cd ipl
pip install -r requirements.txt

# Dry run (no actual post)
python main.py --dry-run

# Live post at current time slot
python main.py

# Force a specific time slot
python main.py --slot-hour 10

# Force a slot with dry run
python main.py --slot-hour 14 --dry-run
```

---

## 6. Manual GitHub Actions Trigger

1. Go to your repo → **Actions** → **IPL Tadka — Auto Post**
2. Click **Run workflow**
3. Set `dry_run = true` to test without posting
4. Optionally set `slot_hour` to test a specific slot

---

## 7. Anti-Detection Measures

The bot includes the following measures to avoid Instagram identifying it as automated:

- **Random delays** (8–20 seconds) between login and posting
- **Random delay_range** on the instagrapi client (simulates human typing speed)
- **Invisible Unicode variation selectors** inserted into captions (makes every post's text hash unique)
- **Session reuse** via `session.json` (avoids repeated logins that trigger bot checks)
- **Fresh session fallback** on session expiry with randomized re-login delay
- **High-quality video** (4000k bitrate, 30fps) that looks like organic content

---

## 8. Viral Optimization (Built-in)

Every post is designed to maximize reach:

- **Aggressive ALL-CAPS headline** on the image (max 10 words, power-word driven)
- **IPL Tadka Breaking badge** with orange gradient (eye-catching TV chyron style)
- **Hyper-realistic player faces** via Gemini image prompt (photorealistic, not cartoon)
- **15 trending IPL hashtags** per caption
- **Call-to-action** in every caption ("Tag your cricket squad!")
- **Cinematic color grading** (teal-orange, blood-red, electric blue — rotated per slot)
- **Diverse topics across the day** so followers see fresh angles, not repetitive content
- **Random music per reel** for audio variety

---

## 9. Pushing to GitHub

The `.github/workflows/` folder must be **inside your actual GitHub repository root**, not inside the `ipl/` subfolder.

If your repo root is the `Autoinsta` folder, the workflow path is already correct:
```
Autoinsta/.github/workflows/ipl_poster.yml
```

If you created a separate repo for the IPL bot, copy the `.github/` folder to that repo's root.
