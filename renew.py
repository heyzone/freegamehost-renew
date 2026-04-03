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
    except Exception:
        pass

# ── 等待 Turnstile Token ─────────────────────────────────────
def wait_for_turnstile_token(sb, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            token = sb.execute_script("""
                (function() {
                    var el = document.querySelector('[name="cf-turnstile-response"]');
                    if (el && el.value.length > 20) return el.value;
                    var iframes = document.querySelectorAll('iframe');
                    for (var i = 0; i < iframes.length; i++) {
                        try {
                            var doc = iframes[i].contentDocument || iframes[i].contentWindow.document;
                            var res = doc.querySelector('[name="cf-turnstile-response"]');
                            if (res && res.value.length > 20) return res.value;
                        } catch(e) {}
                    }
                    return '';
                })();
            """)
            if token:
                print(f"✅ Cloudflare 验证通过！Token 长度: {len(token)}")
                return token
        except Exception:
            pass
        time.sleep(1.5)
    return ""

# ── 找 Turnstile iframe ─────────────────────────────────────
def find_turnstile_iframe_index(sb):
    return sb.execute_script("""
        (function() {
            var iframes = document.querySelectorAll('iframe');
            for (var i = 0; i < iframes.length; i++) {
                var src = iframes[i].src || '';
                if (src.indexOf('cloudflare') !== -1 || src.indexOf('challenges') !== -1) return i;
                try {
                    var doc = iframes[i].contentDocument || iframes[i].contentWindow.document;
                    if (doc.querySelector('[name="cf-turnstile-response"]') || doc.body.innerText.includes('Verify')) return i;
                } catch(e) {}
            }
            return -1;
        })();
    """)

# ── 增强型处理 Turnstile ─────────────────────────────────────
def handle_turnstile(sb, timeout_wait=45):
    print(f"⏳ 正在探测验证组件 (最长等待 {timeout_wait}s)...")
    deadline = time.time() + timeout_wait
    
    while time.time() < deadline:
        nuke_ads(sb)
        
        # 1. 检查是否已经自动出 Token
        if wait_for_turnstile_token(sb, timeout=1): return True

        # 2. 探测 iframe
        idx = find_turnstile_iframe_index(sb)
        if idx >= 0:
            print(f"✅ 捕获到验证组件 iframe[{idx}]")
            
            # 检查 Verifying 状态
            is_verifying = sb.execute_script(f"""
                (function() {{
                    try {{
                        var f = document.querySelectorAll('iframe')[{idx}];
                        var doc = f.contentDocument || f.contentWindow.document;
                        return doc.body.innerText.includes('Verifying');
                    }} catch(e) {{ return false; }}
                }})();
            """)
            
            if not is_verifying:
                print("☝️ 验证框处于可点击状态，尝试模拟交互...")
                try:
                    # 尝试切换进去点击 Mark 元素 (Turnstile 标准元素)
                    sb.switch_to_frame(f"iframe:nth-of-type({idx + 1})")
                    sb.click("span.mark", timeout=3)
                    sb.switch_to_parent_frame()
                except:
                    sb.switch_to_parent_frame()
                    # 备选：使用你原来的坐标点击逻辑逻辑
                    rect = sb.execute_script(f"var r = document.querySelectorAll('iframe')[{idx}].getBoundingClientRect(); return {{x: r.left + 30, y: r.top + r.height/2}};")
                    from selenium.webdriver.common.action_chains import ActionChains
                    ActionChains(sb.driver).move_by_offset(rect["x"], rect["y"]).click().move_by_offset(-rect["x"], -rect["y"]).perform()
            
            # 点击后给一点缓冲再次检查 Token
            if wait_for_turnstile_token(sb, timeout=15): return True

        time.sleep(2)
    
    print("ℹ️ 未能完成验证，可能由于加载超时或无需验证")
    return False

# ── 点击 LOGIN 按钮 ──────────────────────────────────────────
def click_login_button(sb):
    try:
        sb.click("//button[normalize-space(.)='LOGIN']", timeout=8, by="xpath")
        return
    except: pass
    sb.execute_script("var b = document.querySelectorAll('button[type=\"submit\"]'); if(b.length) b[b.length-1].click();")

# ── 浏览器登录 ───────────────────────────────────────────────
def browser_login(sb, email: str, password: str):
    print("🔑 打开登录页面...")
    sb.open(LOGIN_URL)
    sb.sleep(5)
    
    # 登录页前置过盾
    handle_turnstile(sb, timeout_wait=15)

    print("✏️ 填写账号密码...")
    sb.type('input[type="email"]', email)
    sb.type('input[type="password"]', password)
    sb.sleep(1)
    
    print("📤 提交登录...")
    click_login_button(sb)
    sb.sleep(10)

    if "login" in sb.get_current_url().lower():
        print("🛡️ 登录后仍在原页面，尝试再次处理验证...")
        handle_turnstile(sb, timeout_wait=10)
        click_login_button(sb)
        sb.sleep(8)

# ── 单服务器续期 ─────────────────────────────────────────────
def renew_server(sb, server_id: str) -> dict:
    result = {"server_id": server_id, "name": server_id, "success": False, "remaining": "", "error": ""}
    server_url = f"{BASE_URL}/server/{server_id}"
    try:
        print(f"🔗 导航到服务器页面：{server_url}")
        sb.open(server_url)
        sb.wait_for_element_present("body", timeout=20)
        sb.sleep(3)
        nuke_ads(sb)
        
        # 读取名称和旧时间
        time_before = read_remaining_time(sb)
        print(f"⏱ 续期前剩余时间：{time_before or '00:00:00'}")

        # 续期按钮 XPATH 列表
        renew_btn_xpaths = ["//button[contains(., '+8')]", "//button[contains(translate(., 'h', 'H'), '+8 HOURS')]"]
        clicked = False
        for xpath in renew_btn_xpaths:
            try:
                sb.click(xpath, timeout=5, by="xpath")
                clicked = True
                print("🔄 +8 HOURS 续期按钮已点击")
                break
            except: continue

        if not clicked:
            raise RuntimeError("找不到续期按钮")

        # 【核心改进】给验证码生成留出充足时间
        print("⏱ 等待验证组件异步加载 (5s)...")
        sb.sleep(5)
        sb.save_screenshot(f"before_verify_{server_id}.png")

        # 处理验证
        handle_turnstile(sb)

        print("⏳ 等待服务端状态更新 (8s)...")
        sb.sleep(8)
        sb.save_screenshot(f"after_verify_{server_id}.png")

        # 确认结果
        time_after = read_remaining_time(sb)
        if not time_after or time_after == "00:00:00":
            sb.refresh()
            sb.sleep(5)
            time_after = read_remaining_time(sb)

        if time_after and time_after != "00:00:00" and time_after != time_before:
            result["success"] = True
            result["remaining"] = time_after
            print(f"🎉 续期成功！剩余时间：{time_after}")
        else:
            raise RuntimeError(f"续期未生效，当前时间：{time_after}")

    except Exception as e:
        result["error"] = str(e)
        print(f"❌ 续期失败 [{server_id}]: {e}")
        sb.save_screenshot(f"error_{server_id}.png")

    return result

# ── 单账号主流程 ─────────────────────────────────────────────
def process_account(account: dict):
    proxy_str = "http://127.0.0.1:8080" if GOST_PROXY else None
    results = []
    
    # 保持 UC 模式，这对过 Cloudflare 至关重要
    with SB(uc=True, headless=True, proxy=proxy_str) as sb:
        browser_login(sb, account["email"], account["password"])
        for sid in account["server_ids"]:
            results.append(renew_server(sb, sid))
            sb.sleep(2)
    return results

# ── 推送及入口保持不变 ────────────────────────────────────────
def build_tg_message(all_results: list) -> str:
    lines = ["<b>🎮 FGH 续期报告</b>"]
    for r in all_results:
        status = "✅" if r.get("success") else "❌"
        lines.append(f'{status} <b>{r.get("name")}</b> ({r.get("server_id")})\n   ⏱ 剩余：{r.get("remaining") or "N/A"}')
    return "\n".join(lines)

def main():
    accounts = parse_accounts()
    if not accounts: return
    all_results = []
    for acc in accounts:
        try:
            all_results.extend(process_account(acc))
        except Exception as e:
            all_results.append({"server_id": "ACC", "name": acc["email"], "success": False, "error": str(e)})
    tg_send(build_tg_message(all_results))

if __name__ == "__main__":
    main()
