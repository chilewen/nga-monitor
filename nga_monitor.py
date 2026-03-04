import requests
import re
import os
from datetime import datetime

# ===================== 多任务配置项（核心：url带authorid） =====================
# 格式：{"帖子专属过滤URL(带authorid)": "目标用户名(可选，用于推送)"}
# 示例："https://bbs.nga.cn/read.php?tid=45502551&authorid=370218": "实盘楼主"
MONITOR_CONFIG = {
    "https://bbs.nga.cn/read.php?tid=45502551&authorid=370218": "猫猫",  # 带authorid的过滤链接
    "https://bbs.nga.cn/read.php?tid=45502551&authorid=26529713": "小雨"
    # 可添加多个：帖子过滤URL + 自定义名称
    # "https://bbs.nga.cn/read.php?tid=123456&authorid=789012": "用户A",
}

# 通用配置
BARK_KEY = os.getenv("BARK_KEY")
NGA_COOKIE = os.getenv("NGA_COOKIE")
FIRST_RUN_PUSH_LIMIT = 3       # 首次运行仅推送最新N条
MAX_EMPTY_PAGES = 3            # 连续空页面停止爬取
REPLY_SORT_BY_TIME = True      # 按回复时间排序（保证最新在前，核心修复）

# 记录文件配置（按tid+authorid唯一区分）
RECORD_DIR = "nga_monitor_records"
os.makedirs(RECORD_DIR, exist_ok=True)

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

# ===================== 工具函数（适配过滤链接+唯一记录） =====================
def get_unique_id(post_url):
    """从过滤链接提取 tid+authorid 作为唯一标识，保证记录隔离"""
    tid_match = re.search(r'tid=(\d+)', post_url)
    authorid_match = re.search(r'authorid=(\d+)', post_url)
    tid = tid_match.group(1) if tid_match else "unknown_tid"
    authorid = authorid_match.group(1) if authorid_match else "unknown_aid"
    return f"{tid}_{authorid}"

def get_record_file_path(post_url, file_type):
    """生成按tid+authorid区分的记录文件路径"""
    unique_id = get_unique_id(post_url)
    return os.path.join(RECORD_DIR, f"{unique_id}_{file_type}.txt")

def load_pushed_replies(post_url):
    """加载指定用户已推送的回复PID"""
    record_file = get_record_file_path(post_url, "pushed_replies")
    if os.path.exists(record_file):
        with open(record_file, "r", encoding="utf-8") as f:
            pushed_pids = set(f.read().splitlines())
            print(f"✅ [{get_unique_id(post_url)}] 加载到已推送PID：{len(pushed_pids)}条")
            return pushed_pids
    print(f"⚠️ [{get_unique_id(post_url)}] 首次运行，无已推送记录")
    return set()

def save_pushed_ids(post_url, new_pids):
    """保存新推送的回复PID"""
    if not new_pids:
        return
    record_file = get_record_file_path(post_url, "pushed_replies")
    existing_pids = load_pushed_replies(post_url)
    to_save = [pid for pid in new_pids if pid not in existing_pids]
    if to_save:
        with open(record_file, "a", encoding="utf-8") as f:
            f.write("\n".join(to_save) + "\n")
        print(f"✅ [{get_unique_id(post_url)}] 记录新PID：{len(to_save)}条")

def load_last_crawled_page(post_url):
    """加载上次爬取的最后页数"""
    record_file = get_record_file_path(post_url, "last_page")
    if os.path.exists(record_file):
        with open(record_file, "r", encoding="utf-8") as f:
            page_num = f.read().strip()
            if page_num.isdigit():
                page_num = int(page_num)
                print(f"✅ [{get_unique_id(post_url)}] 上次爬取至第{page_num}页")
                return page_num
    print(f"⚠️ [{get_unique_id(post_url)}] 首次运行，从第1页开始爬取")
    return 1

def save_last_crawled_page(post_url, page_num):
    """保存本次爬取的最后页数"""
    record_file = get_record_file_path(post_url, "last_page")
    with open(record_file, "w", encoding="utf-8") as f:
        f.write(str(page_num))
    print(f"✅ [{get_unique_id(post_url)}] 记录本次爬取至第{page_num}页")

def check_login_status(html):
    """检查Cookie是否有效/是否登录"""
    if not html:
        print("❌ 爬取到空页面！")
        return False
    unlogin_keywords = ["请登录后查看", "登录", "未登录", "游客"]
    login_keywords = ["退出", "我的帖子", "个人中心"]
    has_unlogin = any(keyword in html for keyword in unlogin_keywords)
    has_login = any(keyword in html for keyword in login_keywords)
    if has_unlogin and not has_login:
        print("❌ Cookie失效或未登录！请更新Cookie")
        return False
    return True

def get_total_pages(html):
    """从过滤页面提取总页数（仅目标用户的回复页数）"""
    page_patterns = [
        re.compile(r'共 (\d+) 页', re.IGNORECASE),
        re.compile(r'page=(\d+).*?下一页', re.IGNORECASE | re.DOTALL),
        re.compile(r'最后一页.*?page=(\d+)', re.IGNORECASE)
    ]
    for pattern in page_patterns:
        match = pattern.search(html)
        if match:
            return int(match.group(1))
    return 1  # 兜底1页（过滤链接若无数据则直接返回）

# ===================== 核心：爬取带authorid的过滤页面（仅目标用户回复） =====================
def crawl_one_page(url, post_url):
    """爬取单页过滤内容，提取目标用户回复（含发布时间，用于排序）"""
    target_replies = []
    try:
        session = requests.Session()
        response = session.get(
            url, 
            headers=HEADERS, 
            timeout=20,
            allow_redirects=True
        )
        # 强制GBK编码（NGA专属，避免乱码）
        response.encoding = "GBK"
        html = response.text
        page_num = url.split("page=")[-1] if "page=" in url else "1"
        unique_id = get_unique_id(post_url)

        # 登录校验失败直接返回
        if not check_login_status(html):
            return target_replies, html

        # 提取目标用户的回复块（forumbox postbox为NGA通用回复容器）
        postbox_pattern = re.compile(
            r'<table class=\'forumbox postbox\'[^>]*>[\s\S]*?</table>',
            re.IGNORECASE
        )
        postboxes = postbox_pattern.findall(html)
        if not postboxes:
            print(f"📊 [{unique_id}] 第{page_num}页：无目标用户回复")
            return target_replies, html

        # 遍历每个回复块，提取【时间+PID+内容+链接】核心信息
        for box in postboxes:
            # 1. 提取回复PID（唯一标识，用于去重）
            pid_pattern = re.compile(r'pid(\d+)Anchor')
            pid_match = pid_pattern.search(box)
            pid = pid_match.group(1).strip() if pid_match else f"page{page_num}_{id(box)}"
            
            # 2. 提取回复发布时间（核心：用于排序，保证最新在前）
            time_pattern = re.compile(r'title=\'reply time\'>(\d{4}-\d{2}-\d{2} \d{2}:\d{2})')
            time_match = time_pattern.search(box)
            reply_time = time_match.group(1) if time_match else "1970-01-01 00:00"
            
            # 3. 提取回复内容（清理标签和无用信息）
            content_pattern = re.compile(
                r'<span id=\'postcontent\d+\' class=\'postcontent ubbcode\'>([\s\S]*?)</span>',
                re.IGNORECASE
            )
            content_match = content_pattern.search(box)
            content = ""
            if content_match:
                content = re.sub(r'<.*?>', '', content_match.group(1))  # 去掉所有HTML标签
                content = re.sub(r'&nbsp;|&gt;|&lt;|&amp;', ' ', content)  # 替换特殊字符
                content = re.sub(r'\s+', ' ', content).strip()  # 合并多余空格
                content = re.sub(r'\[img\][\s\S]*?\[\/img\]', '[图片]', content)  # 替换图片标签
            
            # 4. 提取回复跳转链接
            reply_url_pattern = re.compile(r'a id=\'pid(\d+)Anchor\'')
            reply_url_match = reply_url_pattern.search(box)
            if reply_url_match:
                reply_url = f"https://bbs.nga.cn/read.php?tid={get_unique_id(post_url).split('_')[0]}#pid{reply_url_match.group(1)}Anchor"
            else:
                reply_url = post_url

            # 过滤空内容，封装回复信息
            if content and len(content) > 2:
                reply_info = {
                    "pid": pid,
                    "reply_time": reply_time,  # 保留时间，用于排序
                    "content": content,
                    "url": reply_url,
                    "post_url": post_url,
                    "unique_id": unique_id
                }
                target_replies.append(reply_info)

        print(f"📊 [{unique_id}] 第{page_num}页：提取到{len(target_replies)}条目标回复")
    except Exception as e:
        unique_id = get_unique_id(post_url)
        print(f"❌ [{unique_id}] 第{page_num}页爬取出错：{type(e).__name__} - {str(e)[:50]}")
        return [], ""
    return target_replies, html

def crawl_filtered_post(post_url, user_name):
    """爬取带authorid的过滤帖子，从上次页数到最新页"""
    all_target_replies = []
    unique_id = get_unique_id(post_url)
    user_name = user_name if user_name else f"UID-{unique_id.split('_')[1]}"

    # 1. 初始化爬取参数
    start_page = load_last_crawled_page(post_url)
    # 提取基础过滤URL（去掉页码，用于拼接）
    base_url = post_url.split("&page=")[0] if "&page=" in post_url else post_url
    base_url = base_url + "&" if not base_url.endswith("&") and not base_url.endswith("?") else base_url

    print(f"\n🚀 开始监控：{user_name} | 专属链接：{base_url}")
    print(f"📖 爬取范围：第{start_page}页 → 最新页")

    # 2. 爬取起始页，获取目标用户的回复总页数
    first_url = base_url.replace("&", "", 1) if start_page == 1 else f"{base_url}page={start_page}"
    _, first_html = crawl_one_page(first_url, post_url)
    total_pages = get_total_pages(first_html)
    last_page = max(start_page, total_pages)
    if last_page < start_page:
        print(f"ℹ️ [{unique_id}] 无更多页面，爬取结束")
        save_last_crawled_page(post_url, start_page)
        return all_target_replies

    # 3. 从起始页爬取到最新页（正序爬取，保留所有回复）
    current_page = start_page
    empty_page_count = 0
    while current_page <= last_page and empty_page_count < MAX_EMPTY_PAGES:
        page_url = base_url.replace("&", "", 1) if current_page == 1 else f"{base_url}page={current_page}"
        page_replies, page_html = crawl_one_page(page_url, post_url)
        
        # 更新总页数（防止NGA动态加载新页数）
        new_total = get_total_pages(page_html)
        if new_total > last_page:
            last_page = new_total
            print(f"🔄 [{unique_id}] 发现新页数，更新至第{last_page}页")
        
        if page_replies:
            all_target_replies.extend(page_replies)
            empty_page_count = 0
        else:
            empty_page_count += 1
        current_page += 1

    # 4. 核心修复：按回复时间倒序排序（保证最新的回复在最前面）
    if REPLY_SORT_BY_TIME and all_target_replies:
        all_target_replies.sort(key=lambda x: x['reply_time'], reverse=True)
        print(f"✅ [{unique_id}] 所有回复按时间倒序排序完成")

    # 5. 保存本次爬取的最后页数
    final_page = current_page - 1
    save_last_crawled_page(post_url, final_page)

    print(f"\n📈 [{unique_id}] 爬取完成：共爬{final_page - start_page + 1}页，累计{len(all_target_replies)}条目标回复")
    return all_target_replies, user_name

# ===================== Bark推送（适配过滤链接+用户名） =====================
def send_to_bark(reply, user_name):
    """推送目标用户回复到Bark，标题带【帖子ID+用户名】"""
    if not BARK_KEY:
        print("❌ Bark Key未配置，跳过推送")
        return
    # 构造推送标题（tid+用户名，清晰区分）
    tid = reply['unique_id'].split('_')[0]
    title = f"【NGA-{tid}】{user_name} 新回复"
    # 内容截断（避免Bark字符限制）
    content = reply['content'][:300] + "..." if len(reply['content']) > 300 else reply['content']
    # Bark API请求
    bark_api = f"https://api.day.app/{BARK_KEY}/"
    params = {
        "title": title,
        "body": content,
        "url": reply['url'],  # 直接跳转至该条回复
        "isArchive": 1,       # 保存到Bark历史
        "sound": "bell.caf",  # 推送铃声
        "icon": "https://img.nga.178.com/ngabbs/favicon.ico"  # NGA图标
    }
    try:
        response = requests.get(bark_api, params=params, timeout=10)
        if response.status_code == 200 and response.json().get("code") == 200:
            print(f"✅ 推送成功：{user_name} | PID={reply['pid']}")
        else:
            print(f"❌ 推送失败：{response.status_code} - {response.text[:50]}")
    except Exception as e:
        print(f"❌ 推送异常：{type(e).__name__} - {str(e)[:50]}")

# ===================== 主程序（多任务调度+首次推送最新3条） =====================
def main():
    print(f"\n=== NGA精准监控脚本启动 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"📋 监控配置：共{len(MONITOR_CONFIG)}个目标用户（带authorid过滤）")
    total_push = 0

    # 遍历每个带authorid的过滤链接，独立监控
    for post_url, user_name in MONITOR_CONFIG.items():
        # 1. 爬取目标用户的所有回复（已按时间倒序）
        all_replies, user_name = crawl_filtered_post(post_url, user_name)
        if not all_replies:
            print(f"ℹ️ [{get_unique_id(post_url)}] 无目标用户回复，跳过")
            continue
        # 2. 加载已推送的PID，筛选新回复
        pushed_pids = load_pushed_replies(post_url)
        new_replies = [r for r in all_replies if r['pid'] not in pushed_pids]
        if not new_replies:
            print(f"ℹ️ [{get_unique_id(post_url)}] 无新回复，跳过推送")
            continue
        # 3. 首次运行仅推送最新N条，非首次推送全部新回复
        is_first_run = len(pushed_pids) == 0
        if is_first_run:
            push_replies = new_replies[:FIRST_RUN_PUSH_LIMIT]
            print(f"\n🎊 [{get_unique_id(post_url)}] 首次运行，推送最新{len(push_replies)}条（共{len(new_replies)}条新回复）")
        else:
            push_replies = new_replies
            print(f"\n🎊 [{get_unique_id(post_url)}] 发现{len(push_replies)}条新回复，开始推送")
        # 4. 推送新回复
        for reply in push_replies:
            send_to_bark(reply, user_name)
            total_push += 1
        # 5. 记录所有新PID（无论是否推送，避免后续重复）
        new_pids = [r['pid'] for r in new_replies]
        save_pushed_ids(post_url, new_pids)

    print(f"\n📊 本次监控结束：共推送{total_push}条新回复")
    print(f"=== NGA精准监控脚本结束 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")

if __name__ == "__main__":
    main()
