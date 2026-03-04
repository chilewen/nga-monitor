import requests
import re
import os
from datetime import datetime

# ===================== 配置项（从环境变量读取） =====================
BARK_KEY = os.getenv("BARK_KEY")
NGA_POST_URL = os.getenv("NGA_POST_URL")
TARGET_UID = os.getenv("TARGET_USER")  # 目标用户UID（纯数字）
NGA_COOKIE = os.getenv("NGA_COOKIE")
# 记录已推送的回复PID文件
RECORD_FILE = "pushed_replies.txt"
# 强制爬取页数
FORCE_CRAWL_PAGES = int(os.getenv("FORCE_CRAWL_PAGES", 10))

# 请求头（模拟浏览器，携带登录态）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Cookie": NGA_COOKIE or "",
    "Referer": "https://bbs.nga.cn/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
}

# ===================== 工具函数 =====================
def load_pushed_replies():
    """加载已推送的回复PID，避免重复推送"""
    if os.path.exists(RECORD_FILE):
        with open(RECORD_FILE, "r", encoding="utf-8") as f:
            pushed_pids = set(f.read().splitlines())
            print(f"✅ 加载到已推送的回复PID数量：{len(pushed_pids)}")
            return pushed_pids
    print("⚠️  首次运行，无已推送记录，将初始化历史ID")
    return set()

def save_pushed_ids(new_pids):
    """批量保存新的回复PID"""
    if not new_pids:
        return
    existing_pids = load_pushed_replies()
    to_save = [pid for pid in new_pids if pid not in existing_pids]
    if to_save:
        with open(RECORD_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(to_save) + "\n")
        print(f"✅ 已记录 {len(to_save)} 个新回复PID到文件")

def check_login_status(html):
    """检查是否登录成功"""
    if not html:
        print("❌ 爬取到空页面！")
        return False
    unlogin_keywords = ["请登录后查看", "登录", "未登录", "游客"]
    login_keywords = ["退出", "我的帖子", "个人中心"]
    
    has_unlogin = any(keyword in html for keyword in unlogin_keywords)
    has_login = any(keyword in html for keyword in login_keywords)
    
    if has_unlogin and not has_login:
        print("❌ Cookie失效或未登录！")
        return False
    return True

# ===================== 核心：精准提取目标回复 =====================
def crawl_one_page(url):
    """
    根据真实页面结构精准提取：
    - UID在 nuke.php?func=ucp&uid=xxx 中
    - 回复内容在 postcontent 标签中
    - PID在 pidxxxAnchor 中
    """
    target_replies = []
    try:
        session = requests.Session()
        response = session.get(
            url, 
            headers=HEADERS, 
            timeout=20,
            allow_redirects=True
        )
        response.encoding = response.apparent_encoding if response.apparent_encoding else "GBK"
        html = response.text
        page_num = url.split("page=")[-1] if "page=" in url else "1"
        
        # 调试信息
        print(f"\n===== 第 {page_num} 页调试信息 =====")
        print(f"📥 页面URL：{url}")
        print(f"🔓 登录状态：{'已登录' if check_login_status(html) else '未登录'}")
        
        if not check_login_status(html):
            return target_replies
        
        # ========== 核心：精准匹配回复块 ==========
        # 匹配完整的postbox表格（你的页面结构）
        postbox_pattern = re.compile(
            r'<table class=\'forumbox postbox\'[^>]*>[\s\S]*?</table>',
            re.IGNORECASE
        )
        postboxes = postbox_pattern.findall(html)
        print(f"📦 第 {page_num} 页找到回复块数量：{len(postboxes)}")
        
        # 遍历每个回复块提取信息
        for box in postboxes:
            # 1. 提取UID（精准匹配你的页面结构）
            uid_pattern = re.compile(r"nuke\.php\?func=ucp&uid=(\d+)")
            uid_match = uid_pattern.search(box)
            if not uid_match:
                continue
            current_uid = uid_match.group(1).strip()
            
            # 只处理目标UID
            if current_uid != TARGET_UID:
                continue
            
            # 2. 提取PID（精准匹配 pidxxxAnchor）
            pid_pattern = re.compile(r'pid(\d+)Anchor')
            pid_match = pid_pattern.search(box)
            pid = pid_match.group(1).strip() if pid_match else f"page{page_num}_{id(box)}"
            
            # 3. 提取回复内容（精准匹配 postcontent 标签）
            content_pattern = re.compile(
                r'<span id=\'postcontent\d+\' class=\'postcontent ubbcode\'>([\s\S]*?)</span>',
                re.IGNORECASE
            )
            content_match = content_pattern.search(box)
            content = ""
            if content_match:
                # 清理内容：去掉HTML标签、特殊字符、多余空格
                content = re.sub(r'<.*?>', '', content_match.group(1))
                content = re.sub(r'&nbsp;|&gt;|&lt;|&amp;', ' ', content)
                content = re.sub(r'\s+', ' ', content).strip()
                # 去掉图片标签 [img]...[/img]
                content = re.sub(r'\[img\][\s\S]*?\[\/img\]', '[图片]', content)
            
            # 4. 提取用户名（从author标签提取）
            username_pattern = re.compile(r'<a[^>]*class=\'author[^>]*>([^<]+)</a>')
            username_match = username_pattern.search(box)
            username = username_match.group(1).strip() if username_match else f"UID-{current_uid}"
            
            # 5. 拼接回复链接
            reply_url = f"{NGA_POST_URL.split('#')[0]}#pid{pid}Anchor" if pid.isdigit() else url
            
            # 只保留有内容的回复
            if content and len(content) > 2:
                reply_info = {
                    "pid": pid,
                    "uid": current_uid,
                    "username": username,
                    "content": content,
                    "url": reply_url
                }
                target_replies.append(reply_info)
                print(f"✅ 提取到目标回复：PID={pid} | 内容预览={content[:100]}...")
    
    except Exception as e:
        print(f"❌ 第 {page_num} 页爬取出错：{type(e).__name__} - {e}")
    
    print(f"📊 第 {page_num} 页最终提取到目标回复数：{len(target_replies)}")
    return target_replies

# ===================== 自动翻页爬取 =====================
def crawl_all_pages():
    """自动翻页爬取全帖"""
    all_target_replies = []
    
    # 提取基础URL
    if "&page=" in NGA_POST_URL:
        base_url = NGA_POST_URL.split("&page=")[0]
    else:
        base_url = NGA_POST_URL
    if not base_url.endswith("&") and not base_url.endswith("?"):
        base_url += "&"
    
    print(f"\n🚀 开始爬取全帖：{base_url}")
    print(f"🎯 监控目标UID：{TARGET_UID}")
    print(f"📖 计划爬取页数：{FORCE_CRAWL_PAGES} 页")
    
    # 强制爬取指定页数
    for page in range(1, FORCE_CRAWL_PAGES + 1):
        if page == 1:
            page_url = base_url.replace("&", "", 1)
        else:
            page_url = f"{base_url}page={page}"
        
        page_replies = crawl_one_page(page_url)
        if page_replies:
            all_target_replies.extend(page_replies)
    
    print(f"\n📈 全帖爬取完成：共爬取 {FORCE_CRAWL_PAGES} 页，找到目标UID回复 {len(all_target_replies)} 条")
    return all_target_replies

# ===================== Bark推送 =====================
def send_to_bark(reply):
    """推送回复到Bark App"""
    if not BARK_KEY:
        print("❌ Bark Key未配置，跳过推送")
        return
    
    bark_api = f"https://api.day.app/{BARK_KEY}/"
    title = f"【NGA新回复】{reply['username']}(UID:{reply['uid']})"
    content = reply['content'][:300] + "..." if len(reply['content']) > 300 else reply['content']
    
    params = {
        "title": title,
        "body": content,
        "url": reply['url'],
        "isArchive": 1,
        "sound": "bell.caf",
        "icon": "https://img.nga.178.com/ngabbs/favicon.ico"
    }
    
    try:
        response = requests.get(bark_api, params=params, timeout=10)
        if response.status_code == 200 and response.json().get("code") == 200:
            print(f"✅ 推送成功：PID={reply['pid']}")
        else:
            print(f"❌ 推送失败：{response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ 推送异常：{type(e).__name__} - {e}")

# ===================== 主程序 =====================
if __name__ == "__main__":
    print(f"\n=== NGA监控脚本启动 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    
    # 1. 爬取所有目标回复
    all_target_replies = crawl_all_pages()
    
    # 2. 加载已推送的PID
    pushed_pids = load_pushed_replies()
    
    # 3. 处理回复
    if not pushed_pids:
        # 首次运行：记录所有历史PID
        if all_target_replies:
            all_pids = [reply['pid'] for reply in all_target_replies]
            save_pushed_ids(all_pids)
            print(f"\n🎉 首次运行初始化完成：记录 {len(all_pids)} 条历史回复PID")
        else:
            print("\nℹ️  首次运行未找到任何目标回复，无PID可记录")
    else:
        # 非首次运行：推送新回复
        new_replies = [r for r in all_target_replies if r['pid'] not in pushed_pids]
        if new_replies:
            print(f"\n🎊 发现 {len(new_replies)} 条新回复，开始推送...")
            for reply in new_replies:
                send_to_bark(reply)
            new_pids = [r['pid'] for r in new_replies]
            save_pushed_ids(new_pids)
        else:
            print("\nℹ️  未发现新回复，无需推送")
    
    print(f"\n=== NGA监控脚本结束 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
