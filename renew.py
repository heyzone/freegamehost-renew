import os
import json
import time
import requests
from seleniumbase import SB

# ── 环境变量 ────────────────────────────────────────────────
# FGH_ACCOUNT 格式：多账号用换行或分号分隔
#   每条格式：email:password:server_id  或  email:password:server_id1,server_id2
RAW_ACCOUNT = os.environ.get("FGH_ACCOUNT", "")
GOST_PROXY  = os.environ.get("GOST_PROXY", "")
TG_BOT      = os.environ.get("TG_BOT", "")   # 格式：bot_token:chat_id

BASE_URL    = "https://panel.freegamehost.xyz"
LOGIN_URL   = f"{BASE_URL}/auth/login"

# ── Telegram 推送 ────────────────────────────────────────────
def tg_send(text: str):
    if not TG_BOT:
        return
    try:
        token, chat_id = TG_BOT.split(":", 1)
        # chat_id 可能含负号（群组），重新拼回
        parts = TG_BOT.split(":")
        token   = parts[0] + ":" + parts[1]
        chat_id = parts[2]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
        if resp.ok:
            print("📨 TG推送成功")
        else:
            print(f"⚠️ TG推送失败: {resp.text}")
    except Exception as e:
        print(f"⚠️ TG推送异常: {e}")

# ── 解析账号列表 ─────────────────────────────────────────────
def parse_accounts():
    """
    支持格式（每行一个账号）：
        email:password:server_id
        email:password:server_id1,server_id2
    """
    accounts = []
    lines = RAW_ACCOUNT.replace(";", "\n").splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 3:
            print(f"⚠️ 账号格式错误，跳过：{line}")
            continue
        email      = parts[0].strip()
        password   = parts[1].strip()
        server_ids = [s.strip() for s in parts[2].split(",") if s.strip()]
        accounts.append({"email": email, "password": password, "server_ids": server_ids})
    return accounts

# ── 等待 Turnstile Token ─────────────────────────────────────
def wait_for_turnstile(sb, timeout=60):
    print("📡 开始监控 Turnstile Token...")
    print("⏳ 等待验证组件加载...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            token = sb.execute_script(
                "return document.querySelector('[name=cf-turnstile-response]')?.value || '';"
            )
            if token and len(token) > 20:
                print(f"✅ Cloudflare Turnstile 验证通过！token：{token[:60]}...")
                return token
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError("❌ Turnstile Token 等待超时")

# ── 点击 Turnstile iframe ────────────────────────────────────
def click_turnstile(sb):
    print("📐 坐标计算完成")
    try:
        # 尝试直接点击 iframe 内的 checkbox
        sb.switch_to_frame("iframe[src*='challenges.cloudflare.com']")
        sb.click("input[type='checkbox']", timeout=10)
        sb.switch_to_default_content()
    except Exception:
        try:
            sb.switch_to_default_content()
            # fallback：用 JS 定位 iframe 坐标后模拟点击
            sb.execute_script("""
                const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                if (iframe) {
                    const rect = iframe.getBoundingClientRect();
                    const x = rect.left + rect.width / 2;
                    const y = rect.top + rect.height / 2;
                    document.elementFromPoint(x, y)?.click();
                }
            """)
        except Exception as e:
            print(f"⚠️ Turnstile 点击异常: {e}")
    print("📐 坐标点击成功")

# ── 单服务器续期 ─────────────────────────────────────────────
def renew_server(sb, server_id: str) -> dict:
    """
    返回 {"server_id": ..., "name": ..., "success": bool, "remaining": str, "error": str}
    """
    result = {"server_id": server_id, "name": server_id, "success": False, "remaining": "", "error": ""}
    server_url = f"{BASE_URL}/server/{server_id}"
    try:
        print(f"🔗 导航到服务器页面：{server_url}")
        sb.open(server_url)
        print("⏳ 等待服务器页面加载...")
        sb.wait_for_element_present("body", timeout=20)
        print("✅ 服务器页面加载完成")

        # 读取服务器名称
        print("🔍 读取服务器名称...")
        try:
            name = sb.get_text("h1, .server-name, [class*='server-name'], [class*='title']", timeout=5)
            name = name.strip().splitlines()[0]
        except Exception:
            name = server_id
        result["name"] = name
        print(f"🖥 服务器名称：{name}")

        print("🔄 开始执行续期流程...")

        # 点击 +8 Hours 续期按钮
        renew_btn_selectors = [
            "button:contains('+8 Hours')",
            "button:contains('Renew')",
            "a:contains('+8 Hours')",
            "[class*='renew']",
            "button[id*='renew']",
        ]
        clicked = False
        for sel in renew_btn_selectors:
            try:
                sb.click(sel, timeout=5)
                print("🔄 +8 Hours 续期按钮已点击")
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            raise RuntimeError("找不到续期按钮")

        # Turnstile 验证
        print("⏳ 等待 Turnstile 验证组件...")
        time.sleep(2)
        click_turnstile(sb)
        wait_for_turnstile(sb, timeout=60)

        # 等待续期完成
        print("⏳ 等待续期完成...")
        time.sleep(3)

        # 读取剩余时间（示例选择器，按实际页面调整）
        remaining = ""
        for sel in ["[class*='remaining']", "[class*='time-left']", "[class*='expire']", ".remaining-time"]:
            try:
                remaining = sb.get_text(sel, timeout=5).strip()
                if remaining:
                    break
            except Exception:
                continue

        # 截图留证
        sb.save_screenshot(f"renew_{server_id}.png")

        result["success"]   = True
        result["remaining"] = remaining
        print(f"🎉 续期成功！剩余时间：{remaining}")

    except Exception as e:
        result["error"] = str(e)
        print(f"❌ 续期失败 [{server_id}]: {e}")
        try:
            sb.save_screenshot(f"error_{server_id}.png")
        except Exception:
            pass

    return result

# ── 单账号主流程 ─────────────────────────────────────────────
def process_account(account: dict):
    email      = account["email"]
    password   = account["password"]
    server_ids = account["server_ids"]

    proxy_args = {}
    if GOST_PROXY:
        proxy_args["proxy"] = "http://127.0.0.1:8080"

    results = []

    with SB(uc=True, headless=True, **proxy_args) as sb:
        print("🔧 启动浏览器...")
        print("🚀 浏览器就绪！")

        # 验证出口 IP
        print("🌐 验证出口IP...")
        try:
            ip_info = sb.execute_script("""
                return await fetch('https://api.ipify.org?format=json').then(r => r.json()).then(d => JSON.stringify(d));
            """)
            ip_masked = ""
            if ip_info:
                ip_data = json.loads(ip_info)
                raw_ip  = ip_data.get("ip", "unknown")
                parts   = raw_ip.split(".")
                ip_masked = ".".join(parts[:3]) + ".xx"
            print(f'✅ 出口IP确认：{{"ip":"{ip_masked}"}} Pretty-print')
        except Exception:
            print("⚠️ 出口IP验证失败，继续...")

        # 登录
        print("🔑 打开登录页面...")
        sb.open(LOGIN_URL)
        sb.wait_for_element_present("input[type='email'], input[name='email']", timeout=20)

        print("✏️ 填写账号密码...")
        sb.type("input[type='email'], input[name='email']", email)
        sb.type("input[type='password'], input[name='password']", password)

        # 登录页 Turnstile
        print("📡 开始监控 Turnstile Token（登录页）...")
        time.sleep(2)
        click_turnstile(sb)

        print("📤 提交登录请求...")
        try:
            sb.click("button[type='submit']", timeout=10)
        except Exception:
            sb.press_keys("input[type='password']", "\n")

        print("⏳ 等待登录跳转...")
        sb.wait_for_url_to_contain("/", timeout=30)
        current = sb.get_current_url()

        if "login" in current.lower():
            raise RuntimeError(f"登录失败，仍在登录页：{current}")

        print(f"✅ 登录成功！当前页面：{current}")

        # 逐个续期
        for sid in server_ids:
            r = renew_server(sb, sid)
            results.append(r)

    return results

# ── 汇总推送 ─────────────────────────────────────────────────
def build_tg_message(all_results: list) -> str:
    lines = ["<b>🎮 FGH 续期报告</b>"]
    for r in all_results:
        status = "✅" if r["success"] else "❌"
        line   = f'{status} <b>{r["name"]}</b> (<code>{r["server_id"]}</code>)'
        if r["success"] and r["remaining"]:
            line += f'\n   ⏱ 剩余：{r["remaining"]}'
        elif not r["success"]:
            line += f'\n   ⚠️ {r["error"][:80]}'
        lines.append(line)
    return "\n".join(lines)

# ── 入口 ─────────────────────────────────────────────────────
def main():
    accounts = parse_accounts()
    if not accounts:
        print("❌ 未找到有效账号，请检查 FGH_ACCOUNT 环境变量")
        return

    all_results = []
    for acc in accounts:
        print(f"\n{'='*50}")
        print(f"👤 处理账号：{acc['email']}")
        print(f"{'='*50}")
        try:
            results = process_account(acc)
            all_results.extend(results)
        except Exception as e:
            print(f"❌ 账号 {acc['email']} 处理失败: {e}")
            all_results.append({
                "server_id": ",".join(acc["server_ids"]),
                "name": acc["email"],
                "success": False,
                "remaining": "",
                "error": str(e),
            })

    # 汇总推送
    msg = build_tg_message(all_results)
    tg_send(msg)

    success_count = sum(1 for r in all_results if r["success"])
    total_count   = len(all_results)
    print(f"\n🏁 完成：{success_count}/{total_count} 台服务器续期成功")


if __name__ == "__main__":
    main()