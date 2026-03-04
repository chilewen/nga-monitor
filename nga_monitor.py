import requests
import re
import os
from datetime import datetime

# ===================== 【配置区：你只需要改这里】 =====================
MONITOR_TASKS = [
    {
        "url": "https://bbs.nga.cn/read.php?tid=45502551&authorid=370218",
        "name": "猫猫"
    },
    {
        "url": "https://bbs.nga.cn/read.php?tid=45502551&authorid=26529713",
        "name": "小雨"
    }
    # 可添加更多任务
    # {
    #     "url": "https://bbs.nga.cn/read.php?tid=123456&authorid=789012",
    #     "name": "测试用户"
    # },
]

BARK_KEY = os.getenv("BARK_KEY")
NGA_COOKIE = os.getenv("NGA_COOKIE")

FIRST_RUN_PUSH_LIMIT = 3  # 首次只推最新3条
MAX_EMPTY_PAGES = 3       # 连续N页无回复则停止遍历
DEBUG_MODE = True         # 调试模式：打印详细日志
# =====================================================================

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

# 确保记录目录存在（自动创建）
os.makedirs("nga_monitor", exist_ok=True)

# ===================== 工具函数：每个任务独立记录 =====================
def get_task_key(task):
    """生成任务唯一标识：tid_authorid"""
    url = task["url"]
    tid_match = re.search(r"tid=(\d+)", url)
    aid_match = re.search(r"authorid=(\d+)", url)
    tid = tid_match.group(1) if tid_match else "unknown_tid"
    aid = aid_match.group(1) if aid_match else "unknown_aid"
    task_key = f"{tid}_{aid}"
    if DEBUG_MODE:
        print(f"[调试] 任务唯一标识：{task_key}（URL：{url}）")
    return task_key

def last_page_file(task):
    """任务页数记录文件路径"""
    return f"nga_monitor/last_page_{get_task_key(task)}.txt"

def pushed_file(task):
    """任务已推送PID记录文件路径"""
    return f"nga_monitor/pushed_{get_task_key(task)}.txt"

def load_last_page(task):
    """加载上次爬取的最后页数（带调试）"""
    file_path = last_page_file(task)
    if os.path.exists(file_path):
        with open(file_path, encoding="utf-8") as fp:
            page_num = int(fp.read().strip())
            if DEBUG_MODE:
                print(f"[调试] 加载到上次爬取页数：{page_num}（文件：{file_path}）")
        return page_num
    if DEBUG_MODE:
        print(f"[调试] 未找到页数记录文件，首次运行（文件：{file_path}）")
    return 0  # 首次运行从0开始（表示还没爬过任何页）

def save_last_page(task, page):
    """保存本次爬取的最后页数（带调试）"""
    file_path = last_page_file(task)
    with open(file_path, "w", encoding="utf-8") as fp:
        fp.write(str(page))
    if DEBUG_MODE:
        print(f"[调试] 保存本次爬取最后页数：{page}（文件：{file_path}）")

def load_pushed_pids(task):
    """加载已推送的PID（带调试）"""
    file_path = pushed_file(task)
    pushed_pids = set()
    if os.path.exists(file_path):
        with open(file_path, encoding="utf-8") as fp:
            pushed_pids = set(line.strip() for line in fp if line.strip())
    if DEBUG_MODE:
        print(f"[调试] 已推送PID数量：{len(pushed_pids)}（文件：{file_path}）")
    return pushed_pids

def append_pushed_pids(task, pids):
    """追加记录已推送的PID（带调试）"""
    if not pids:
        return
    file_path = pushed_file(task)
    with open(file_path, "a", encoding="utf-8") as fp:
        fp.write("\n".join(pids) + "\n")
    if DEBUG_MODE:
        print(f"[调试] 新增记录PID数量：{len(pids)}（文件：{file_path}）")

# ===================== 核心函数：爬取单页（提取目标用户回复） =====================
def crawl_page(task, page):
    """爬取指定页数（带详细调试，打印所有提取的回复）"""
    # 构造带页码的URL
    base_url = task["url"].split("&page=")[0] if "&page=" in task["url"] else task["url"]
    crawl_url = f"{base_url}&page={page}"
    if DEBUG_MODE:
        print(f"\n[调试] 开始爬取页面，页码：{page}，请求URL：{crawl_url}")
    
    try:
        response = requests.get(crawl_url, headers=HEADERS, timeout=20)
        response.encoding = "gbk"  # 强制NGA编码
        html = response.text
        
        # 提取回复块
        post_pattern = re.compile(
            r'<table class=\'forumbox postbox\'[^>]*>[\s\S]*?</table>',
            re.IGNORECASE
        )
        posts = post_pattern.findall(html)
        if DEBUG_MODE:
            print(f"[调试] 第{page}页提取到回复块数量：{len(posts)}")
        
        replies = []
        for idx, post in enumerate(posts):
            # 提取核心信息
            pid_match = re.search(r'pid(\d+)Anchor', post)
            time_match = re.search(r'title=\'reply time\'>(\d{4}-\d{2}-\d{2} \d{2}:\d{2})', post)
            content_match = re.search(r'class=\'postcontent ubbcode\'>([\s\S]*?)</span>', post)
            
            # 调试：打印单个回复块
            if DEBUG_MODE:
                print(f"\n[调试] 第{page}页回复块{idx+1}：")
                print(f"  PID匹配结果：{pid_match.group(1) if pid_match else '无'}")
                print(f"  时间匹配结果：{time_match.group(1) if time_match else '无'}")
            
            # 过滤无效回复
            if not (pid_match and content_match):
                if DEBUG_MODE:
                    print(f"[调试] 第{page}页回复块{idx+1}：无效，跳过")
                continue
            
            # 清理内容
            pid = pid_match.group(1)
            reply_time = time_match.group(1) if time_match else "1970-01-01 00:00"
            content = content_match.group(1)
            content = re.sub(r"<.*?>", "", content)  # 去掉HTML标签
            content = re.sub(r"\[img\].*?\[/img\]", "[图片]", content)  # 替换图片
            content = re.sub(r"\s+", " ", content).strip()  # 清理空格
            
            reply = {
                "pid": pid,
                "time": reply_time,
                "content": content,
                "page": page
            }
            replies.append(reply)
            
            if DEBUG_MODE:
                print(f"[调试] 第{page}页有效回复{len(replies)}：PID={pid} | 时间={reply_time} | 内容={content[:50]}...")
        
        return replies
    
    except Exception as e:
        print(f"[错误] 爬取第{page}页失败：{str(e)}")
        return []

# ===================== 核心函数：遍历所有页（从起始页到无内容为止） =====================
def crawl_all_pages(task):
    """遍历所有页（从上次页数+1开始，直到连续N页无回复）"""
    start_page = load_last_page(task) + 1  # 从上次页数的下一页开始
    all_replies = []
    empty_page_count = 0  # 连续空页面计数
    current_page = start_page
    
    print(f"\n🚀 开始遍历页面：从第{start_page}页开始，连续{MAX_EMPTY_PAGES}页无回复则停止")
    
    while empty_page_count < MAX_EMPTY_PAGES:
        # 爬取当前页
        page_replies = crawl_page(task, current_page)
        
        if page_replies:
            all_replies.extend(page_replies)
            empty_page_count = 0  # 重置空页面计数
            print(f"✅ 第{current_page}页：爬取到{len(page_replies)}条有效回复")
        else:
            empty_page_count += 1
            print(f"ℹ️ 第{current_page}页：无有效回复（连续空页{empty_page_count}/{MAX_EMPTY_PAGES}）")
        
        current_page += 1
    
    # 计算本次爬取的最后有效页数
    last_crawled_page = current_page - empty_page_count - 1
    if last_crawled_page < start_page:
        last_crawled_page = start_page - 1  # 无任何有效页面
    
    # 调试：打印遍历结果
    if DEBUG_MODE:
        print(f"\n[调试] 遍历完成：")
        print(f"  起始页码：{start_page}")
        print(f"  最后爬取页码：{current_page - 1}")
        print(f"  最后有效页码：{last_crawled_page}")
        print(f"  累计提取回复数量：{len(all_replies)}")
    
    return all_replies, last_crawled_page

# ===================== 推送函数（带调试） =====================
def push(task, reply):
    """推送回复到Bark（带调试）"""
    if not BARK_KEY:
        print("[调试] 跳过推送：BARK_KEY未配置")
        return
    
    # 构造推送内容
    task_key = get_task_key(task)
    tid = task_key.split('_')[0]
    title = f"【NGA-{tid}】{task['name']} 新回复"
    content = reply["content"][:300] if len(reply["content"]) > 300 else reply["content"]
    bark_url = f"https://api.day.app/{BARK_KEY}/{title}/{content}"
    
    if DEBUG_MODE:
        print(f"\n[调试] 开始推送：")
        print(f"  推送标题：{title}")
        print(f"  推送内容：{content[:100]}...")
    
    try:
        response = requests.get(bark_url, timeout=10)
        if response.status_code == 200 and response.json().get("code") == 200:
            print(f"✅ 推送成功 | PID={reply['pid']} | 页码={reply['page']}")
        else:
            print(f"❌ 推送失败 | 状态码={response.status_code} | 响应={response.text}")
    except Exception as e:
        print(f"❌ 推送异常 | 错误={str(e)}")

# ===================== 单任务执行（核心逻辑） =====================
def run_task(task):
    """执行单个监控任务（遍历所有页+精准取最新3条）"""
    print("\n" + "="*80)
    print(f"开始执行任务：{task['name']}")
    print(f"任务URL：{task['url']}")
    print("="*80)
    
    # 1. 遍历所有页（从上次页数+1开始）
    all_replies, last_crawled_page = crawl_all_pages(task)
    
    # 2. 加载已推送的PID，筛选新回复
    pushed_pids = load_pushed_pids(task)
    new_replies = [r for r in all_replies if r["pid"] not in pushed_pids]
    
    # 3. 按时间/PID倒序排序（保证最新回复在前）
    if new_replies:
        # 优先按时间排序，时间相同按PID排序
        new_replies.sort(key=lambda x: (x["time"], x["pid"]), reverse=True)
        print(f"\n📊 筛选出新回复数量：{len(new_replies)}（按时间倒序排序）")
        
        # 调试：打印新回复列表
        if DEBUG_MODE:
            print(f"\n[调试] 新回复列表（前10条）：")
            for idx, r in enumerate(new_replies[:10]):
                print(f"  {idx+1}. PID={r['pid']} | 时间={r['time']} | 页码={r['page']}")
    else:
        print(f"\nℹ️ 未发现新回复，无需推送")
        # 保存本次爬取的最后页数
        save_last_page(task, last_crawled_page)
        return
    
    # 4. 处理推送逻辑
    is_first_run = len(pushed_pids) == 0
    if is_first_run:
        # 首次运行：只推送最新3条
        push_list = new_replies[:FIRST_RUN_PUSH_LIMIT]
        print(f"\n🎯 首次运行：推送最新{len(push_list)}条回复（共{len(new_replies)}条新回复）")
    else:
        # 非首次运行：推送所有新回复
        push_list = new_replies
        print(f"\n🎯 非首次运行：推送全部{len(push_list)}条新回复")
    
    # 执行推送
    if push_list:
        for idx, r in enumerate(push_list):
            print(f"\n--- 推送第{idx+1}条 ---")
            push(task, r)
        
        # 记录已推送的PID
        append_pushed_pids(task, [r["pid"] for r in push_list])
    
    # 5. 保存本次爬取的最后页数
    save_last_page(task, last_crawled_page)

# ===================== 主入口 =====================
if __name__ == "__main__":
    print(f"\n=== NGA多用户监控脚本启动 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"📝 调试模式：{'开启' if DEBUG_MODE else '关闭'}")
    print(f"📋 监控任务数量：{len(MONITOR_TASKS)}")
    print(f"🔧 连续空页停止阈值：{MAX_EMPTY_PAGES}页")
    print(f"🎯 首次推送限制：{FIRST_RUN_PUSH_LIMIT}条最新回复")
    
    # 遍历执行所有任务
    for task in MONITOR_TASKS:
        run_task(task)
    
    print(f"\n=== NGA多用户监控脚本结束 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
