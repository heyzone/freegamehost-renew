import os
import time
import requests
from seleniumbase import SB

# ── 环境变量 ────────────────────────────────────────────────
RAW_ACCOUNT = os.environ.get("FGH_ACCOUNT", "")
GOST_PROXY  = os.environ.get("GOST_PROXY", "")
TG_BOT      = os.environ.get("TG_BOT", "")

BASE_URL  = "https://panel.freegamehost.xyz"
LOGIN_URL = f"{BASE_URL}/auth/login"

# ── Telegram 推送 ────────────────────────────────────────────
def tg_send(text: str):
    if not TG_BOT:
        return
    try:
        parts   = TG_BOT.split(":")
        token   = parts[0] + ":" + parts[1]
        chat_id = parts[2]
        url     = f"https://api.telegram.org/bot{token}/sendMessage"
        resp    = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
        if resp.ok:
            print("📨 TG推送成功")
        else:
            print(f"⚠️ TG推送失败: {resp.text}")
    except Exception as e:
        print(f"⚠️ TG推送异常: {e}")

# ── 解析账号列表 ─────────────────────────────────────────────
def parse_accounts():
    accounts = []
    lines = RAW_ACCOUNT.replace(";", "\n").splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 最多切2刀，密码里有冒号也安全
        parts = line.split(":", 2)
        if len(parts) < 3:
            print(f"⚠️ 账号格式错误，跳过：{line}")
            continue
        email      = parts[0].strip()
        password   = parts[1].strip()
        server_ids = [s.strip() for s in parts[2].split(",") if s.strip()]
        if not server_ids:
            print(f"⚠️ 未找到服务器ID，跳过：{line}")
            continue
        accounts.append({"email": email, "password": password, "server_ids": server_ids})
        print(f"✅ 解析账号：{email}，服务器：{server_ids}")
    return accounts

# ── 等待 Turnstile / reCAPTCHA Token（通用轮询）─────────────
def wait_for_captcha_token(sb, timeout=90):
    print("📡 开始监控验证 Token...")
    print("⏳ 等待验证组件加载...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        # 优先检查 Turnstile
        try:
            token = sb.execute_script(
                "return document.querySelector('[name=\"cf-turnstile-response\"]')?.value || '';"
            )
            if token and len(token) > 20:
                print(f"✅ Cloudflare Turnstile 验证通过！token：{token[:60]}...")
                return "turnstile", token
        except Exception:
            pass

        # 遍历 iframe 查 Turnstile
        try:
            iframe_count = sb.execute_script("return document.querySelectorAll('iframe').length;")
            for i in range(iframe_count):
                try:
                    token = sb.execute_script(f"""
                        try {{
                            var iframe = document.querySelectorAll('iframe')[{i}];
                            var doc = iframe.contentDocument || iframe.contentWindow.document;
                            var el = doc.querySelector('[name="cf-turnstile-response"]');
                            return el ? el.value : '';
                        }} catch(e) {{ return ''; }}
                    """)
                    if token and len(token) > 20:
                        print(f"✅ Cloudflare Turnstile 验证通过（iframe[{i}]）！token：{token[:60]}...")
                        return "turnstile", token
                except Exception:
                    pass
        except Exception:
            pass

        # 检查 reCAPTCHA
        try:
            token = sb.execute_script("""
                var els = document.querySelectorAll(
                    '[id*="g-recaptcha-response"], textarea[name="g-recaptcha-response"]'
                );
                for (var i = 0; i < els.length; i++) {
                    if (els[i].value && els[i].value.length > 20) return els[i].value;
                }
                return '';
            """)
            if token and len(token) > 20:
                print(f"✅ reCAPTCHA 验证通过！token：{token[:60]}...")
                return "recaptcha", token
        except Exception:
            pass

        time.sleep(1)

    raise TimeoutError(f"❌ 验证 Token 等待超时（{timeout}s）")

# ── 点击 Turnstile iframe 中心 ───────────────────────────────
def click_turnstile(sb):
    try:
        sb.sleep(1)
        sb.execute_script("""
            var iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
            if (iframe) { iframe.scrollIntoView({behavior: 'smooth', block: 'center'}); }
        """)
        sb.sleep(0.5)
        iframe_el = sb.find_element("iframe[src*='challenges.cloudflare.com']")
        rect = sb.execute_script(
            "var r = arguments[0].getBoundingClientRect();"
            "return {x: r.left + 20, y: r.top + r.height / 2};",
            iframe_el
        )
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(sb.driver) \
            .move_by_offset(rect["x"], rect["y"]) \
            .click() \
            .move_by_offset(-rect["x"], -rect["y"]) \
            .perform()
        print("📐 坐标点击成功")
    except Exception as e:
        print(f"⚠️ Turnstile 点击异常（依赖自动验证）: {e}")

# ── 浏览器登录 ───────────────────────────────────────────────
def browser_login(sb, email: str, password: str):
    print("🔑 打开登录页面...")
    sb.open(LOGIN_URL)
    sb.sleep(5)
    sb.save_screenshot("login_page.png")

    print("✏️ 填写账号密码...")
    try:
        sb.wait_for_element_present("input", timeout=15)
        # Pterodactyl 是 React 框架，用 nativeInputValueSetter 触发状态更新
        email_js    = email.replace("\\", "\\\\").replace("'", "\\'")
        password_js = password.replace("\\", "\\\\").replace("'", "\\'")
        sb.execute_script(f"""
            var inputs = document.querySelectorAll('input');
            var setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            setter.call(inputs[0], '{email_js}');
            inputs[0].dispatchEvent(new Event('input', {{ bubbles: true }}));
            setter.call(inputs[1], '{password_js}');
            inputs[1].dispatchEvent(new Event('input', {{ bubbles: true }}));
        """)
        sb.sleep(0.5)
    except Exception as e:
        sb.save_screenshot("input_error.png")
        raise RuntimeError(f"填写表单失败: {e}")

    sb.save_screenshot("before_submit.png")

    # 检查是否有验证（Turnstile 或 reCAPTCHA）
    has_turnstile = sb.execute_script(
        "return !!document.querySelector('iframe[src*=\"challenges.cloudflare.com\"]');"
    )
    has_recaptcha = sb.execute_script(
        "return !!document.querySelector('iframe[src*=\"recaptcha\"]') || "
        "!!document.querySelector('.g-recaptcha') || "
        "!!document.getElementById('g-recaptcha-response');"
    )

    if has_turnstile:
        print("📡 开始监控 Turnstile Token（登录页）...")
        click_turnstile(sb)
        wait_for_captcha_token(sb, timeout=90)
    elif has_recaptcha:
        print("📡 检测到 reCAPTCHA，等待自动通过...")
        wait_for_captcha_token(sb, timeout=120)
    else:
        print("ℹ️ 未检测到验证组件，直接提交")

    # 提交登录
    print("📤 提交登录请求...")
    try:
        sb.click(
            "//button[contains(translate(., 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'LOGIN')]",
            timeout=10, by="xpath"
        )
    except Exception:
        try:
            sb.click("button[type='submit']", timeout=5)
        except Exception:
            sb.execute_script("document.querySelector('button').click();")

    print("⏳ 等待登录跳转...")
    sb.sleep(8)
    sb.save_screenshot("after_login.png")

    current = sb.get_current_url()
    print(f"🔁 登录后URL：{current}")

    if "login" in current.lower():
        sb.save_screenshot("login_failed.png")
        raise RuntimeError(f"❌ 登录失败，仍在登录页：{current}")

    print(f"✅ 登录成功！当前页面：{current}")

# ── 单服务器续期 ─────────────────────────────────────────────
def renew_server(sb, server_id: str) -> dict:
    result = {"server_id": server_id, "name": server_id, "success": False, "remaining": "", "error": ""}
    server_url = f"{BASE_URL}/server/{server_id}"
    try:
        print(f"🔗 导航到服务器页面：{server_url}")
        sb.open(server_url)
        print("⏳ 等待服务器页面加载...")
        sb.wait_for_element_present("body", timeout=20)
        sb.sleep(3)
        print("✅ 服务器页面加载完成")

        # 读取服务器名称
        print("🔍 读取服务器名称...")
        name = server_id
        for sel in ["h1", ".server-name", "[class*='server-name']", "[class*='title']"]:
            try:
                raw = sb.get_text(sel, timeout=3).strip()
                if raw:
                    name = raw.splitlines()[0]
                    break
            except Exception:
                continue
        result["name"] = name
        print(f"🖥 服务器名称：{name}")

        print("🔄 开始执行续期流程...")
        sb.save_screenshot(f"before_renew_{server_id}.png")

        # 先启动 Turnstile 监控（后台轮询），再点击续期按钮
        # 这样不会错过验证窗口
        print("📡 开始监控 Turnstile Token...")

        # 点击续期按钮（XPath，兼容性更好）
        renew_btn_xpaths = [
            "//button[contains(., '+8 Hours')]",
            "//a[contains(., '+8 Hours')]",
            "//button[contains(., 'Renew')]",
            "//a[contains(., 'Renew')]",
            "//*[contains(@class,'renew')]",
            "//*[contains(@id,'renew')]",
        ]
        clicked = False
        for xpath in renew_btn_xpaths:
            try:
                sb.click(xpath, timeout=5, by="xpath")
                print(f"🔄 +8 Hours 续期按钮已点击")
                clicked = True
                break
            except Exception:
                continue

        if not clicked:
            sb.save_screenshot(f"no_renew_btn_{server_id}.png")
            raise RuntimeError("找不到续期按钮，请检查截图确认页面结构")

        # 等待验证组件出现并处理
        print("⏳ 等待 Turnstile 验证组件...")
        sb.sleep(2)

        has_turnstile = sb.execute_script(
            "return !!document.querySelector('iframe[src*=\"challenges.cloudflare.com\"]');"
        )
        if has_turnstile:
            print("✅ 验证组件就绪")
            print("📐 坐标计算完成")
            click_turnstile(sb)
            wait_for_captcha_token(sb, timeout=90)

            # 验证通过后点确认（部分面板需要）
            try:
                sb.click(
                    "//button[@type='submit' or contains(., 'Confirm') or contains(., '确认')]",
                    timeout=5, by="xpath"
                )
                print("✅ 续期确认按钮已点击")
            except Exception:
                pass
        else:
            print("ℹ️ 无需验证，续期直接完成")

        print("⏳ 等待续期完成...")
        sb.sleep(3)
        sb.save_screenshot(f"after_renew_{server_id}.png")

        # 读取剩余时间
        remaining = ""
        for sel in ["[class*='remaining']", "[class*='time-left']", "[class*='expire']", ".remaining-time"]:
            try:
                remaining = sb.get_text(sel, timeout=3).strip()
                if remaining:
                    break
            except Exception:
                continue

        result["success"]   = True
        result["remaining"] = remaining
        print(f"🎉 续期成功！剩余时间：{remaining or '（未能读取）'}")

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

    proxy_str = "http://127.0.0.1:8080" if GOST_PROXY else None

    print("🌐 验证出口IP...")
    try:
        proxies   = {"http": proxy_str, "https": proxy_str} if proxy_str else {}
        raw_ip    = requests.get("https://api.ipify.org", proxies=proxies, timeout=10).text.strip()
        ip_masked = ".".join(raw_ip.split(".")[:3]) + ".xx"
        print(f"✅ 出口IP确认：{ip_masked}")
    except Exception as e:
        print(f"⚠️ 出口IP验证失败: {e}，继续...")

    sb_kwargs = dict(uc=True, headless=False)
    if proxy_str:
        sb_kwargs["proxy"] = proxy_str

    results = []
    with SB(**sb_kwargs) as sb:
        print("🔧 启动浏览器...")
        print("🚀 浏览器就绪！")

        browser_login(sb, email, password)

        for sid in server_ids:
            r = renew_server(sb, sid)
            results.append(r)
            sb.sleep(2)

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
                "name":      acc["email"],
                "success":   False,
                "remaining": "",
                "error":     str(e),
            })

    msg = build_tg_message(all_results)
    tg_send(msg)

    success_count = sum(1 for r in all_results if r["success"])
    total_count   = len(all_results)
    print(f"\n🏁 完成：{success_count}/{total_count} 台服务器续期成功")


if __name__ == "__main__":
    main()
