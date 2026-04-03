import os
import time
import requests
from seleniumbase import SB

# ── 环境变量 (保持原样) ──────────────────────────────────────────
RAW_ACCOUNT = os.environ.get("FGH_ACCOUNT", "")
GOST_PROXY  = os.environ.get("GOST_PROXY", "")
TG_BOT      = os.environ.get("TG_BOT", "")

BASE_URL  = "https://panel.freegamehost.xyz"
LOGIN_URL = f"{BASE_URL}/auth/login"

# ── Telegram 推送 (保持原样) ─────────────────────────────────────
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

# ── 解析账号列表 (保持原样) ──────────────────────────────────────
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

# ── 读取页面时间 (保持原样) ────────────────────────────
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

# ── 强力清除广告 (保持原样) ─────────────────────────────────────
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
                            var src = (el.querySelector('iframe') ? el.querySelector('iframe').src : '') || '';
                            if (text.indexOf('verify') !== -1 ||
                                text.indexOf('cloudflare') !== -1 ||
                                text.indexOf('human') !== -1 ||
                                src.indexOf('cloudflare') !== -1) return;
                            el.style.display = 'none';
                        }
                    } catch(e) {}
                });
                var adSelectors = [
                    '[id*="google_ads"]', '[id*="ad-container"]',
                    '[class*="adsbygoogle"]', '[class*="ad-banner"]',
                    'ins.adsbygoogle', '[data-ad-slot]'
                ];
                adSelectors.forEach(function(sel) {
                    document.querySelectorAll(sel).forEach(function(el) {
                        el.style.display = 'none';
                    });
                });
            })();
        """)
        print("🚫 广告已清除")
    except Exception as e:
        print(f"⚠️ 广告清除异常: {e}")

# ── 等待 Turnstile Token (保持原样) ─────────────────────────────────────
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
            iframe_count = sb.execute_script("(function() { return document.querySelectorAll('iframe').length; })();")
            for i in range(iframe_count):
                try:
                    token = sb.execute_script(f"""
                        (function() {{
                            try {{
                                var iframe = document.querySelectorAll('iframe')[{i}];
                                var doc = iframe.contentDocument || iframe.contentWindow.document;
                                var el = doc.querySelector('[name="cf-turnstile-response"]');
                                return el ? el.value : '';
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

# ── 找 Turnstile iframe (保持原样) ────────────────────────────
def find_turnstile_iframe_index(sb):
    return sb.execute_script("""
        (function() {
            var iframes = document.querySelectorAll('iframe');
            for (var i = 0; i < iframes.length; i++) {
                var src = iframes[i].src || '';
                if (src.indexOf('cloudflare') !== -1 || src.indexOf('turnstile') !== -1 || src.indexOf('challenges') !== -1) {
                    return i;
                }
                try {
                    var doc = iframes[i].contentDocument || iframes[i].contentWindow.document;
                    if (!doc || !doc.body) continue;
                    var text = doc.body.innerText || '';
                    var html = doc.body.innerHTML || '';
                    if (text.indexOf('Verify') !== -1 || text.indexOf('Verifying') !== -1 || 
                        html.indexOf('cf-turnstile') !== -1 || html.indexOf('turnstile') !== -1 || 
                        doc.querySelector('[name="cf-turnstile-response"]')) {
                        return i;
                    }
                } catch(e) {}
            }
            return -1;
        })();
    """)

# ── 等待并点击 Turnstile (【优化点】：增加循环探测) ─────────────────────────────────────
def handle_turnstile(sb, timeout_wait=35, timeout_token=90):
    print(f"⏳ 正在探测验证组件 (最长等待 {timeout_wait}s)...")
    deadline = time.time() + timeout_wait
    iframe_idx = -1

    while time.time() < deadline:
        nuke_ads(sb)
        idx = find_turnstile_iframe_index(sb)
        if idx >= 0:
            iframe_idx = idx
            print(f"✅ 验证组件就绪（iframe[{idx}]）")
            break
        
        # 顺便查一下是不是已经悄悄通过了
        try:
            token = sb.execute_script("return document.querySelector('[name=\"cf-turnstile-response\"]')?.value || '';")
            if len(token) > 20: return True
        except: pass
        
        time.sleep(1.5)

    if iframe_idx < 0:
        print("ℹ️ 未检测到 Turnstile 验证组件")
        return False

    # 等 Verifying 状态结束
    print("⏳ 等待 Verifying 状态结束...")
    verifying_deadline = time.time() + 20
    while time.time() < verifying_deadline:
        still_verifying = sb.execute_script(f"""
            (function() {{
                try {{
                    var iframe = document.querySelectorAll('iframe')[{iframe_idx}];
                    var doc = iframe.contentDocument || iframe.contentWindow.document;
                    return doc.body.innerText.indexOf('Verifying') !== -1;
                }} catch(e) {{ return false; }}
            }})();
        """)
        if not still_verifying:
            print("✅ Verifying 结束，准备点击")
            break
        time.sleep(1)

    # 保持原有的坐标点击逻辑
    try:
        rect = sb.execute_script(f"var r = document.querySelectorAll('iframe')[{iframe_idx}].getBoundingClientRect(); return {{x: r.left + 25, y: r.top + r.height / 2}};")
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(sb.driver).move_by_offset(rect["x"], rect["y"]).click().move_by_offset(-rect["x"], -rect["y"]).perform()
        print("📐 坐标点击成功")
    except Exception as e:
        print(f"⚠️ 点击异常: {e}")

    return wait_for_turnstile_token(sb, timeout=timeout_token)

# ── 点击 LOGIN 按钮 (保持原样) ──────────────────────────────────────────
def click_login_button(sb):
    try:
        sb.click("//button[normalize-space(.)='LOGIN']", timeout=8, by="xpath")
        return
    except Exception: pass
    try:
        sb.click("//button[contains(.,'LOGIN') and not(contains(.,'Reload'))]", timeout=5, by="xpath")
        return
    except Exception: pass
    sb.execute_script("var btns = document.querySelectorAll('button[type=\"submit\"]'); if (btns.length > 0) btns[btns.length - 1].click();")

# ── 浏览器登录 (完全还原你的原始 JS 填写逻辑) ───────────────────────────────────────────────
def browser_login(sb, email: str, password: str):
    print("🔑 打开登录页面...")
    sb.open(LOGIN_URL)
    sb.sleep(5)
    print("✏️ 填写账号密码...")
    try:
        sb.wait_for_element_present("input", timeout=15)
        email_js    = email.replace("\\", "\\\\").replace("'", "\\'")
        password_js = password.replace("\\", "\\\\").replace("'", "\\'")
        sb.execute_script(f"""
            (function() {{
                var inputs = document.querySelectorAll('input');
                var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(inputs[0], '{email_js}');
                inputs[0].dispatchEvent(new Event('input', {{ bubbles: true }}));
                setter.call(inputs[1], '{password_js}');
                inputs[1].dispatchEvent(new Event('input', {{ bubbles: true }}));
             facility})();
        """)
        sb.sleep(0.5)
    except Exception as e: raise RuntimeError(f"填写表单失败: {e}")

    idx = find_turnstile_iframe_index(sb)
    if idx >= 0: handle_turnstile(sb)

    print("📤 提交登录请求...")
    click_login_button(sb)
    sb.sleep(10)
    if "login" in sb.get_current_url().lower():
        sb.sleep(10)
    print(f"✅ 登录成功！当前页面：{sb.get_current_url()}")

# ── 单服务器续期 (【优化点】：点击后增加 5s 物理等待) ─────────────────────────────────────────────
def renew_server(sb, server_id: str) -> dict:
    result = {"server_id": server_id, "name": server_id, "success": False, "remaining": "", "error": ""}
    server_url = f"{BASE_URL}/server/{server_id}"
    try:
        sb.open(server_url)
        sb.wait_for_element_present("body", timeout=20)
        sb.sleep(3)
        nuke_ads(sb)
        time_before = read_remaining_time(sb)

        renew_btn_xpaths = ["//button[contains(., '+8 HOURS')]", "//button[contains(., '+8 Hours')]"]
        clicked = False
        for xpath in renew_btn_xpaths:
            try:
                sb.click(xpath, timeout=5, by="xpath")
                clicked = True; break
            except: continue

        if not clicked: raise RuntimeError("找不到续期按钮")

        # 【关键改动】：点击后多等 5 秒，解决验证组件加载滞后的问题
        print("⏱ 续期按钮已点击，静默等待 5s 让验证码加载...")
        sb.sleep(5)
        sb.save_screenshot(f"after_click_renew_{server_id}.png")

        handle_turnstile(sb)

        print("⏳ 等待续期完成...")
        sb.sleep(6)
        time_after = read_remaining_time(sb)
        if not time_after or time_after == "00:00:00":
            sb.refresh(); sb.sleep(4); time_after = read_remaining_time(sb)

        if time_after and time_after != "00:00:00":
            result["success"] = True; result["remaining"] = time_after
        else: raise RuntimeError("续期未生效")

    except Exception as e:
        result["error"] = str(e); sb.save_screenshot(f"error_{server_id}.png")
    return result

# ── 汇总及入口 (保持原样) ─────────────────────────────────────────────────
def process_account(account: dict):
    proxy_str = "http://127.0.0.1:8080" if GOST_PROXY else None
    sb_kwargs = dict(uc=True, headless=True)
    if proxy_str: sb_kwargs["proxy"] = proxy_str
    
    results = []
    with SB(**sb_kwargs) as sb:
        browser_login(sb, account["email"], account["password"])
        for sid in account["server_ids"]:
            results.append(renew_server(sb, sid))
            sb.sleep(2)
    return results

def main():
    accounts = parse_accounts()
    all_results = []
    for acc in accounts:
        try:
            all_results.extend(process_account(acc))
        except Exception as e:
            all_results.append({"server_id": "ACC", "name": acc["email"], "success": False, "error": str(e)})
    tg_send("\n".join(["<b>🎮 FGH 报告</b>"] + [f'{"✅" if r["success"] else "❌"} {r["name"]}: {r["remaining"] or r["error"][:50]}' for r in all_results]))

if __name__ == "__main__":
    main()
