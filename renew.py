import os
import time
import requests
from seleniumbase import SB

# ── 环境变量 ────────────────────────────────────────────────
PTERO_SESSION = os.environ.get("PTERO_SESSION", "")
PTERO_XSRF    = os.environ.get("PTERO_XSRF", "")
CF_CLEARANCE  = os.environ.get("CF_CLEARANCE", "")
GOST_PROXY    = os.environ.get("GOST_PROXY", "")
TG_BOT        = os.environ.get("TG_BOT", "")
SERVER_IDS    = os.environ.get("SERVER_IDS", "")

BASE_URL = "https://panel.freegamehost.xyz"
DOMAIN   = "panel.freegamehost.xyz"

# ── TG 推送 ────────────────────────────────────────────────
def tg_send(text: str):
    if not TG_BOT:
        return
    try:
        token, chat_id = TG_BOT.rsplit(":", 1)
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        print(f"TG失败: {e}")

# ── 注入 Cookie（修复版）────────────────────────────────────
def inject_cookies(sb):
    print("🍪 注入 Cookie 登录...")

    # 必须先打开域名
    sb.open(BASE_URL)
    time.sleep(2)

    cookies = []

    if PTERO_SESSION:
        cookies.append({
            "name": "pterodactyl_session",
            "value": PTERO_SESSION,
            "domain": DOMAIN,
            "path": "/",
        })

    if PTERO_XSRF:
        cookies.append({
            "name": "XSRF-TOKEN",
            "value": PTERO_XSRF,
            "domain": DOMAIN,
            "path": "/",
        })

    if CF_CLEARANCE:
        cookies.append({
            "name": "cf_clearance",
            "value": CF_CLEARANCE,
            "domain": DOMAIN,
            "path": "/",
        })

    for c in cookies:
        try:
            sb.driver.add_cookie(c)
            print(f"✅ 注入 {c['name']}")
        except Exception as e:
            print(f"⚠️ 注入失败 {c['name']}: {e}")

    # 关键：刷新让 cookie 生效
    sb.refresh()
    time.sleep(3)

    print("🔍 当前浏览器 Cookie：")
    for c in sb.driver.get_cookies():
        print(f" - {c['name']}")

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
        except:
            pass
        time.sleep(1)
    raise TimeoutError("Turnstile 超时")

def click_turnstile(sb):
    try:
        sb.switch_to_frame("iframe[src*='challenges.cloudflare.com']")
        sb.click("input[type='checkbox']", timeout=10)
        sb.switch_to_default_content()
    except:
        sb.switch_to_default_content()

# ── 续期 ───────────────────────────────────────────────────
def renew_server(sb, server_id: str):
    result = {"server_id": server_id, "success": False, "error": ""}

    try:
        sb.open(f"{BASE_URL}/server/{server_id}")
        sb.wait_for_element_present("body", timeout=20)

        selectors = ["button:contains('+8 Hours')", "[class*='renew']"]

        for sel in selectors:
            try:
                sb.click(sel, timeout=5)
                break
            except:
                pass
        else:
            raise RuntimeError("找不到按钮")

        time.sleep(2)
        click_turnstile(sb)
        wait_for_turnstile(sb)

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result

# ── 主流程 ────────────────────────────────────────────────
def main():
    if not PTERO_SESSION:
        raise RuntimeError("缺少 PTERO_SESSION")

    server_ids = [s.strip() for s in SERVER_IDS.split(",") if s.strip()]

    proxy_str = "http://127.0.0.1:8080" if GOST_PROXY else None

    sb_kwargs = dict(uc=True, headless=True)
    if proxy_str:
        sb_kwargs["proxy"] = proxy_str

    results = []

    with SB(**sb_kwargs) as sb:
        inject_cookies(sb)

        sb.open(BASE_URL)
        time.sleep(3)

        current = sb.get_current_url()
        print(f"🌐 当前URL: {current}")

        if "login" in current.lower():
            sb.save_screenshot("cookie_invalid.png")
            raise RuntimeError("❌ Cookie 失效（或未正确注入）")

        print("✅ Cookie 登录成功")

        for sid in server_ids:
            results.append(renew_server(sb, sid))

    tg_send(str(results))

if __name__ == "__main__":
    main()
