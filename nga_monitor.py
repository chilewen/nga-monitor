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
DEBUG_MODE = True  # 调试模式：打印详细日志
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

# 确保记录目录存在
os.makedirs("nga_monitor", exist_ok=True)

# ===================== 工具函数：每个任务独立记录（强化调试） =====================
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
    return None  # 首次运行

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

# ===================== 核心函数：获取总页数（强化调试） =====================
def get_total_page(task):
    """获取帖子总页数（带详细调试）"""
    url = task["url"]
    if DEBUG_MODE:
        print(f"\n[调试] 开始获取总页数，请求URL：{url}")
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.encoding = "gbk"  # 强制NGA编码
        html = response.text
        
        # 打印页面关键片段（定位总页数）
        if DEBUG_MODE:
            print(f"[调试] 页面状态码：{response.status_code}")
            print(f"[调试] 页面关键片段（含页数）：{html[:1000]}")
        
        # 匹配总页数（多种规则兜底）
        page_patterns = [
            re.compile(r'共 (\d+) 页', re.IGNORECASE),
            re.compile(r'page=(\d+).*?末页', re.IGNORECASE | re.DOTALL),
            re.compile(r'最后一页.*?page=(\d+)', re.IGNORECASE)
        ]
        total_page = 1
        for pattern in page_patterns:
            match = pattern.search(html)
            if match:
                total_page = int(match.group(1))
                break
        
        if DEBUG_MODE:
            print(f"[调试] 识别到总页数：{total_page}")
        return total_page
    
    except Exception as e:
        print(f"[错误] 获取总页数失败：{str(e)}")
        return 1

# ===================== 核心函数：爬取单页（强化调试） =====================
def crawl_page(task, page):
    """爬取指定页数（带详细调试，打印所有提取的回复）"""
    # 构造带页码的URL
    base_url = task["url"].split("&page=")[0] if "&page=" in task["url"] else task["url"]
    crawl_url = f"{base_url}&page={page}" if page > 1 else base_url
    if DEBUG_MODE:
        print(f"\n[调试] 开始爬取页面，页码：{page}，请求URL：{crawl_url}")
    
    try:
        response = requests.get(crawl_url, headers=HEADERS, timeout=20)
        response.encoding = "gbk"
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
                print(f"  原始片段：{post[:500]}")
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

# ===================== 推送函数（带调试） =====================
def push(task, reply):
    """推送回复到Bark（带调试）"""
    if not BARK_KEY:
        print("[调试] 跳过推送：BARK_KEY未配置")
        return
    
    # 构造推送内容
    task_name = task["name"]
    title = f"【NGA-{get_task_key(task).split('_')[0]}】{task_name} 新回复"
    content = reply["content"][:300] if len(reply["content"]) > 300 else reply["content"]
    bark_url = f"https://api.day.app/{BARK_KEY}/{title}/{content}"
    
    if DEBUG_MODE:
        print(f"\n[调试] 开始推送：")
        print(f"  推送标题：{title}")
        print(f"  推送内容：{content[:100]}...")
        print(f"  Bark请求URL：{bark_url}")
    
    try:
        response = requests.get(bark_url, timeout=10)
        if response.status_code == 200 and response.json().get("code") == 200:
            print(f"✅ 推送成功 | PID={reply['pid']} | 页码={reply['page']}")
        else:
            print(f"❌ 推送失败 | 状态码={response.status_code} | 响应={response.text}")
    except Exception as e:
        print(f"❌ 推送异常 | 错误={str(e)}")

# ===================== 单任务执行（核心逻辑，强化调试） =====================
def run_task(task):
    """执行单个监控任务（带完整调试日志）"""
    print("\n" + "="*80)
    print(f"开始执行任务：{task['name']}")
    print(f"任务URL：{task['url']}")
    print("="*80)
    
    # 1. 加载上次页数 + 获取总页数
    last_page = load_last_page(task)
    total_page = get_total_page(task)
    
    # 关键调试：打印核心页数信息
    print(f"\n📌 核心页数信息（重点关注）：")
    print(f"   帖子总页数：{total_page}")
    print(f"   上次爬取页数：{last_page if last_page else '首次运行'}")
    print(f"   本次爬取目标：{'最后一页（首次运行）' if last_page is None else f'第{last_page+1}页 → 第{total_page}页'}")
    
    # 2. 首次运行逻辑（只爬最后一页）
    if last_page is None:
        print("\n🚀 首次运行模式：只爬最后一页，提取最新3条")
        # 爬取最后一页
        last_page_replies = crawl_page(task, total_page)
        
        # 调试：打印最后一页所有回复
        print(f"\n📊 最后一页（第{total_page}页）爬取结果：")
        print(f"   提取到回复数量：{len(last_page_replies)}")
        if last_page_replies:
            print(f"   回复列表（按PID倒序）：")
            # 按PID倒序（保证最新在前）
            last_page_replies.sort(key=lambda x: x["pid"], reverse=True)
            for idx, r in enumerate(last_page_replies):
                print(f"     {idx+1}. PID={r['pid']} | 时间={r['time']} | 页码={r['page']}")
        
        # 筛选要推送的3条
        push_list = last_page_replies[:FIRST_RUN_PUSH_LIMIT]
        # 记录最后页数（总页数）
        save_last_page(task, total_page)
    
    # 3. 非首次运行（增量爬取）
    else:
        start_page = last_page + 1
        end_page = total_page
        
        if start_page > end_page:
            print("\nℹ️ 非首次运行：无新页面，无需爬取")
            return
        
        print(f"\n🚀 增量爬取模式：第{start_page}页 → 第{end_page}页")
        all_replies = []
        # 爬取所有新页面
        for page in range(start_page, end_page + 1):
            page_replies = crawl_page(task, page)
            all_replies.extend(page_replies)
        
        # 调试：打印增量爬取结果
        print(f"\n📊 增量爬取结果：")
        print(f"   累计提取回复数量：{len(all_replies)}")
        if all_replies:
            all_replies.sort(key=lambda x: x["pid"], reverse=True)
            print(f"   最新回复前5条：")
            for idx, r in enumerate(all_replies[:5]):
                print(f"     {idx+1}. PID={r['pid']} | 时间={r['time']} | 页码={r['page']}")
        
        # 筛选未推送的回复
        pushed_pids = load_pushed_pids(task)
        push_list = [r for r in all_replies if r["pid"] not in pushed_pids]
        # 记录最后页数（最新页）
        save_last_page(task, end_page)
    
    # 4. 执行推送 + 记录PID
    print(f"\n🎯 最终推送列表：")
    if push_list:
        print(f"   本次推送数量：{len(push_list)}")
        for idx, r in enumerate(push_list):
            print(f"     {idx+1}. PID={r['pid']} | 页码={r['page']} | 时间={r['time']}")
            push(task, r)
        # 记录已推送的PID
        append_pushed_pids(task, [r["pid"] for r in push_list])
    else:
        print(f"   无新回复需要推送")

# ===================== 主入口 =====================
if __name__ == "__main__":
    print(f"\n=== NGA多用户监控脚本启动 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"📝 调试模式：{'开启' if DEBUG_MODE else '关闭'}")
    print(f"📋 监控任务数量：{len(MONITOR_TASKS)}")
    
    # 遍历执行所有任务
    for task in MONITOR_TASKS:
        run_task(task)
    
    print(f"\n=== NGA多用户监控脚本结束 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
