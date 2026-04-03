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

# ── 读取页面时间（HH:MM:SS 格式，排除 00:00:00）────────────
def read_remaining_time(sb):
    try:
        val = sb.execute_script("""
            var els = document.querySelectorAll('*');
            for (var i = 0; i < els.length; i++) {
                var t = (els[i].childNodes.length === 1 ? els[i].innerText || '' : '').trim();
                if (/^\\d{2}\\s*:\\s*\\d{2}\\s*:\\s*\\d{2}$/.test(t)) return t;
            }
            return '';
        """)
        if val:
            return val.replace(" ", "")
    except Exception:
        pass
    return ""

# ── 等待 Turnstile 复选框出现并点击 ─────────────────────────
# 点击按钮后会先显示 "Verifying..."，需要等复选框真正出现
def wait_and_click_turnstile(sb, timeout=30):
    print("⏳ 等待 Turnstile 复选框出现（跳过 Verifying 状态）...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        # 检查是否已经显示复选框（不再是 Verifying 状态）
        is_ready = sb.execute_script("""
            var iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
            if (!iframe) return false;
            try {
                var doc = iframe.contentDocument || iframe.contentWindow.document;
                var cb = doc.querySelector('input[type="checkbox"]');
                var label = doc.querySelector('.ctp-checkbox-label');
                return !!(cb || label);
            } catch(e) { return false; }
        """)
        if is_ready:
            print("✅ Turnstile 复选框已就绪，开始点击...")
            break
        time.sleep(1)

    # 点击 iframe 中心偏左位置（复选框位置）
    try:
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
        print("📐 Turnstile 复选框点击完成")
    except Exception as e:
        print(f"⚠️ Turnstile 点击异常: {e}")

# ── 等待 Turnstile Token ─────────────────────────────────────
def wait_for_turnstile_token(sb, timeout=90):
    print("📡 开始监控 Turnstile Token...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            token = sb.execute_script(
                "return document.querySelector('[name=\"cf-turnstile-response\"]')?.value || '';"
            )
            if token and len(token) > 20:
                print(f"✅ Cloudflare Turnstile 验证通过！token：{token[:60]}...")
                return token
        except Exception:
            pass

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
                        return token
                except Exception:
                    pass
        except Exception:
            pass

        time.sleep(1)

    raise TimeoutError(f"❌ Turnstile Token 等待超时（{timeout}s）")

# ── 等待并处理续期 Turnstile（含重试）───────────────────────
def handle_renew_turnstile(sb, timeout_wait=15, timeout_token=90):
    # 等待 Turnstile iframe 出现（点击续期按钮后可能需要几秒）
    print("⏳ 等待 Turnstile 验证组件出现...")
    deadline = time.time() + timeout_wait
    appeared = False
    while time.time() < deadline:
        has = sb.execute_script(
            "return !!document.querySelector('iframe[src*=\"challenges.cloudflare.com\"]');"
        )
        if has:
            appeared = True
            print("✅ Turnstile 验证组件已出现")
            break
        time.sleep(1)

    if not appeared:
        print("ℹ️ 无需 Turnstile 验证")
        return False

    # 等复选框就绪后点击
    wait_and_click_turnstile(sb, timeout=30)

    # 等待 token
    wait_for_turnstile_token(sb, timeout=timeout_token)
    return True

# ── 点击 LOGIN 按钮 ──────────────────────────────────────────
def click_login_button(sb):
    try:
        sb.click(
            "//button[contains(translate(., 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'LOGIN')]",
            timeout=10, by="xpath"
        )
        return
    except Exception:
        pass
    try:
        sb.click("button[type='submit']", timeout=5)
        return
    except Exception:
        pass
    sb.execute_script("document.querySelector('button').click();")

# ── 浏览器登录 ───────────────────────────────────────────────
def browser_login(sb, email: str, password: str):
    print("🔑 打开登录页面...")
    sb.open(LOGIN_URL)
    sb.sleep(5)
    sb.save_screenshot("login_page.png")

    print("✏️ 填写账号密码...")
    try:
        sb.wait_for_element_present("input", timeout=15)
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
        sb.sleep(1)
        print("✅ 账号密码填写完成")
    except Exception as e:
        sb.save_screenshot("input_error.png")
        raise RuntimeError(f"填写表单失败: {e}")

    # 检查登录页是否有 Turnstile
    has_turnstile = sb.execute_script(
        "return !!document.querySelector('iframe[src*=\"challenges.cloudflare.com\"]');"
    )
    if has_turnstile:
        print("🛡️ 登录页检测到 Turnstile...")
        wait_and_click_turnstile(sb, timeout=30)
        wait_for_turnstile_token(sb, timeout=90)

    print("📤 提交登录请求...")
    click_login_button(sb)

    print("⏳ 等待登录跳转...")
    sb.sleep(8)
    sb.save_screenshot("after_login.png")

    current = sb.get_current_url()
    if "login" in current.lower():
        print("⏳ 仍在登录页，再等 10s...")
        sb.sleep(10)
        current = sb.get_current_url()

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

        sb.save_screenshot(f"loaded_{server_id}.png")

        # ── 读取服务器名称（从侧边栏 ID 上方的名字）────────
        print("🔍 读取服务器名称...")
        name = sb.execute_script("""
            // FGH 侧边栏结构：服务器名在 ID 上方，是短文本
            // 找包含 "ID:" 文字的父元素，取其前一个兄弟节点
            var allEls = document.querySelectorAll('*');
            for (var i = 0; i < allEls.length; i++) {
                var t = (allEls[i].innerText || '').trim();
                if (t.startsWith('ID:') && t.length < 30) {
                    // 找父级里 ID 上面的文字
                    var parent = allEls[i].parentElement;
                    if (parent) {
                        var children = Array.from(parent.children);
                        var idx = children.indexOf(allEls[i]);
                        if (idx > 0) {
                            var nameEl = children[idx - 1];
                            var name = (nameEl.innerText || '').trim();
                            if (name && name.length < 50) return name;
                        }
                        // 尝试父级的前一个兄弟
                        var prev = parent.previousElementSibling;
                        if (prev) {
                            var name = (prev.innerText || '').trim().split('\\n')[0];
                            if (name && name.length < 50 && !name.includes('/')) return name;
                        }
                    }
                }
            }
            return '';
        """)
        if not name:
            # 备用：取侧边栏里 ID 文字上方的短文本
            name = sb.execute_script("""
                var spans = document.querySelectorAll('span, p, div, h1, h2, h3, h4');
                for (var i = 0; i < spans.length; i++) {
                    var t = (spans[i].innerText || '').trim();
                    // 找紧跟在 ID 模式文本后面出现的短名字
                    if (t.length > 0 && t.length < 30
                        && spans[i].children.length === 0
                        && !t.match(/^[0-9a-f-]{8}/i)
                        && !t.includes('/')
                        && !t.includes('@')
                        && !t.includes('ID')
                        && !t.includes('Ad ')
                        && !t.includes('Block')
                        && !t.includes('Detected')
                        && !t.includes('Dashboard')
                        && !t.includes('Account')
                        && !t.includes('Console')
                        && !t.includes('Sign')
                        && !t.includes('Upgrade')) {
                        return t;
                    }
                }
                return '';
            """)
        result["name"] = name or server_id
        print(f"🖥 服务器名称：{result['name']}")

        # ── 续期前读取剩余时间 ───────────────────────────────
        time_before = read_remaining_time(sb)
        print(f"⏱ 续期前剩余时间：{time_before or '00:00:00'}")

        print("🔄 开始执行续期流程...")

        # 点击 +8 HOURS 按钮
        renew_btn_xpaths = [
            "//button[contains(., '+8 HOURS')]",
            "//button[contains(., '+8 Hours')]",
            "//button[contains(., '+8 hours')]",
            "//button[contains(translate(., 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), '+8 HOURS')]",
            "//a[contains(translate(., 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), '+8 HOURS')]",
        ]
        clicked = False
        for xpath in renew_btn_xpaths:
            try:
                sb.click(xpath, timeout=5, by="xpath")
                print(f"🔄 +8 HOURS 续期按钮已点击")
                clicked = True
                break
            except Exception:
                continue

        if not clicked:
            sb.save_screenshot(f"no_renew_btn_{server_id}.png")
            raise RuntimeError("找不到续期按钮，请检查截图确认页面结构")

        # ── 处理 Turnstile（等待出现 + 等复选框就绪 + 点击）─
        handled = handle_renew_turnstile(sb, timeout_wait=15, timeout_token=90)

        print("⏳ 等待续期完成...")
        sb.sleep(5)
        sb.save_screenshot(f"after_renew_{server_id}.png")

        # ── 续期后读取剩余时间 ───────────────────────────────
        time_after = read_remaining_time(sb)
        print(f"⏱ 续期后剩余时间：{time_after or '00:00:00'}")

        # 如果时间还是 00:00:00，刷新再读一次
        if not time_after or time_after == "00:00:00":
            print("🔄 刷新页面再确认...")
            sb.refresh()
            sb.sleep(3)
            time_after = read_remaining_time(sb)
            print(f"⏱ 刷新后剩余时间：{time_after or '00:00:00'}")

        # ── 判断续期是否成功 ─────────────────────────────────
        # 成功条件：续期后时间不为 00:00:00，且与续期前不同（或前后都为0但已操作）
        if time_after and time_after != "00:00:00":
            result["success"]   = True
            result["remaining"] = time_after
            print(f"🎉 续期成功！剩余时间：{time_after}")
        else:
            # 时间仍为 0，但如果 Turnstile 通过了，标记为可能成功
            if handled:
                result["success"]   = True
                result["remaining"] = time_after or "（读取失败）"
                print(f"⚠️ 续期操作已完成（Turnstile已通过），但剩余时间读取为0，请手动确认")
            else:
                raise RuntimeError("续期后剩余时间仍为 00:00:00，续期可能未生效")

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
