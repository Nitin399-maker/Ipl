"""
Run this script ONCE to create assets/session.json.
It handles all Instagram security challenges interactively.

Usage:
  python login_helper.py
"""
import sys
import time
import json
import base64
from pathlib import Path

try:
    from instagrapi import Client
    from instagrapi.exceptions import (
        ChallengeRequired,
        TwoFactorRequired,
        BadPassword,
        LoginRequired,
        ReloginAttemptExceeded,
    )
except ImportError:
    print("instagrapi not installed.  Run:  pip install instagrapi --upgrade")
    sys.exit(1)

ASSETS_DIR   = Path(__file__).parent / "assets"
SESSION_FILE = ASSETS_DIR / "session.json"
ASSETS_DIR.mkdir(exist_ok=True)

# ── challenge handlers (called automatically by instagrapi) ──────────────────

def challenge_code_handler(username: str, choice) -> str:
    """Called by instagrapi when it needs a verification code from SMS/email."""
    print(f"\n[Challenge] Instagram sent a verification code to: {choice}")
    code = input("[Challenge] Enter the code you received: ").strip()
    return code


def change_password_handler(username: str) -> str:
    """Called by instagrapi if Instagram forces a password change."""
    print("\n[Warning] Instagram is asking you to change your password.")
    new_pw = input("Enter a NEW password for your Instagram account: ").strip()
    return new_pw


def build_client() -> Client:
    cl = Client()
    cl.delay_range               = [3, 8]
    cl.challenge_code_handler    = challenge_code_handler
    cl.change_password_handler   = change_password_handler
    return cl


def dump_and_print_b64(cl: Client):
    cl.dump_settings(str(SESSION_FILE))
    print(f"\n✓ Session saved → {SESSION_FILE}")
    with open(SESSION_FILE, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    print("\n" + "="*60)
    print("Copy the line below as GitHub Secret  INSTAGRAM_SESSION_B64")
    print("="*60)
    print(b64)
    print("="*60)


# ── main ─────────────────────────────────────────────────────────────────────

username = input("Instagram username: ").strip()
password = input("Instagram password: ").strip()

# Wipe any broken old session so we always do a clean login
if SESSION_FILE.exists():
    SESSION_FILE.unlink()
    print("[Info] Removed stale session file — starting fresh.")

cl = build_client()
print("\n[Login] Attempting login …")

try:
    cl.login(username, password)

except TwoFactorRequired:
    print("\n[2FA] Two-factor authentication is enabled.")
    print("[2FA] Check your authenticator app or SMS.")
    code = input("[2FA] Enter the 6-digit code: ").strip()
    cl.login(username, password, verification_code=code)

except ChallengeRequired as e:
    # instagrapi's built-in challenge_resolve_contact_form can crash on empty
    # responses — we intercept and drive the challenge manually instead.
    print(f"\n[Challenge] Instagram wants to verify your identity.")
    print(f"[Challenge] Exception detail: {e}")

    last = cl.last_json or {}
    challenge_url = last.get("challenge", {}).get("url", "")
    if challenge_url:
        print(f"[Challenge] Challenge URL: {challenge_url}")

    # Try to make Instagram send a code automatically
    sent = False
    for method in (1, 0):          # 1 = email, 0 = SMS
        try:
            cl.challenge_send_code(method)
            label = "email" if method == 1 else "SMS"
            print(f"[Challenge] Verification code sent via {label}.")
            sent = True
            break
        except Exception as ex:
            print(f"[Challenge] Could not send via method {method}: {ex}")

    if not sent:
        print("[Challenge] Could not auto-send a code.")
        print("            Open the Instagram app and approve the 'Suspicious Login'")
        print("            notification, then press Enter here.")

    code = input("[Challenge] Enter the verification code (leave blank if you approved via app): ").strip()

    if code:
        try:
            cl.challenge_resolve(last, code)
        except Exception as ex:
            print(f"[Challenge] challenge_resolve raised: {ex}")
            print("[Challenge] Retrying login on a fresh client …")
            cl = build_client()
            cl.login(username, password)
    else:
        print("[Challenge] Waiting 5 s for app-approval to propagate …")
        time.sleep(5)
        cl = build_client()
        cl.login(username, password)

except BadPassword:
    print("\n[Error] Wrong password — please check your credentials.")
    sys.exit(1)

except ReloginAttemptExceeded:
    print("\n[Error] Too many login attempts. Instagram has temporarily blocked logins.")
    print("        Wait a few hours and try again.")
    sys.exit(1)

except Exception as e:
    print(f"\n[Error] Unexpected error: {type(e).__name__}: {e}")
    try:
        print(f"[Error] Last Instagram JSON:\n{json.dumps(cl.last_json, indent=2)}")
    except Exception:
        pass
    sys.exit(1)

# ── verify ────────────────────────────────────────────────────────────────────
print("\n[Verify] Checking account …")
try:
    info = cl.account_info()
    print(f"[Verify] ✓ Logged in as @{info.username}  (followers: {info.follower_count})")
except Exception as e:
    print(f"[Verify] Could not fetch account info ({e}) — saving session anyway.")

dump_and_print_b64(cl)
print("\n✓ Done.  Run  python main.py  to post.")
