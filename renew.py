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

# ── 等待 reCAPTCHA v2 完成 ───────────────────────────────────
def wait_for_recaptcha(sb, timeout=120):
    print("📡 开始轮询 reCAPTCHA Token...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        # 方式1：标准 id
        try:
            token = sb.execute_script(
                "return document.getElementById('g-recaptcha-response')?.value || '';"
            )
            if token and len(token) > 20:
                print(f"✅ reCAPTCHA Token 获取成功：{token[:40]}...")
                return token
        except Exception:
            pass

        # 方式2：遍历所有 g-recaptcha-response 元素
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
                print(f"✅ reCAPTCHA Token 获取成功（备用）：{token[:40]}...")
                return token
        except Exception:
            pass

        remaining = int(deadline - time.time())
        if remaining % 10 == 0 and remaining > 0:
            print(f"⏳ 等待 reCAPTCHA 通过...（剩余 {remaining}s）")
        time.sleep(1)

    raise TimeoutError("❌ reCAPTCHA 等待超时（120s）")

# ── 等待 Turnstile Token（iframe 轮询，续期页用）─────────────
def wait_for_turnstile(sb, timeout=90):
    print("📡 开始轮询 Turnstile Token...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        # 方式1：主页面隐藏 input
        try:
            token = sb.execute_script(
                "return document.querySelector('[name=\"cf-turnstile-response\"]')?.value || '';"
            )
            if token and len(token) > 20:
                print(f"✅ Turnstile Token 获取成功（主页面）：{token[:40]}...")
                return token
        except Exception:
            pass

        # 方式2：遍历所有 iframe
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
                        print(f"✅ Turnstile Token 获取成功（iframe[{i}]）：{token[:40]}...")
                        return token
                except Exception:
                    pass
        except Exception:
            pass

        time.sleep(1)

    raise TimeoutError("❌ Turnstile Token 等待超时（90s）")

# ── 点击 Turnstile iframe 中心（续期页用）───────────────────
def click_turnstile(sb):
    print("🖱️ 尝试点击 Turnstile 复选框...")
    try:
        sb.sleep(2)
        sb.execute_script("""
            var iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
            if (iframe) {
                iframe.scrollIntoView({behavior: 'smooth', block: 'center'});
            }
        """)
        sb.sleep(1)
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
        print("✅ Turnstile 点击完成")
    except Exception as e:
        print(f"⚠️ Turnstile 点击异常（将依赖自动验证）: {e}")

# ── 浏览器登录 ───────────────────────────────────────────────
def browser_login(sb, email: str, password: str):
    print(f"🔑 打开登录页：{LOGIN_URL}")
    sb.open(LOGIN_URL)
    sb.sleep(5)
    sb.save_screenshot("login_page.png")

    print("📝 填写登录表单...")
    try:
        sb.wait_for_element_present("input", timeout=15)
        # Pterodactyl 是 React 框架，需用 nativeInputValueSetter 触发状态更新
        sb.execute_script("""
            var inputs = document.querySelectorAll('input');
            var setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            setter.call(inputs[0], arguments[0]);
            inputs[0].dispatchEvent(new Event('input', { bubbles: true }));
            setter.call(inputs[1], arguments[1]);
            inputs[1].dispatchEvent(new Event('input', { bubbles: true }));
        """, email, password)
        sb.sleep(0.5)
        print("✅ 邮箱和密码填写完成")
    except Exception as e:
        sb.save_screenshot("input_error.png")
        raise RuntimeError(f"填写表单失败: {e}")

    sb.save_screenshot("before_recaptcha.png")

    # 等待 reCAPTCHA 自动完成（UC模式会自动处理）
    print("🛡️ 等待 reCAPTCHA 自动验证（最长120s）...")
    wait_for_recaptcha(sb, timeout=120)

    sb.save_screenshot("after_recaptcha.png")

    # 点击 LOGIN 按钮
    print("🚀 点击 LOGIN 按钮...")
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
    print("✅ LOGIN 按钮已点击")

    # 等待跳转
    print("⏳ 等待登录跳转...")
    sb.sleep(8)
    sb.save_screenshot("after_login.png")

    current = sb.get_current_url()
    print(f"🔁 登录后URL：{current}")

    if "login" in current.lower():
        sb.save_screenshot("login_failed.png")
        raise RuntimeError(f"❌ 登录失败，仍在登录页：{current}")

    print(f"✅ 登录成功！当前URL：{current}")

# ── 单服务器续期 ─────────────────────────────────────────────
def renew_server(sb, server_id: str) -> dict:
    result = {"server_id": server_id, "name": server_id, "success": False, "remaining": "", "error": ""}
    server_url = f"{BASE_URL}/server/{server_id}"
    try:
        print(f"\n🔗 导航到服务器页面：{server_url}")
        sb.open(server_url)
        sb.sleep(3)
        sb.wait_for_element_present("body", timeout=20)

        # 读取服务器名称
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
        print(f"🖥️  服务器名称：{name}")

        sb.save_screenshot(f"before_renew_{server_id}.png")

        # 点击续期按钮（XPath）
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
                print(f"✅ 续期按钮已点击：{xpath}")
                clicked = True
                break
            except Exception:
                continue

        if not clicked:
            sb.save_screenshot(f"no_renew_btn_{server_id}.png")
            raise RuntimeError("找不到续期按钮，请检查截图确认页面结构")

        # 检查是否有 Turnstile
        sb.sleep(2)
        has_turnstile = sb.execute_script(
            "return !!document.querySelector('iframe[src*=\"challenges.cloudflare.com\"]');"
        )
        if has_turnstile:
            print("🛡️ 检测到续期 Turnstile，开始处理...")
            click_turnstile(sb)
            wait_for_turnstile(sb, timeout=90)
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
            print("ℹ️ 续期无需 Turnstile 验证")

        sb.sleep(4)
        sb.save_screenshot(f"after_renew_{server_id}.png")

        # 读取剩余时间
        remaining = ""
        for sel in [
            "[class*='remaining']", "[class*='time-left']",
            "[class*='expire']", ".remaining-time",
        ]:
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

    # 验证出口 IP
    print("🌐 验证出口IP...")
    try:
        proxies   = {"http": proxy_str, "https": proxy_str} if proxy_str else {}
        raw_ip    = requests.get("https://api.ipify.org", proxies=proxies, timeout=10).text.strip()
        ip_parts  = raw_ip.split(".")
        ip_masked = ".".join(ip_parts[:3]) + ".xx"
        print(f"✅ 出口IP确认：{ip_masked}")
    except Exception as e:
        print(f"⚠️ 出口IP验证失败: {e}，继续...")

    sb_kwargs = dict(uc=True, headless=True)
    if proxy_str:
        sb_kwargs["proxy"] = proxy_str

    results = []
    with SB(**sb_kwargs) as sb:
        print("🔧 浏览器已启动")

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
