import os
import time
import subprocess
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

# ── 读取页面时间（HH:MM:SS 格式）────────────────────────────
def read_remaining_time(sb):
    try:
        val = sb.execute_script("""
            (function() {
                var els = document.querySelectorAll('*');
                for (var i = 0; i < els.length; i++) {
                    var t = (els[i].childNodes.length === 1 ? els[i].innerText || '' : '').trim();
                    if (/^\\d{2}\\s*:\\s*\\d{2}\\s*:\\s*\\d{2}$/.test(t)) return t;
                }
                return '';
            })();
        """)
        if val:
            return val.replace(" ", "")
    except Exception:
        pass
    return ""

# ── xdotool 系统级点击（绕过所有 Selenium 限制）─────────────
def xdotool_click(screen_x, screen_y):
    try:
        # 移动鼠标到目标位置并点击
        subprocess.run(
            ["xdotool", "mousemove", "--sync", str(int(screen_x)), str(int(screen_y))],
            check=True, timeout=5
        )
        time.sleep(0.3)
        subprocess.run(
            ["xdotool", "click", "1"],
            check=True, timeout=5
        )
        print(f"📐 xdotool 点击成功：({int(screen_x)}, {int(screen_y)})")
        return True
    except Exception as e:
        print(f"⚠️ xdotool 点击失败: {e}")
        return False

# ── 获取浏览器窗口在屏幕上的位置偏移 ────────────────────────
def get_browser_window_offset(sb):
    try:
        # 获取浏览器窗口位置
        rect = sb.driver.get_window_rect()
        win_x = rect.get("x", 0)
        win_y = rect.get("y", 0)

        # 获取浏览器内部 viewport 偏移（工具栏高度等）
        offsets = sb.execute_script("""
            (function() {
                return {
                    outerWidth: window.outerWidth,
                    outerHeight: window.outerHeight,
                    innerWidth: window.innerWidth,
                    innerHeight: window.innerHeight,
                    screenX: window.screenX,
                    screenY: window.screenY
                };
            })();
        """)

        # Chrome 工具栏高度 = outerHeight - innerHeight
        toolbar_height = offsets["outerHeight"] - offsets["innerHeight"]
        # 左侧边栏宽度（通常为0）
        sidebar_width = offsets["outerWidth"] - offsets["innerWidth"]

        # 实际 viewport 在屏幕上的起始位置
        viewport_x = offsets["screenX"] + sidebar_width
        viewport_y = offsets["screenY"] + toolbar_height

        print(f"🖥 浏览器窗口：pos=({win_x},{win_y}) viewport起点=({viewport_x},{viewport_y}) toolbar高={toolbar_height}")
        return viewport_x, viewport_y
    except Exception as e:
        print(f"⚠️ 获取窗口偏移失败: {e}，使用默认值")
        return 0, 100  # Chrome 默认工具栏约100px

# ── 等待 Turnstile Token ─────────────────────────────────────
def wait_for_turnstile_token(sb, timeout=90):
    print("📡 开始监控 Turnstile Token...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            token = sb.execute_script("""
                (function() {
                    var els = document.querySelectorAll('[name="cf-turnstile-response"]');
                    for (var i = 0; i < els.length; i++) {
                        if (els[i].value && els[i].value.length > 20) return els[i].value;
                    }
                    return '';
                })();
            """)
            if token and len(token) > 20:
                print(f"✅ Cloudflare Turnstile 验证通过！token：{token[:60]}...")
                return token
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError(f"❌ Turnstile Token 等待超时（{timeout}s）")

# ── 检查 token ───────────────────────────────────────────────
def check_token(sb):
    try:
        token = sb.execute_script("""
            (function() {
                var els = document.querySelectorAll('[name="cf-turnstile-response"]');
                for (var i = 0; i < els.length; i++) {
                    if (els[i].value && els[i].value.length > 20) return els[i].value;
                }
                return '';
            })();
        """)
        return token if (token and len(token) > 20) else None
    except Exception:
        return None

# ── 检查是否还在 Verifying ───────────────────────────────────
def is_verifying(sb):
    try:
        return sb.execute_script("""
            (function() {
                var box = document.querySelector('[class*="TurnstileBox"]');
                if (!box) return false;
                return box.innerText.indexOf('Verifying') !== -1;
            })();
        """)
    except Exception:
        return False

# ── 处理续期 Turnstile（xdotool 版）─────────────────────────
def handle_turnstile(sb, timeout_wait=40, timeout_token=90):
    print("⏳ 等待 Turnstile widget 出现（最多40s）...")
    deadline = time.time() + timeout_wait

    # 1. 等待 TurnstileBox 出现
    appeared = False
    while time.time() < deadline:
        token = check_token(sb)
        if token:
            print(f"✅ Turnstile 已自动通过！")
            return True

        exists = sb.execute_script("""
            (function() { return !!document.querySelector('[class*="TurnstileBox"]'); })();
        """)
        if exists:
            print("✅ 验证组件就绪")
            appeared = True
            break
        time.sleep(1)

    if not appeared:
        print("ℹ️ 未检测到 Turnstile widget，跳过")
        return False

    # 2. 等待 Verifying 结束（最多30s）
    print("⏳ 等待 Verifying 状态结束...")
    verifying_deadline = time.time() + 30
    while time.time() < verifying_deadline:
        token = check_token(sb)
        if token:
            print(f"✅ Turnstile 已自动通过！")
            return True
        verifying = is_verifying(sb)
        if verifying:
            print("⏳ 仍在 Verifying...")
        else:
            print("✅ Verifying 结束，准备点击")
            break
        time.sleep(2)
    else:
        print("⚠️ Verifying 等待超时，强行点击")

    sb.sleep(0.5)

    # 3. 先把 TurnstileBox 滚入视口中心
    sb.execute_script("""
        (function() {
            var box = document.querySelector('[class*="TurnstileBox"]');
            if (box) box.scrollIntoView({behavior: 'instant', block: 'center'});
        })();
    """)
    sb.sleep(1)

    # 4. 获取 widget div（input 父级）在视口中的坐标
    page_coords = sb.execute_script("""
        (function() {
            var inp = document.querySelector('[name="cf-turnstile-response"]');
            if (!inp) return null;
            var div = inp.parentElement;
            if (!div) return null;
            var r = div.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) {
                return {x: r.left + 20, y: r.top + r.height / 2, w: r.width, h: r.height};
            }
            // 备用：TurnstileBox
            var box = document.querySelector('[class*="TurnstileBox"]');
            if (!box) return null;
            var r2 = box.getBoundingClientRect();
            return {x: r2.left + r2.width * 0.15, y: r2.top + r2.height / 2, w: r2.width, h: r2.height};
        })();
    """)

    if not page_coords:
        print("⚠️ 找不到 widget 坐标")
        return False

    print(f"📐 视口坐标：x={page_coords['x']:.0f}, y={page_coords['y']:.0f}")

    # 5. 计算屏幕绝对坐标
    viewport_x, viewport_y = get_browser_window_offset(sb)
    screen_x = viewport_x + page_coords["x"]
    screen_y = viewport_y + page_coords["y"]
    print(f"📐 屏幕坐标：x={screen_x:.0f}, y={screen_y:.0f}")

    # 6. xdotool 系统级点击
    xdotool_click(screen_x, screen_y)
    sb.sleep(1)

    # 7. 等待 token
    wait_for_turnstile_token(sb, timeout=timeout_token)
    return True

# ── 点击 LOGIN 按钮 ──────────────────────────────────────────
def click_login_button(sb):
    try:
        sb.click("//button[normalize-space(.)='LOGIN']", timeout=8, by="xpath")
        return
    except Exception:
        pass
    try:
        sb.click("//button[contains(.,'LOGIN') and not(contains(.,'Reload'))]",
                 timeout=5, by="xpath")
        return
    except Exception:
        pass
    try:
        sb.click("button[type='submit']:last-of-type", timeout=5)
        return
    except Exception:
        pass
    sb.execute_script("""
        (function() {
            var btns = document.querySelectorAll('button[type="submit"]');
            if (btns.length > 0) btns[btns.length - 1].click();
        })();
    """)

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
            (function() {{
                var inputs = document.querySelectorAll('input');
                var setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                setter.call(inputs[0], '{email_js}');
                inputs[0].dispatchEvent(new Event('input', {{ bubbles: true }}));
                setter.call(inputs[1], '{password_js}');
                inputs[1].dispatchEvent(new Event('input', {{ bubbles: true }}));
            }})();
        """)
        sb.sleep(0.5)
        print("✅ 账号密码填写完成")
    except Exception as e:
        sb.save_screenshot("input_error.png")
        raise RuntimeError(f"填写表单失败: {e}")

    sb.save_screenshot("before_submit.png")
    print("📤 提交登录请求...")
    click_login_button(sb)

    print("⏳ 等待登录跳转...")
    sb.sleep(10)
    sb.save_screenshot("after_login.png")

    current = sb.get_current_url()
    print(f"🔁 登录后URL：{current}")

    if "login" in current.lower():
        print("⏳ 仍在登录页，再等 10s...")
        sb.sleep(10)
        sb.save_screenshot("after_login2.png")
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

        # ── 读取服务器名称 ───────────────────────────────────
        print("🔍 读取服务器名称...")
        name = sb.execute_script("""
            (function() {
                var allEls = document.querySelectorAll('*');
                for (var i = 0; i < allEls.length; i++) {
                    var t = (allEls[i].innerText || '').trim();
                    if (t.indexOf('ID:') === 0 && t.length < 30) {
                        var parent = allEls[i].parentElement;
                        if (parent) {
                            var children = Array.from(parent.children);
                            var idx = children.indexOf(allEls[i]);
                            if (idx > 0) {
                                var n = (children[idx - 1].innerText || '').trim();
                                if (n && n.length < 50) return n;
                            }
                            var prev = parent.previousElementSibling;
                            if (prev) {
                                var n = (prev.innerText || '').trim().split('\\n')[0];
                                if (n && n.length < 50 && n.indexOf('/') === -1) return n;
                            }
                        }
                    }
                }
                return '';
            })();
        """)
        if not name:
            name = sb.execute_script("""
                (function() {
                    var blacklist = ['Dashboard','Account','Console','Files','Sign','Upgrade',
                        'Ad ','Block','Detect','FreeGame','Premium','Reload','Online','Offline'];
                    var els = document.querySelectorAll('span,p,div,h1,h2,h3,h4');
                    for (var i = 0; i < els.length; i++) {
                        var t = (els[i].innerText || '').trim();
                        if (t.length < 2 || t.length > 30) continue;
                        if (els[i].children.length > 0) continue;
                        if (/^[0-9a-f-]{8}/i.test(t)) continue;
                        if (t.indexOf('/') !== -1 || t.indexOf('@') !== -1 || t.indexOf('ID') !== -1) continue;
                        var ok = true;
                        for (var j = 0; j < blacklist.length; j++) {
                            if (t.indexOf(blacklist[j]) !== -1) { ok = false; break; }
                        }
                        if (ok) return t;
                    }
                    return '';
                })();
            """)
        result["name"] = name or server_id
        print(f"🖥 服务器名称：{result['name']}")

        time_before = read_remaining_time(sb)
        print(f"⏱ 续期前剩余时间：{time_before or '00:00:00'}")

        print("🔄 开始执行续期流程...")
        print("📡 开始监控 Turnstile Token...")

        # 先滚动到续期区域
        sb.execute_script("""
            (function() {
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    if ((btns[i].innerText || '').toUpperCase().indexOf('+8') !== -1) {
                        btns[i].scrollIntoView({behavior: 'instant', block: 'center'});
                        break;
                    }
                }
            })();
        """)
        sb.sleep(1)

        # 点击 +8 HOURS 按钮
        renew_btn_xpaths = [
            "//button[contains(., '+8 HOURS')]",
            "//button[contains(., '+8 Hours')]",
            "//button[contains(., '+8 hours')]",
            "//button[contains(translate(., 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), '+8 HOURS')]",
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

        sb.sleep(2)
        sb.save_screenshot(f"after_click_renew_{server_id}.png")

        # ── 处理 Turnstile ───────────────────────────────────
        handled = handle_turnstile(sb, timeout_wait=40, timeout_token=90)

        print("⏳ 等待续期完成...")
        sb.sleep(5)
        sb.save_screenshot(f"after_renew_{server_id}.png")

        time_after = read_remaining_time(sb)
        print(f"⏱ 续期后剩余时间：{time_after or '00:00:00'}")

        if not time_after or time_after == "00:00:00":
            print("🔄 刷新页面再确认...")
            sb.refresh()
            sb.sleep(3)
            time_after = read_remaining_time(sb)
            print(f"⏱ 刷新后剩余时间：{time_after or '00:00:00'}")

        if time_after and time_after != "00:00:00":
            result["success"]   = True
            result["remaining"] = time_after
            print(f"🎉 续期成功！剩余时间：{time_after}")
        elif handled:
            result["success"]   = True
            result["remaining"] = "（时间读取失败，但 Turnstile 已通过）"
            print("⚠️ Turnstile 已通过，续期完成，但剩余时间读取为0，请手动确认")
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
