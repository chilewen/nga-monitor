import requests
import re
import os
from datetime import datetime
from collections import defaultdict

# ===================== 多任务配置项（核心修改） =====================
# 方式1：直接在代码中配置（简单直观）
# 格式：{"帖子URL": ["目标UID1", "目标UID2"], ...}
MONITOR_CONFIG = {
    "https://bbs.nga.cn/read.php?tid=45502551": ["370218"],  # 实盘贴 + 目标UID
    # 可添加更多帖子和用户
    # "https://bbs.nga.cn/read.php?tid=123456": ["789012", "345678"],
    # "https://bbs.nga.cn/read.php?tid=789012": ["987654"],
}

# 方式2：从环境变量读取（适合部署场景，推荐）
# 格式：POST1_URL=xxx;POST1_UIDS=370218,123456;POST2_URL=xxx;POST2_UIDS=789012
# if os.getenv("NGA_MONITOR_CONFIG"):
#     config_str = os.getenv("NGA_MONITOR_CONFIG")
#     MONITOR_CONFIG = {}
#     parts = config_str.split(";")
#     post_url = ""
#     for part in parts:
#         if part.startswith("POST") and part.endswith("_URL"):
#             post_url = part.split("=")[1].strip()
#         elif part.startswith("POST") and part.endswith("_UIDS") and post_url:
#             uids = part.split("=")[1].strip().split(",")
#             MONITOR_CONFIG[post_url] = [uid.strip() for uid in uids if uid.strip()]

# 通用配置
BARK_KEY = os.getenv("BARK_KEY")
NGA_COOKIE = os.getenv("NGA_COOKIE")
FIRST_RUN_PUSH_LIMIT = 3  # 首次运行仅推送最新N条
MAX_EMPTY_PAGES = 3       # 连续空页面停止爬取

# 记录文件配置（按帖子URL区分）
RECORD_DIR = "nga_monitor_records"
os.makedirs(RECORD_DIR, exist_ok=True)

# 请求头
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Cookie": NGA_COOKIE or "",
    "Referer": "https://bbs.nga.cn/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
}

# ===================== 工具函数（适配多任务） =====================
def get_record_file_path(post_url, file_type):
    """生成按帖子区分的记录文件路径"""
    # 从URL提取tid作为唯一标识
    tid_match = re.search(r'tid=(\d+)', post_url)
    tid = tid_match.group(1) if tid_match else str(hash(post_url))[:8]
    return os.path.join(RECORD_DIR, f"{tid}_{file_type}.txt")

def load_pushed_replies(post_url):
    """加载指定帖子已推送的回复PID"""
    record_file = get_record_file_path(post_url, "pushed_replies")
    if os.path.exists(record_file):
        with open(record_file, "r", encoding="utf-8") as f:
            pushed_pids = set(f.read().splitlines())
            print(f"✅ [{post_url}] 加载到已推送的回复PID数量：{len(pushed_pids)}")
            return pushed_pids
    print(f"⚠️ [{post_url}] 首次运行，无已推送记录")
    return set()

def save_pushed_ids(post_url, new_pids):
    """保存指定帖子的新回复PID"""
    if not new_pids:
        return
    record_file = get_record_file_path(post_url, "pushed_replies")
    existing_pids = load_pushed_replies(post_url)
    to_save = [pid for pid in new_pids if pid not in existing_pids]
    if to_save:
        with open(record_file, "a", encoding="utf-8") as f:
            f.write("\n".join(to_save) + "\n")
        print(f"✅ [{post_url}] 已记录 {len(to_save)} 个新回复PID到文件")

def load_last_crawled_page(post_url):
    """加载指定帖子上次爬取的最后页数"""
    record_file = get_record_file_path(post_url, "last_page")
    if os.path.exists(record_file):
        with open(record_file, "r", encoding="utf-8") as f:
            page_num = f.read().strip()
            if page_num.isdigit():
                page_num = int(page_num)
                print(f"✅ [{post_url}] 加载到上次爬取的最后页数：{page_num}")
                return page_num
    print(f"⚠️ [{post_url}] 首次运行，从第1页开始爬取")
    return 1

def save_last_crawled_page(post_url, page_num):
    """保存指定帖子本次爬取的最后页数"""
    record_file = get_record_file_path(post_url, "last_page")
    with open(record_file, "w", encoding="utf-8") as f:
        f.write(str(page_num))
    print(f"✅ [{post_url}] 已记录本次爬取的最后页数：{page_num}")

def check_login_status(html):
    """检查是否登录成功"""
    if not html:
        return False
    unlogin_keywords = ["请登录后查看", "登录", "未登录", "游客"]
    login_keywords = ["退出", "我的帖子", "个人中心"]
    
    has_unlogin = any(keyword in html for keyword in unlogin_keywords)
    has_login = any(keyword in html for keyword in login_keywords)
    
    if has_unlogin and not has_login:
        print("❌ Cookie失效或未登录！")
        return False
    return True

def get_total_pages(html):
    """提取帖子总页数"""
    page_patterns = [
        re.compile(r'共 (\d+) 页', re.IGNORECASE),
        re.compile(r'page=(\d+).*?下一页.*?末页', re.IGNORECASE | re.DOTALL),
        re.compile(r'最后一页.*?page=(\d+)', re.IGNORECASE)
    ]
    
    for pattern in page_patterns:
        match = pattern.search(html)
        if match:
            return int(match.group(1))
    return 5

# ===================== 核心：单帖子爬取 =====================
def crawl_single_post(post_url, target_uids):
    """爬取单个帖子的目标用户回复"""
    all_target_replies = []
    target_uids = set(target_uids)  # 去重
    
    # 1. 初始化爬取参数
    start_page = load_last_crawled_page(post_url)
    
    # 提取基础URL
    if "&page=" in post_url:
        base_url = post_url.split("&page=")[0]
    else:
        base_url = post_url
    if not base_url.endswith("&") and not base_url.endswith("?"):
        base_url += "&"
    
    print(f"\n🚀 开始爬取帖子：{post_url}")
    print(f"🎯 监控目标UID：{list(target_uids)}")
    print(f"📖 爬取起始页数：{start_page}")
    
    # 2. 先爬起始页获取总页数
    first_url = base_url.replace("&", "", 1) if start_page == 1 else f"{base_url}page={start_page}"
    _, first_html = crawl_one_page(first_url, post_url, target_uids)
    total_pages = get_total_pages(first_html)
    last_page = max(start_page, total_pages)
    
    # 3. 遍历爬取页面（从后往前爬，保证最新回复在前）
    current_page = start_page
    empty_page_count = 0
    
    # 反向爬取（优先获取最新页，方便取最新3条）
    crawl_pages = list(range(start_page, last_page + 1))
    crawl_pages.reverse()  # 从最后一页往回爬
    
    for current_page in crawl_pages:
        if current_page == 1:
            page_url = base_url.replace("&", "", 1)
        else:
            page_url = f"{base_url}page={current_page}"
        
        page_replies, page_html = crawl_one_page(page_url, post_url, target_uids)
        
        # 更新总页数
        new_total = get_total_pages(page_html)
        if new_total > last_page:
            last_page = new_total
            print(f"🔄 [{post_url}] 更新帖子总页数为：{last_page}")
        
        if page_replies:
            all_target_replies.extend(page_replies)
            empty_page_count = 0
        else:
            empty_page_count += 1
            if empty_page_count >= MAX_EMPTY_PAGES:
                break
    
    # 4. 保存本次爬取的最后页数（取最大页数）
    final_page = max(crawl_pages) if crawl_pages else start_page
    save_last_crawled_page(post_url, final_page)
    
    # 按PID排序（保证最新回复在前）
    all_target_replies.sort(key=lambda x: x['pid'], reverse=True)
    
    print(f"\n📈 [{post_url}] 爬取完成：共爬取 {len(crawl_pages)} 页，找到目标UID回复 {len(all_target_replies)} 条")
    return all_target_replies

def crawl_one_page(url, post_url, target_uids):
    """爬取单页，提取指定UID的回复"""
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
        
        # 登录校验
        if not check_login_status(html):
            return target_replies, html
        
        # 提取回复块
        postbox_pattern = re.compile(
            r'<table class=\'forumbox postbox\'[^>]*>[\s\S]*?</table>',
            re.IGNORECASE
        )
        postboxes = postbox_pattern.findall(html)
        
        # 遍历回复块
        for box in postboxes:
            # 提取UID
            uid_pattern = re.compile(r"nuke\.php\?func=ucp&uid=(\d+)")
            uid_match = uid_pattern.search(box)
            if not uid_match:
                continue
            current_uid = uid_match.group(1).strip()
            
            # 只处理目标UID
            if current_uid not in target_uids:
                continue
            
            # 提取PID
            pid_pattern = re.compile(r'pid(\d+)Anchor')
            pid_match = pid_pattern.search(box)
            pid = pid_match.group(1).strip() if pid_match else f"page{page_num}_{id(box)}"
            
            # 提取内容
            content_pattern = re.compile(
                r'<span id=\'postcontent\d+\' class=\'postcontent ubbcode\'>([\s\S]*?)</span>',
                re.IGNORECASE
            )
            content_match = content_pattern.search(box)
            content = ""
            if content_match:
                content = re.sub(r'<.*?>', '', content_match.group(1))
                content = re.sub(r'&nbsp;|&gt;|&lt;|&amp;', ' ', content)
                content = re.sub(r'\s+', ' ', content).strip()
                content = re.sub(r'\[img\][\s\S]*?\[\/img\]', '[图片]', content)
            
            # 提取用户名
            username_pattern = re.compile(r'<a[^>]*class=\'author[^>]*>([^<]+)</a>')
            username_match = username_pattern.search(box)
            username = username_match.group(1).strip() if username_match else f"UID-{current_uid}"
            
            # 拼接链接
            reply_url = f"{post_url.split('#')[0]}#pid{pid}Anchor" if pid.isdigit() else post_url
            
            if content and len(content) > 2:
                reply_info = {
                    "pid": pid,
                    "uid": current_uid,
                    "username": username,
                    "content": content,
                    "url": reply_url,
                    "post_url": post_url
                }
                target_replies.append(reply_info)
    
    except Exception as e:
        print(f"❌ [{post_url}] 第 {page_num} 页爬取出错：{type(e).__name__} - {e}")
        return [], ""
    
    print(f"📊 [{post_url}] 第 {page_num} 页提取到目标回复数：{len(target_replies)}")
    return target_replies, html

# ===================== Bark推送（适配多任务） =====================
def send_to_bark(reply):
    """推送回复到Bark App"""
    if not BARK_KEY:
        print("❌ Bark Key未配置，跳过推送")
        return
    
    bark_api = f"https://api.day.app/{BARK_KEY}/"
    # 推送标题区分不同帖子
    tid_match = re.search(r'tid=(\d+)', reply['post_url'])
    tid = tid_match.group(1) if tid_match else "未知帖子"
    title = f"【NGA-{tid}】{reply['username']}(UID:{reply['uid']})"
    
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
            print(f"✅ 推送成功：PID={reply['pid']} | 帖子={tid}")
        else:
            print(f"❌ 推送失败：{response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ 推送异常：{type(e).__name__} - {e}")

# ===================== 主程序（多任务调度） =====================
def main():
    print(f"\n=== NGA多帖子监控脚本启动 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"📋 监控配置：共 {len(MONITOR_CONFIG)} 个帖子，{sum(len(uids) for uids in MONITOR_CONFIG.values())} 个目标UID")
    
    total_new_replies = 0
    
    # 遍历每个帖子进行监控
    for post_url, target_uids in MONITOR_CONFIG.items():
        # 1. 爬取该帖子的目标回复
        all_replies = crawl_single_post(post_url, target_uids)
        
        # 2. 加载已推送的PID
        pushed_pids = load_pushed_replies(post_url)
        
        # 3. 筛选新回复
        new_replies = [r for r in all_replies if r['pid'] not in pushed_pids]
        
        if new_replies:
            # 判断是否首次运行（无已推送记录）
            is_first_run = len(pushed_pids) == 0
            
            # 首次运行仅推送最新3条
            if is_first_run:
                push_replies = new_replies[:FIRST_RUN_PUSH_LIMIT]
                print(f"\n🎊 [{post_url}] 首次运行，仅推送最新 {len(push_replies)} 条回复（共{len(new_replies)}条）")
            else:
                push_replies = new_replies
                print(f"\n🎊 [{post_url}] 发现 {len(push_replies)} 条新回复，开始推送...")
            
            # 推送
            for reply in push_replies:
                send_to_bark(reply)
            
            # 记录所有新PID（无论是否推送）
            new_pids = [r['pid'] for r in new_replies]
            save_pushed_ids(post_url, new_pids)
            
            total_new_replies += len(push_replies)
        else:
            print(f"\nℹ️ [{post_url}] 未发现新回复，无需推送")
    
    print(f"\n📊 本次监控完成：共推送 {total_new_replies} 条新回复")
    print(f"\n=== NGA多帖子监控脚本结束 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

if __name__ == "__main__":
    main()
