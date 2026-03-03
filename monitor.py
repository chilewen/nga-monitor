import requests
import re
import os
import time

# ================= 配置区域 =================
# BARK_KEY 现在从 GitHub Secrets 中读取，不再硬编码
# 请确保在 GitHub Settings -> Secrets 中设置了 BARK_KEY 和 NGA_COOKIE

# --- NGA 配置 ---
NGA_ENABLE = True
NGA_UID = "26529713"
NGA_RECORD_FILE = "nga_last_record.txt"
NGA_URL_TEMPLATE = "https://bbs.nga.cn/thread.php?searchpost=1&authorid={}"

# --- 虎扑配置 ---
HUPU_ENABLE = True
HUPU_POST_URL = "https://bbs.hupu.com/636748637.html"
HUPU_TARGET_UID = "20829162237257"
HUPU_RECORD_FILE = "hupu_last_record.txt"
HUPU_MAX_PAGES = 5
# =======================================================

def get_bark_key():
    key = os.getenv("BARK_KEY")
    if not key:
        print("[错误] 未找到 BARK_KEY 环境变量！请在 GitHub Secrets 中设置 BARK_KEY。")
        return None
    # 自动去除首尾空格，防止复制时带入了多余字符
    return key.strip()

def send_bark_notification(title, content, jump_url, group_name):
    bark_key = get_bark_key()
    if not bark_key:
        print(f"[Push 跳过] 因缺少 BARK_KEY，无法推送: {title}")
        return

    url = f"{bark_key}/{title}"
    params = {
        "body": content,
        "url": jump_url,
        "sound": "minuet",
        "isArchive": "1",
        "group": group_name
    }
    try:
        resp = requests.get(url, params=params, timeout=5)
        # Bark 成功通常返回 {"code":200, ...}
        if resp.status_code == 200:
            print(f"[Push 成功] {title}")
        else:
            print(f"[Push 失败] 状态码: {resp.status_code}, 内容: {resp.text}")
    except Exception as e:
        print(f"[Push 错误] {e}")

def check_nga():
    if not NGA_ENABLE:
        return
    
    print("--- 开始检查 NGA (带 Cookie 模式) ---")
    url = NGA_URL_TEMPLATE.format(NGA_UID)
    
    nga_cookie = os.getenv("NGA_COOKIE")
    
    # 构造更真实的 Headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    
    if nga_cookie:
        headers["Cookie"] = nga_cookie
        # 关键：NGA 有时需要 Referer 才能正常返回搜索页
        headers["Referer"] = "https://bbs.nga.cn/"
        print("NGA: 已加载 Cookie")
    else:
        print("NGA: 警告！未检测到 NGA_COOKIE，必定无法获取数据。")
        return

    try:
        # 创建一个 Session 来自动处理 gzip 和保持连接
        session = requests.Session()
        resp = session.get(url, headers=headers, timeout=15)
        
        # 强制解码为 utf-8，防止乱码导致匹配失败
        resp.encoding = 'utf-8' 
        content = resp.text
        
        # === 调试诊断区域 ===
        debug_info = False
        
        # 1. 检查是否被重定向到登录页
        if "login.php" in resp.url or "ngabbs.com/login" in resp.url:
            print("[错误] 请求被重定向到登录页！Cookie 可能已失效。")
            debug_info = True
            
        # 2. 检查页面内容是否包含典型的“未登录”或“无权限”提示
        if "您没有权限访问" in content or "需要先登录" in content or "guest_js" not in content:
             # guest_js 是 NGA 登录后页面通常包含的一个变量，如果没有，大概率是游客
            print("[错误] 页面内容显示未登录或无权限。")
            debug_info = True
            
        # 3. 如果没找到任何 read.php 链接，也视为异常
        if "read.php?tid=" not in content:
            print("[警告] 页面中未找到任何帖子链接 (read.php?tid=)。")
            debug_info = True

        # 如果触发上述任一异常，打印部分 HTML 以便排查
        if debug_info:
            print("-" * 30)
            print("【服务器返回的 HTML 片段 (前 1500 字):】")
            # 清理一下换行符方便看
            clean_content = content.replace('\n', '').replace('\r', '')
            print(clean_content[:1500])
            print("\n... (内容截断)")
            print("-" * 30)
            print("建议：请复制浏览器中最新的 Cookie 替换 GitHub Secret。")
            return # 既然页面都不对，就不继续解析了
        # === 调试诊断结束 ===

        # 正常解析逻辑
        pattern = r'<a\s+href="read\.php\?tid=(\d+)[^"]*"[^>]*>(.*?)</a>'
        matches = re.findall(pattern, content, re.DOTALL)
        
        if not matches:
            # 如果上面没报错，但这里还是没匹配到，可能是正则太严格或页面结构变了
            print("NGA: 正则未匹配到帖子。尝试更宽松的匹配...")
            # 备用宽松正则
            pattern_loose = r'read\.php\?tid=(\d+)'
            matches_loose = re.findall(pattern_loose, content)
            if matches_loose:
                print(f"宽松匹配找到 TID: {matches_loose[0]}，但无法提取标题。可能是页面结构变化。")
            else:
                print("NGA: 确实未解析到任何帖子。")
            return

        tid = matches[0][0]
        raw_title = matches[0][1]
        title = re.sub(r'<[^>]+>', '', raw_title).strip()
        if len(title) > 40:
            title = title[:40] + "..."
            
        # 提取时间逻辑 (保持不变)
        tid_pos = content.find(f'tid={tid}')
        r_time = "未知时间"
        if tid_pos != -1:
            snippet = content[tid_pos : tid_pos + 600]
            time_match = re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}-\d{2})\s+(\d{1,2}:\d{2})', snippet)
            if time_match:
                r_time = f"{time_match.group(1)} {time_match.group(2)}"
            elif "刚刚" in snippet:
                r_time = "刚刚"

        current_record = f"{tid}|{r_time}"
        
        last_record = ""
        if os.path.exists(NGA_RECORD_FILE):
            with open(NGA_RECORD_FILE, "r", encoding="utf-8") as f:
                last_record = f.read().strip()
        
        print(f"NGA 最新: TID={tid}, 标题={title}, 时间={r_time}")

        if current_record != last_record:
            msg = f"NGA 用户 ({NGA_UID}) 有新动态\n帖子: {title}\n时间: {r_time}"
            send_bark_notification("💬 NGA 新动态", msg, f"https://bbs.nga.cn/read.php?tid={tid}", "NGA监控")
            
            with open(NGA_RECORD_FILE, "w", encoding="utf-8") as f:
                f.write(current_record)
            print("NGA: 已推送并更新")
        else:
            print("NGA: 无新动态")
            
    except Exception as e:
        print(f"NGA 发生异常: {e}")
        import traceback
        traceback.print_exc()

def check_hupu():
    if not HUPU_ENABLE:
        return

    print("--- 开始检查 虎扑 ---")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://bbs.hupu.com/"
    }
    
    found_reply = False
    latest_info = None
    
    for page in range(1, HUPU_MAX_PAGES + 1):
        url = f"{HUPU_POST_URL}?page={page}"
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue
            
            content = resp.text
            target_patterns = [
                f'href="/home/{HUPU_TARGET_UID}"',
                f'data-userid="{HUPU_TARGET_UID}"'
            ]
            
            match_positions = []
            for pat in target_patterns:
                for m in re.finditer(pat, content):
                    match_positions.append(m.start())
            
            if match_positions:
                last_pos_in_page = match_positions[-1]
                snippet = content[last_pos_in_page : last_pos_in_page + 800]
                
                time_match = re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}-\d{2}|今天|昨天)\s+(\d{1,2}:\d{2})', snippet)
                h_time = "未知时间"
                if time_match:
                    h_time = f"{time_match.group(1)} {time_match.group(2)}"
                
                text_fp = re.sub(r'<[^>]+>', ' ', snippet).strip()
                text_fp = text_fp[:40].replace('\n', '').strip()
                
                latest_info = {
                    "page": page,
                    "time": h_time,
                    "fingerprint": text_fp,
                }
                found_reply = True
                print(f"虎扑 Page {page}: 找到目标用户回复")
            else:
                print(f"虎扑 Page {page}: 未找到目标用户")
                
        except Exception as e:
            print(f"虎扑 Page {page} 错误: {e}")
    
    if not found_reply:
        print(f"虎扑: 在前 {HUPU_MAX_PAGES} 页均未找到该用户的回复")
        return

    info = latest_info
    current_record = f"{info['time']}|{info['fingerprint']}"
    
    last_record = ""
    if os.path.exists(HUPU_RECORD_FILE):
        with open(HUPU_RECORD_FILE, "r", encoding="utf-8") as f:
            last_record = f.read().strip()
    
    print(f"虎扑最新: 时间={info['time']}, 指纹={info['fingerprint']} (来自第 {info['page']} 页)")
    
    if current_record != last_record:
        msg = f"虎扑目标用户 ({HUPU_TARGET_UID[-6:]}) 有新回复!\n时间: {info['time']}\n内容: {info['fingerprint']}"
        send_bark_notification("🏀 虎扑新回复", msg, HUPU_POST_URL, "虎扑监控")
        
        with open(HUPU_RECORD_FILE, "w", encoding="utf-8") as f:
            f.write(current_record)
        print("虎扑: 已推送并更新")
    else:
        print("虎扑: 无新回复")

def main():
    # 检查关键配置是否存在
    if not os.getenv("BARK_KEY"):
        print("========================================")
        print("警告：未在 GitHub Secrets 中找到 BARK_KEY")
        print("请在 Settings -> Secrets -> Actions 中添加 BARK_KEY")
        print("========================================")
    
    if NGA_ENABLE and not os.getenv("NGA_COOKIE"):
        print("========================================")
        print("警告：未在 GitHub Secrets 中找到 NGA_COOKIE")
        print("NGA 监控可能失败，请添加 NGA_COOKIE")
        print("========================================")

    check_nga()
    time.sleep(3) 
    check_hupu()

if __name__ == "__main__":
    main()
