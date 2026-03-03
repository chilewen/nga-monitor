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
# NGA 移动端主页 URL 模板
NGA_URL_TEMPLATE = "https://m.nga.cn/u/uid.php?uid={}"

# --- 虎扑配置 ---
HUPU_ENABLE = True
HUPU_POST_URL = "https://bbs.hupu.com/636748637.html"
HUPU_TARGET_UID = "20829162237257"
HUPU_RECORD_FILE = "hupu_last_record.txt"
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
    
    print("--- 开始检查 NGA (HTML 模式) ---")
    url = NGA_URL_TEMPLATE.format(NGA_UID)
    
    # 关键：必须使用真实的手机 User-Agent，否则 NGA 会返回 404 或跳转
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://m.nga.cn/"
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        
        # 调试：如果状态码不是 200，打印出来
        if resp.status_code != 200:
            print(f"NGA 请求失败，状态码: {resp.status_code}")
            # 如果是 404，可能是 UID 错误或用户被屏蔽
            if resp.status_code == 404:
                print(f"提示：请检查 UID {NGA_UID} 是否正确，或该用户是否注销/被屏蔽。")
                print(f"尝试访问链接: {url}")
            return

        content = resp.text
        
        # --- 解析逻辑变更 ---
        # 在用户主页 HTML 中查找“回复”标签页下的内容
        # 通常结构：<div class="item"> ... <a href="read.php?tid=xxx">标题</a> ... <span>时间</span></div>
        # 我们寻找包含 "reply" 或 "回复" 上下文的链接
        
        # 策略：查找所有 read.php?tid= 开头的链接，并尝试获取其附近的标题和时间
        # 注意：主页可能混合了“发帖”和“回复”，我们需要区分。
        # 简单做法：取最新的几条，通过上下文判断，或者直接取最新的一条（假设用户最近主要是回复）
        # 更稳妥：查找包含 class="topicopt" 或类似标识回复的区域，但这很难通用。
        
        # 替代方案：NGA 主页通常有 tab 切换，但 HTML 源码里可能只加载了默认 tab (通常是主题)。
        # 如果默认是主题，我们需要构造“回复”Tab 的 URL。
        # NGA 回复列表 URL 通常是: https://m.nga.cn/u/uid.php?uid=xxx&tab=reply
        
        url_reply = f"{url}&tab=reply"
        resp_reply = requests.get(url_reply, headers=headers, timeout=15)
        
        if resp_reply.status_code == 200:
            content = resp_reply.text
        else:
            print(f"NGA 回复 Tab 请求失败: {resp_reply.status_code}, 尝试使用默认页面")
            # 如果 tab=reply 失败，回退到默认页面碰运气
            content = resp.text

        # 正则匹配：寻找 read.php?tid=数字 以及紧随其后的标题
        # 匹配模式：<a href="read.php?tid=123456">标题文本</a>
        pattern = r'<a\s+href="read\.php\?tid=(\d+)[^"]*"[^>]*>(.*?)</a>'
        matches = re.findall(pattern, content, re.DOTALL)
        
        if not matches:
            print("NGA: 未解析到任何帖子链接，可能页面结构变更或无数据")
            # 打印前 500 字符用于调试 (可在 GitHub Logs 查看)
            # print(content[:500]) 
            return

        # 取第一个匹配项 (即最新的一条)
        tid = matches[0][0]
        # 清理标题中的 HTML 标签和空白
        raw_title = matches[0][1]
        title = re.sub(r'<[^>]+>', '', raw_title).strip()
        if len(title) > 30:
            title = title[:30] + "..."
            
        # 尝试提取时间 (在链接附近)
        # 简单起见，我们暂时只用 TID 作为唯一标识。
        # 如果需要区分同帖多次回复，需要更复杂的上下文解析。
        # 为了稳健，我们先记录 TID。如果同帖回复，TID 不变则不推送（这符合“监控新帖回复”的大多数场景）。
        # 如果你非常需要监控“同帖内的多次回复”，请告知，我们需要解析具体时间戳。
        # 这里采用 TID + 标题前10字 作为指纹，防止 TID 重复但标题极罕见的情况（实际上 TID 唯一）
        # 修正：如果要监控同帖多次回复，必须解析时间。让我们尝试解析时间。
        # 在 matches[0] 附近找时间？re.findall 拿不到上下文。
        # 我们直接在 content 中找该 tid 出现的位置，然后向后找时间。
        tid_pos = content.find(f'tid={tid}')
        if tid_pos != -1:
            snippet = content[tid_pos:tid_pos+300]
            time_match = re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}-\d{2})\s+(\d{1,2}:\d{2})', snippet)
            r_time = f"{time_match.group(1)} {time_match.group(2)}" if time_match else "刚刚"
        else:
            r_time = "未知"

        current_record = f"{tid}|{r_time}"
        
        # 读取旧记录
        last_record = ""
        if os.path.exists(NGA_RECORD_FILE):
            with open(NGA_RECORD_FILE, "r", encoding="utf-8") as f:
                last_record = f.read().strip()
        
        print(f"NGA 最新: TID={tid}, 标题={title}, 时间={r_time}")
        print(f"NGA 上次: {last_record}")

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
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://bbs.hupu.com/"
    }
    
    try:
        url = f"{HUPU_POST_URL}?page=1" 
        resp = requests.get(url, headers=headers, timeout=15)
        
        if resp.status_code != 200:
            resp = requests.get(HUPU_POST_URL, headers=headers, timeout=15)
            if resp.status_code != 200:
                print(f"虎扑请求失败: {resp.status_code}")
                return

        content = resp.text
        user_link_pattern = f'href="/home/{HUPU_TARGET_UID}"'
        matches = list(re.finditer(user_link_pattern, content))
        
        if not matches:
            print("虎扑: 当前页未找到该用户的回复")
            return

        last_match_pos = matches[-1].start()
        snippet = content[last_match_pos : last_match_pos + 600]
        
        general_time = re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}-\d{2})\s+(\d{1,2}:\d{2})', snippet)
        hupu_time = "未知时间"
        if general_time:
            hupu_time = f"{general_time.group(1)} {general_time.group(2)}"
        else:
            today_match = re.search(r'(今天|昨天)\s+(\d{1,2}:\d{2})', snippet)
            if today_match:
                hupu_time = f"{today_match.group(1)} {today_match.group(2)}"

        snippet_fingerprint = re.sub(r'<[^>]+>', '', snippet)[:30].strip()
        current_record = f"{hupu_time}|{snippet_fingerprint}"
        
        last_record = ""
        if os.path.exists(HUPU_RECORD_FILE):
            with open(HUPU_RECORD_FILE, "r", encoding="utf-8") as f:
                last_record = f.read().strip()
        
        if current_record != last_record:
            msg = f"虎扑目标用户 ({HUPU_TARGET_UID[-6:]}) 在帖子中有新回复!\n时间: {hupu_time}\n内容片段: {snippet_fingerprint}"
            send_bark_notification("🏀 虎扑新回复", msg, HUPU_POST_URL, "虎扑监控")
            
            with open(HUPU_RECORD_FILE, "w", encoding="utf-8") as f:
                f.write(current_record)
            print("虎扑: 发现新回复，已推送并更新记录")
        else:
            print("虎扑: 无新回复")

    except Exception as e:
        print(f"虎扑错误: {e}")

def main():
    check_nga()
    time.sleep(2)
    check_hupu()

if __name__ == "__main__":
    main()
