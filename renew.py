import os
import time
import random
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

# ── 单次登录尝试 ─────────────────────────────────────────────
def try_login(sb, email: str, password: str) -> bool:
    print("🔑 打开登录页面...")
    sb.open(LOGIN_URL)
    # 多等几秒让 reCAPTCHA v3 隐式完成
    sb.sleep(random.uniform(5, 8))
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
                inputs[0].dispatchEvent(new Event('change', {{ bubbles: true }}));
                setter.call(inputs[1], '{password_js}');
                inputs[1].dispatchEvent(new Event('input', {{ bubbles: true }}));
                inputs[1].dispatchEvent(new Event('change', {{ bubbles: true }}));
            }})();
        """)
        # 模拟人工停留
        sb.sleep(random.uniform(1.5, 3))
        print("✅ 账号密码填写完成")
    except Exception as e:
        sb.save_screenshot("input_error.png")
        print(f"❌ 填写表单失败: {e}")
        return False

    sb.save_screenshot("before_submit.png")

    # 直接提交，依赖 reCAPTCHA v3 隐式验证
    # 不调用 uc_gui_handle_captcha，避免触发图片验证
    print("📤 提交登录请求...")
    try:
        sb.click("//button[normalize-space(.)='LOGIN']", timeout=8, by="xpath")
    except Exception:
        try:
            sb.click("//button[contains(.,'LOGIN') and not(contains(.,'Reload'))]",
                     timeout=5, by="xpath")
        except Exception:
            try:
                sb.click("button[type='submit']:last-of-type", timeout=5)
            except Exception:
                sb.execute_script("""
                    (function() {
                        var btns = document.querySelectorAll('button[type="submit"]');
                        if (btns.length > 0) btns[btns.length - 1].click();
                    })();
                """)

    # 等待跳转
    sb.sleep(random.uniform(8, 12))
    sb.save_screenshot("after_login.png")

    current = sb.get_current_url()
    print(f"🔁 登录后URL：{current}")

    if "login" in current.lower():
        # 检查是否有图片验证弹出
        has_captcha_img = sb.execute_script("""
            (function() {
                return !!document.querySelector('iframe[src*="recaptcha/api2/bframe"]') ||
                       !!document.querySelector('.rc-imageselect');
            })();
        """)
        if has_captcha_img:
            print("🖼️ 检测到 reCAPTCHA 图片验证，尝试 uc_gui_handle_captcha...")
            try:
                sb.uc_gui_handle_captcha()
                print("✅ uc_gui_handle_captcha 完成")
                sb.sleep(5)
                current = sb.get_current_url()
                print(f"🔁 验证后URL：{current}")
            except Exception as e:
                print(f"⚠️ uc_gui_handle_captcha 异常: {e}")

    return "login" not in sb.get_current_url().lower()

# ── 浏览器登录（最多重试3次）────────────────────────────────
def browser_login(sb, email: str, password: str):
    for attempt in range(1, 4):
        print(f"\n🔐 登录尝试 {attempt}/3...")
        if try_login(sb, email, password):
            print(f"✅ 登录成功！当前页面：{sb.get_current_url()}")
            return
        else:
            sb.save_screenshot(f"login_failed_{attempt}.png")
            print(f"❌ 第 {attempt} 次登录失败")
            if attempt < 3:
                sb.sleep(random.uniform(4, 8))

    raise RuntimeError("❌ 3次登录均失败")

# ── 单服务器续期 ─────────────────────────────────────────────
def renew_server(sb, server_id: str) -> dict:
    result = {"server_id": server_id, "name": server_id, "success": False, "remaining": "", "error": ""}
    server_url = f"{BASE_URL}/server/{server_id}"
    try:
        print(f"🔗 导航到服务器页面：{server_url}")
        sb.open(server_url)
        print("⏳ 等待服务器页面加载...")
        sb.wait_for_element_present("body", timeout=20)
        sb.sleep(random.uniform(3, 5))
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

        # 滚动到续期按钮
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
        sb.sleep(random.uniform(0.8, 1.5))

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
            raise RuntimeError("找不到续期按钮")

        sb.sleep(2)
        sb.save_screenshot(f"after_click_renew_{server_id}.png")

        # ── 处理续期 Turnstile ───────────────────────────────
        print("⏳ 等待 Turnstile 验证组件出现（最多20s）...")
        turnstile_found = False
        for _ in range(20):
            # 先检查 token 是否已有
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
                    print(f"✅ Turnstile 已自动通过！")
                    turnstile_found = True
                    break
            except Exception:
                pass

            exists = sb.execute_script("""
                (function() {
                    return !!document.querySelector('[class*="TurnstileBox"]') ||
                           !!document.querySelector('[name="cf-turnstile-response"]');
                })();
            """)
            if exists:
                print("✅ 验证组件就绪")
                turnstile_found = True
                break
            time.sleep(1)

        if turnstile_found:
            # 等待 Verifying 结束（最多25s）
            print("⏳ 等待 Verifying 状态结束...")
            for i in range(25):
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
                        print(f"✅ Turnstile 已自动通过！")
                        break
                except Exception:
                    pass

                verifying = sb.execute_script("""
                    (function() {
                        var box = document.querySelector('[class*="TurnstileBox"]');
                        if (!box) return false;
                        return box.innerText.indexOf('Verifying') !== -1;
                    })();
                """)
                if not verifying:
                    print("✅ Verifying 结束，准备点击复选框...")
                    break
                if i % 4 == 0:
                    print(f"⏳ 仍在 Verifying... ({i*2}s)")
                time.sleep(2)

            sb.sleep(1)
            sb.save_screenshot(f"before_captcha_click_{server_id}.png")

            # 用 uc_gui_click_captcha 点击 Turnstile 复选框
            print("🖱️ 调用 uc_gui_click_captcha 点击 Turnstile 复选框...")
            try:
                sb.uc_gui_click_captcha()
                print("✅ uc_gui_click_captcha 完成")
            except Exception as e:
                print(f"⚠️ uc_gui_click_captcha 异常: {e}")
                try:
                    sb.uc_gui_handle_captcha()
                    print("✅ uc_gui_handle_captcha 备用完成")
                except Exception as e2:
                    print(f"⚠️ 备用也失败: {e2}")

            # 等待 token
            wait_for_turnstile_token(sb, timeout=90)
        else:
            print("ℹ️ 未检测到 Turnstile，续期可能直接完成")

        print("⏳ 等待续期完成...")
        sb.sleep(random.uniform(4, 6))
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

    sb_kwargs = dict(uc=True, xvfb=True)
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
            sb.sleep(random.uniform(2, 4))

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
