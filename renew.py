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

# ── 打印所有 iframe 详情（调试用）────────────────────────────
def debug_iframes(sb, label=""):
    try:
        info = sb.execute_script("""
            (function() {
                var iframes = document.querySelectorAll('iframe');
                var result = [];
                for (var i = 0; i < iframes.length; i++) {
                    var r = iframes[i].getBoundingClientRect();
                    result.push({
                        index: i,
                        src: (iframes[i].src || '').substring(0, 80),
                        x: Math.round(r.left),
                        y: Math.round(r.top),
                        w: Math.round(r.width),
                        h: Math.round(r.height),
                        visible: r.width > 0 && r.height > 0
                    });
                }
                return result;
            })();
        """)
        print(f"🔍 [{label}] 页面 iframe 列表（共{len(info)}个）：")
        for f in info:
            print(f"   iframe[{f['index']}] src={f['src']!r} pos=({f['x']},{f['y']}) size={f['w']}x{f['h']} visible={f['visible']}")
    except Exception as e:
        print(f"⚠️ debug_iframes 异常: {e}")

# ── 等待 Turnstile Token ─────────────────────────────────────
def wait_for_turnstile_token(sb, timeout=90):
    print("📡 开始监控 Turnstile Token...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            token = sb.execute_script("""
                (function() {
                    var el = document.querySelector('[name="cf-turnstile-response"]');
                    return el ? el.value : '';
                })();
            """)
            if token and len(token) > 20:
                print(f"✅ Cloudflare Turnstile 验证通过！token：{token[:60]}...")
                return token
        except Exception:
            pass
        try:
            count = sb.execute_script(
                "(function() { return document.querySelectorAll('iframe').length; })();"
            )
            for i in range(count):
                try:
                    token = sb.execute_script(f"""
                        (function() {{
                            try {{
                                var f = document.querySelectorAll('iframe')[{i}];
                                var d = f.contentDocument || f.contentWindow.document;
                                var e = d.querySelector('[name="cf-turnstile-response"]');
                                return e ? e.value : '';
                            }} catch(e) {{ return ''; }}
                        }})();
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

# ── 强力清除广告 ─────────────────────────────────────────────
def nuke_ads(sb):
    try:
        sb.execute_script("""
            (function() {
                document.querySelectorAll('*').forEach(function(el) {
                    try {
                        var style = window.getComputedStyle(el);
                        var pos = style.position;
                        var zi = parseInt(style.zIndex) || 0;
                        if ((pos === 'fixed' || pos === 'absolute') && zi > 100) {
                            var text = (el.innerText || '').toLowerCase();
                            if (text.indexOf('verify') !== -1 ||
                                text.indexOf('cloudflare') !== -1 ||
                                text.indexOf('human') !== -1) return;
                            el.style.display = 'none';
                        }
                    } catch(e) {}
                });
            })();
        """)
    except Exception:
        pass

# ── 处理续期 Turnstile ───────────────────────────────────────
def handle_turnstile(sb, timeout_wait=40, timeout_token=90):
    print("⏳ 等待 Turnstile 验证组件出现（最多40s）...")
    deadline = time.time() + timeout_wait

    while time.time() < deadline:
        # 检查是否已自动通过
        try:
            token = sb.execute_script("""
                (function() {
                    var el = document.querySelector('[name="cf-turnstile-response"]');
                    return el ? el.value : '';
                })();
            """)
            if token and len(token) > 20:
                print(f"✅ Turnstile 已自动通过！token：{token[:60]}...")
                return True
        except Exception:
            pass

        # 打印当前所有 iframe 信息（每5秒打印一次）
        elapsed = timeout_wait - (deadline - time.time())
        if int(elapsed) % 5 == 0:
            debug_iframes(sb, f"等待中 {int(elapsed)}s")

        time.sleep(1)

    # 超时后打印最终 iframe 状态
    print("⏰ 等待超时，打印最终 iframe 状态：")
    debug_iframes(sb, "超时")

    # 最后尝试：不管什么 iframe，只要有就点
    print("🔄 最后尝试：点击所有可见 iframe...")
    try:
        info = sb.execute_script("""
            (function() {
                var iframes = document.querySelectorAll('iframe');
                var result = [];
                for (var i = 0; i < iframes.length; i++) {
                    var r = iframes[i].getBoundingClientRect();
                    if (r.width > 0 && r.height > 0 && r.top > 300) {
                        result.push({index: i, x: r.left + 20, y: r.top + r.height/2});
                    }
                }
                return result;
            })();
        """)
        for f in info:
            print(f"   点击 iframe[{f['index']}] at ({f['x']:.0f}, {f['y']:.0f})")
            nuke_ads(sb)
            sb.sleep(0.3)
            from selenium.webdriver.common.action_chains import ActionChains
            ActionChains(sb.driver) \
                .move_by_offset(f["x"], f["y"]) \
                .click() \
                .move_by_offset(-f["x"], -f["y"]) \
                .perform()
            sb.sleep(1)
            # 检查是否通过
            token = sb.execute_script("""
                (function() {
                    var el = document.querySelector('[name="cf-turnstile-response"]');
                    return el ? el.value : '';
                })();
            """)
            if token and len(token) > 20:
                print(f"✅ 点击后 Turnstile 通过！")
                return True
    except Exception as e:
        print(f"⚠️ 最后尝试点击异常: {e}")

    print("ℹ️ Turnstile 处理完毕，等待 token...")
    try:
        wait_for_turnstile_token(sb, timeout=30)
        return True
    except Exception:
        return False

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
        nuke_ads(sb)
        sb.sleep(1)
        print("✅ 服务器页面加载完成")

        # 打印初始 iframe 状态
        debug_iframes(sb, "页面加载后")

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

        # 点击后等2s再打印 iframe 状态
        sb.sleep(2)
        debug_iframes(sb, "点击续期后2s")
        sb.save_screenshot(f"after_click_renew_{server_id}.png")

        # 再等5s打印一次（Verifying 结束后）
        sb.sleep(5)
        debug_iframes(sb, "点击续期后7s")
        sb.save_screenshot(f"after_click_renew2_{server_id}.png")

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
