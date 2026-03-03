import requests
import re
import os
import time

# ================= 配置区域 =================
BARK_KEY = "https://api.day.app/H4TvmYKzupRxHAgrpTD65N" 

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

def send_bark_notification(title, content, jump_url, group_name):
    url = f"{BARK_KEY}/{title}"
    params = {
        "body": content,
        "url": jump_url,
        "sound": "minuet",
        "isArchive": "1",
        "group": group_name
    }
    try:
        resp = requests.get(url, params=params, timeout=5)
        print(f"[Push] {title}: {resp.text}")
    except Exception as e:
        print(f"[Push Error] {e}")

def check_nga():
    if not NGA_ENABLE:
        return
    
    print("--- 开始检查 NGA (带 Cookie 模式) ---")
    url = NGA_URL_TEMPLATE.format(NGA_UID)
    
    # 获取 Cookie
    nga_cookie = os.getenv("NGA_COOKIE")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://bbs.nga.cn/",
    }
    
    # 如果有 Cookie，加入 Header
    if nga_cookie:
        headers["Cookie"] = nga_cookie
        print("NGA: 已加载 Cookie，尝试以登录状态访问...")
    else:
        print("NGA: 警告！未检测到 NGA_COOKIE 环境变量，可能无法获取数据。请检查 GitHub Secrets 设置。")

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        
        # 检测是否被重定向到登录页
        if resp.status_code != 200 or "login" in resp.url or "登录" in resp.text:
            print(f"NGA 请求异常：状态码 {resp.status_code} 或检测到登录页。")
            print("原因可能是：Cookie 过期、Cookie 未正确设置、或 NGA 强制要求二次验证。")
            # 简单调试：打印 URL 确认是否跳转
            print(f"最终请求 URL: {resp.url}")
            return

        content = resp.text
        
        # 再次确认内容是否包含帖子列表（防止 Cookie 无效但没跳转）
        if "read.php?tid=" not in content:
            print("NGA: 页面内容中未找到帖子链接，可能 Cookie 无效或无数据。")
            # 可选：打印前 200 字排查
            # print(content[:200])
            return

        # --- 解析逻辑 ---
        pattern = r'<a\s+href="read\.php\?tid=(\d+)[^"]*"[^>]*>(.*?)</a>'
        matches = re.findall(pattern, content, re.DOTALL)
        
        if not matches:
            print("NGA: 未解析到任何帖子记录。")
            return

        tid = matches[0][0]
        raw_title = matches[0][1]
        title = re.sub(r'<[^>]+>', '', raw_title).strip()
        if len(title) > 40:
            title = title[:40] + "..."
            
        tid_pos = content.find(f'tid={tid}')
        r_time = "未知时间"
        if tid_pos != -1:
            snippet = content[tid_pos : tid_pos + 500]
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
        print(f"NGA 错误: {e}")
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
    check_nga()
    time.sleep(3) 
    check_hupu()

if __name__ == "__main__":
    main()
