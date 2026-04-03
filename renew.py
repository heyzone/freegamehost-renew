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

# ── 强力清除广告（只执行一次）───────────────────────────────
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
                var adSelectors = [
                    '[id*="google_ads"]','[id*="ad-container"]',
                    '[class*="adsbygoogle"]','[class*="ad-banner"]',
                    'ins.adsbygoogle','[data-ad-slot]'
                ];
                adSelectors.forEach(function(sel) {
                    document.querySelectorAll(sel).forEach(function(el) {
                        el.style.display = 'none';
                    });
                });
            })();
        """)
    except Exception:
        pass

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

# ── 获取 Turnstile iframe 的屏幕坐标 ────────────────────────
def get_turnstile_screen_coords(sb):
    """
    通过 selenium 获取 iframe 在页面中的位置，
    结合浏览器窗口位置得到屏幕绝对坐标
    """
    try:
        # 找所有 iframe，取位置在右下区域的那个（Turnstile 通常在右下角）
        result = sb.execute_script("""
            (function() {
                var iframes = document.querySelectorAll('iframe');
                var candidates = [];
                for (var i = 0; i < iframes.length; i++) {
                    var r = iframes[i].getBoundingClientRect();
                    // Turnstile iframe 尺寸约 300x65，排除过大的广告 iframe
                    if (r.width > 50 && r.width < 500 && r.height > 30 && r.height < 200
                        && r.top > 0 && r.left > 0) {
                        candidates.push({
                            index: i,
                            x: r.left + 20,
                            y: r.top + r.height / 2,
                            width: r.width,
                            height: r.height
                        });
                    }
                }
                return candidates;
            })();
        """)
        if result:
            print(f"🔍 候选 Turnstile iframe：{result}")
            return result
    except Exception as e:
        print(f"⚠️ 获取 iframe 坐标异常: {e}")
    return []

# ── 等待 Verifying 结束，然后用坐标点击 Turnstile ───────────
def handle_turnstile(sb, timeout_wait=40, timeout_token=90):
    print("⏳ 等待 Turnstile 验证组件出现（最多40s）...")

    # 等待页面出现 Turnstile 相关内容
    deadline = time.time() + timeout_wait
    turnstile_appeared = False
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

        # 检查 iframe 数量（点击续期按钮后会新增 Turnstile iframe）
        try:
            count = sb.execute_script(
                "(function() { return document.querySelectorAll('iframe').length; })();"
            )
            if count > 0:
                # 找到小尺寸 iframe（Turnstile 特征）
                candidates = get_turnstile_screen_coords(sb)
                if candidates:
                    turnstile_appeared = True
                    print(f"✅ 检测到 Turnstile 候选 iframe（共{len(candidates)}个）")
                    break
        except Exception:
            pass

        time.sleep(1)

    if not turnstile_appeared:
        print("ℹ️ 未检测到 Turnstile 验证组件，跳过")
        return False

    # 等待 Verifying 状态结束（检查 iframe 内是否有复选框文字出现）
    print("⏳ 等待 Verifying 状态结束...")
    verifying_deadline = time.time() + 30
    while time.time() < verifying_deadline:
        # 先检查是否已自动通过
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

        # 用 scrot 截图看当前状态（仅调试用，不影响逻辑）
        time.sleep(1)

        # 通过 iframe 尺寸变化判断 Verifying 是否结束
        # Verifying 时 iframe 较高，复选框出现后 iframe 变小
        candidates = get_turnstile_screen_coords(sb)
        if candidates:
            # 取最小的那个（最可能是 Turnstile）
            smallest = min(candidates, key=lambda c: c["width"] * c["height"])
            # Turnstile 复选框 iframe 高度约 65px，Verifying 时约 100px+
            if smallest["height"] < 100:
                print(f"✅ Verifying 结束，复选框已就绪（高度={smallest['height']:.0f}px）")
                break
        time.sleep(1)
    else:
        print("⚠️ Verifying 等待超时，强行点击")

    # 清广告
    nuke_ads(sb)
    sb.sleep(0.5)

    # 重新获取坐标
    candidates = get_turnstile_screen_coords(sb)
    if not candidates:
        print("⚠️ 点击前找不到 iframe 坐标，尝试直接轮询 token")
        try:
            wait_for_turnstile_token(sb, timeout=30)
            return True
        except Exception:
            return False

    # 取最小的 iframe（Turnstile 复选框）
    target = min(candidates, key=lambda c: c["width"] * c["height"])
    page_x = target["x"]
    page_y = target["y"]

    # 滚动到目标位置
    sb.execute_script(f"window.scrollTo(0, {max(0, page_y - 300)});")
    sb.sleep(0.5)

    # 重新获取坐标（滚动后重新计算）
    candidates = get_turnstile_screen_coords(sb)
    if candidates:
        target = min(candidates, key=lambda c: c["width"] * c["height"])
        page_x = target["x"]
        page_y = target["y"]

    print(f"📐 坐标计算完成：页面坐标 x={page_x:.0f}, y={page_y:.0f}")

    # 用 ActionChains 点击
    try:
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(sb.driver) \
            .move_by_offset(page_x, page_y) \
            .click() \
            .move_by_offset(-page_x, -page_y) \
            .perform()
        print("📐 坐标点击成功")
    except Exception as e:
        print(f"⚠️ ActionChains 点击失败: {e}，尝试 JS 点击...")
        try:
            sb.execute_script(f"""
                (function() {{
                    var el = document.elementFromPoint({page_x}, {page_y});
                    if (el) el.click();
                }})();
            """)
            print("📐 JS 点击完成")
        except Exception as e2:
            print(f"⚠️ JS 点击也失败: {e2}")

    # 等待 token
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
        nuke_ads(sb)
        sb.sleep(1)
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

        # ── 续期前读取剩余时间 ───────────────────────────────
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

        print("⏳ 等待 Turnstile 验证组件...")
        sb.sleep(2)
        sb.save_screenshot(f"after_click_renew_{server_id}.png")

        # ── 处理 Turnstile ───────────────────────────────────
        handled = handle_turnstile(sb, timeout_wait=40, timeout_token=90)

        print("⏳ 等待续期完成...")
        sb.sleep(5)
        sb.save_screenshot(f"after_renew_{server_id}.png")

        # ── 续期后读取剩余时间 ───────────────────────────────
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
