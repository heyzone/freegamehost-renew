import os
import time
import urllib.parse
import requests
from seleniumbase import SB

# ── 环境变量 ────────────────────────────────────────────────
PTERO_SESSION = os.environ.get("PTERO_SESSION", "")
PTERO_XSRF    = os.environ.get("PTERO_XSRF", "")
CF_CLEARANCE  = os.environ.get("CF_CLEARANCE", "")
GOST_PROXY    = os.environ.get("GOST_PROXY", "")
TG_BOT        = os.environ.get("TG_BOT", "")

SERVER_IDS = os.environ.get("SERVER_IDS", "")

BASE_URL = "https://panel.freegamehost.xyz"

# ── Telegram 推送 ────────────────────────────────────────────
def tg_send(text: str):
    if not TG_BOT:
        return
    try:
        parts   = TG_BOT.split(":")
        token   = parts[0] + ":" + parts[1]
        chat_id = parts[2]
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"⚠️ TG推送异常: {e}")

# ── 注入 Cookie ─────────────────────────────────────────────
def inject_cookies(sb):
    print("🍪 注入 Cookie 登录...")

    sb.open(BASE_URL)

    cookies = {
        "pterodactyl_session": PTERO_SESSION,
        "XSRF-TOKEN": PTERO_XSRF,
    }

    if CF_CLEARANCE:
        cookies["cf_clearance"] = CF_CLEARANCE

    for k, v in cookies.items():
        if v:
            sb.execute_script(
                f"document.cookie = '{k}={v}; path=/;';"
            )

    sb.sleep(2)
    print("✅ Cookie 注入完成")

# ── Turnstile ──────────────────────────────────────────────
def wait_for_turnstile(sb, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            token = sb.execute_script(
                "return document.querySelector('[name=cf-turnstile-response]')?.value || '';"
            )
            if token and len(token) > 20:
                return token
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError("❌ Turnstile 超时")

def click_turnstile(sb):
    try:
        sb.switch_to_frame("iframe[src*='challenges.cloudflare.com']")
        sb.click("input[type='checkbox']", timeout=10)
        sb.switch_to_default_content()
    except Exception:
        sb.switch_to_default_content()

# ── 单服务器续期 ─────────────────────────────────────────────
def renew_server(sb, server_id: str) -> dict:
    result = {"server_id": server_id, "name": server_id, "success": False, "remaining": "", "error": ""}
    server_url = f"{BASE_URL}/server/{server_id}"

    try:
        sb.open(server_url)
        sb.wait_for_element_present("body", timeout=20)

        try:
            name = sb.get_text("h1", timeout=5).strip()
        except:
            name = server_id

        result["name"] = name

        selectors = [
            "button:contains('+8 Hours')",
            "button:contains('Renew')",
            "[class*='renew']",
        ]

        clicked = False
        for sel in selectors:
            try:
                sb.click(sel, timeout=5)
                clicked = True
                break
            except:
                pass

        if not clicked:
            raise RuntimeError("找不到续期按钮")

        time.sleep(2)
        click_turnstile(sb)
        wait_for_turnstile(sb)

        time.sleep(3)

        try:
            remaining = sb.get_text("[class*='remaining']", timeout=5)
        except:
            remaining = ""

        result["success"] = True
        result["remaining"] = remaining

        sb.save_screenshot(f"renew_{server_id}.png")

    except Exception as e:
        result["error"] = str(e)
        sb.save_screenshot(f"error_{server_id}.png")

    return result

# ── 主流程 ────────────────────────────────────────────────
def main():
    server_ids = [s.strip() for s in SERVER_IDS.split(",") if s.strip()]

    if not PTERO_SESSION:
        print("❌ 缺少 PTERO_SESSION")
        return

    proxy_str = "http://127.0.0.1:8080" if GOST_PROXY else None

    sb_kwargs = dict(uc=True, headless=True)
    if proxy_str:
        sb_kwargs["proxy"] = proxy_str

    results = []

    with SB(**sb_kwargs) as sb:
        inject_cookies(sb)

        sb.open(BASE_URL)
        sb.sleep(3)

        if "login" in sb.get_current_url().lower():
            raise RuntimeError("❌ Cookie 失效")

        for sid in server_ids:
            r = renew_server(sb, sid)
            results.append(r)

    msg = "\n".join([f"{'✅' if r['success'] else '❌'} {r['name']}" for r in results])
    tg_send(msg)

if __name__ == "__main__":
    main()
