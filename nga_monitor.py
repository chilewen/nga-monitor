import requests
import re
import os
import json
from datetime import datetime

# ===================== 【配置区：你只需要改这里】 =====================
MONITOR_TASKS = [
    {
        "url": "https://bbs.nga.cn/read.php?tid=45502551&authorid=370218",
        "name": "猫猫"
    },
    # 可添加更多任务
    # {
    #     "url": "https://bbs.nga.cn/read.php?tid=45502551&authorid=26529713",
    #     "name": "小雨"
    # },
]

BARK_KEY = os.getenv("BARK_KEY")
NGA_COOKIE = os.getenv("NGA_COOKIE")

FIRST_RUN_PUSH_LIMIT = 3       # 首次只推最新3条
MAX_EMPTY_PAGES = 3            # 连续N页无回复则停止遍历
DEBUG_MODE = True              # 调试模式：打印详细日志
MAX_PAGE_LIMIT = 100           # 最大遍历页数（防止无限循环）
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

# ===================== 工具函数：修复文件读写+唯一记录 =====================
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

def get_task_meta_file(task):
    """任务元数据文件（统一存储页数+已推送PID，避免文件读写问题）"""
    return f"nga_monitor/{get_task_key(task)}_meta.json"

def load_task_meta(task):
    """加载任务元数据（修复首次运行判定）"""
    meta_file = get_task_meta_file(task)
    default_meta = {
        "last_page": 0,
        "pushed_pids": []
    }
    
    try:
        if os.path.exists(meta_file):
            with open(meta_file, "r", encoding="utf-8") as fp:
                meta = json.load(fp)
                # 兼容旧数据格式
                meta["last_page"] = meta.get("last_page", 0)
                meta["pushed_pids"] = meta.get("pushed_pids", [])
                if DEBUG_MODE:
                    print(f"[调试] 加载任务元数据：最后页数={meta['last_page']}，已推送PID={len(meta['pushed_pids'])}（文件：{meta_file}）")
            return meta
    except Exception as e:
        print(f"[警告] 加载元数据失败，使用默认值：{e}")
    
    if DEBUG_MODE:
        print(f"[调试] 未找到元数据文件，使用默认值（首次运行）（文件：{meta_file}）")
    return default_meta

def save_task_meta(task, meta):
    """保存任务元数据（原子操作，避免写入失败）"""
    meta_file = get_task_meta_file(task)
    try:
        # 先写入临时文件，再替换（防止文件损坏）
        temp_file = f"{meta_file}.tmp"
        with open(temp_file, "w", encoding="utf-8") as fp:
            json.dump(meta, fp, ensure_ascii=False, indent=2)
        os.replace(temp_file, meta_file)
        
        if DEBUG_MODE:
            print(f"[调试] 保存任务元数据：最后页数={meta['last_page']}，已推送PID={len(meta['pushed_pids'])}（文件：{meta_file}）")
    except Exception as e:
        print(f"[错误] 保存元数据失败：{e}")

# ===================== 核心函数：修复爬取逻辑（去重+页码校验） =====================
def crawl_page(task, page):
    """爬取指定页数（去重+严格页码校验）"""
    # 页码合法性校验
    if page < 1 or page > MAX_PAGE_LIMIT:
        if DEBUG_MODE:
            print(f"[调试] 第{page}页：页码非法（<1或>{MAX_PAGE_LIMIT}），跳过")
        return []
    
    # 构造带页码的URL（严格拼接）
    base_url = task["url"].split("&page=")[0] if "&page=" in task["url"] else task["url"]
    crawl_url = f"{base_url}&page={page}" if page > 1 else base_url
    if DEBUG_MODE:
        print(f"\n[调试] 开始爬取页面，页码：{page}，请求URL：{crawl_url}")
    
    try:
        response = requests.get(crawl_url, headers=HEADERS, timeout=20)
        response.encoding = "gbk"  # 强制NGA编码
        html = response.text
        
        # 多规则匹配回复块
        post_patterns = [
            re.compile(r'<table class=\'forumbox postbox\'[^>]*>[\s\S]*?</table>', re.IGNORECASE),
            re.compile(r'<table class=\'postbox\'[^>]*>[\s\S]*?</table>', re.IGNORECASE),
            re.compile(r'<div class=\'postrow\'[^>]*>[\s\S]*?</div>', re.IGNORECASE),
            re.compile(r'<div id=\'postcontainer\d+\'[^>]*>[\s\S]*?</div>', re.IGNORECASE)
        ]
        
        posts = []
        for pattern in post_patterns:
            posts = pattern.findall(html)
            if posts:
                if DEBUG_MODE:
                    print(f"[调试] 第{page}页：使用规则{post_patterns.index(pattern)+1}匹配到回复块数量：{len(posts)}")
                break
        
        if not posts:
            if DEBUG_MODE:
                print(f"[调试] 第{page}页：无回复块")
            return []
        
        # 存储当前页的有效回复（按PID去重）
        page_replies = []
        pid_set = set()
        
        for idx, post in enumerate(posts):
            # 精准提取核心信息
            pid_match = re.search(r'pid(\d+)Anchor', post) or re.search(r'id="pid(\d+)"', post)
            time_match = re.search(r'title=\'reply time\'>(\d{4}-\d{2}-\d{2} \d{2}:\d{2})', post) or re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2})', post)
            content_match = re.search(r'class=\'postcontent ubbcode\'>([\s\S]*?)</span>', post) or re.search(r'class=\'message\'>([\s\S]*?)</div>', post)
            
            if not (pid_match and content_match):
                continue
            
            pid = pid_match.group(1)
            # 去重：同一页跳过重复PID
            if pid in pid_set:
                if DEBUG_MODE:
                    print(f"[调试] 第{page}页回复块{idx+1}：PID={pid}重复，跳过")
                continue
            pid_set.add(pid)
            
            reply_time = time_match.group(1) if time_match else "1970-01-01 00:00"
            content = content_match.group(1)
            # 深度清理内容
            content = re.sub(r"<.*?>", "", content)          # 去掉HTML标签
            content = re.sub(r"\[quote\][\s\S]*?\[/quote\]", "", content)  # 去掉引用
            content = re.sub(r"\[img\].*?\[/img\]", "[图片]", content)     # 替换图片
            content = re.sub(r"\s+", " ", content).strip()   # 清理空格
            content = re.sub(r"[\x00-\x1f\x7f]", "", content) # 去掉不可见字符
            
            # 过滤无效内容
            if len(content) < 3:
                continue
            
            reply = {
                "pid": pid,
                "time": reply_time,
                "content": content,
                "page": page
            }
            page_replies.append(reply)
            
            if DEBUG_MODE:
                print(f"[调试] 第{page}页有效回复{len(page_replies)}：PID={pid} | 时间={reply_time} | 内容={content[:50]}...")
        
        return page_replies
    
    except Exception as e:
        print(f"[错误] 爬取第{page}页失败：{str(e)}")
        return []

def crawl_all_pages(task):
    """遍历所有页（修复页码计数+全局去重）"""
    meta = load_task_meta(task)
    start_page = meta["last_page"] + 1
    all_replies = []
    empty_page_count = 0
    current_page = start_page
    global_pid_set = set()  # 全局PID去重（跨页）
    
    print(f"\n🚀 开始遍历页面：从第{start_page}页开始，连续{MAX_EMPTY_PAGES}页无回复或>{MAX_PAGE_LIMIT}页则停止")
    
    while empty_page_count < MAX_EMPTY_PAGES and current_page <= MAX_PAGE_LIMIT:
        page_replies = crawl_page(task, current_page)
        
        # 全局去重
        unique_replies = []
        for r in page_replies:
            if r["pid"] not in global_pid_set:
                global_pid_set.add(r["pid"])
                unique_replies.append(r)
        
        if unique_replies:
            all_replies.extend(unique_replies)
            empty_page_count = 0
            print(f"✅ 第{current_page}页：爬取到{len(unique_replies)}条唯一回复（去重后）")
        else:
            empty_page_count += 1
            print(f"ℹ️ 第{current_page}页：无有效回复（连续空页{empty_page_count}/{MAX_EMPTY_PAGES}）")
        
        current_page += 1
    
    # 计算最后有效页数
    last_crawled_page = current_page - empty_page_count - 1
    if last_crawled_page < start_page:
        last_crawled_page = start_page - 1
    
    if DEBUG_MODE:
        print(f"\n[调试] 遍历完成：")
        print(f"  起始页码：{start_page}")
        print(f"  最后爬取页码：{current_page - 1}")
        print(f"  最后有效页码：{last_crawled_page}")
        print(f"  累计提取唯一回复数量：{len(all_replies)}")
    
    return all_replies, last_crawled_page

# ===================== 推送函数（保持稳定） =====================
def push(task, reply):
    """推送回复到Bark（稳定版）"""
    if not BARK_KEY:
        print("[调试] 跳过推送：BARK_KEY未配置")
        return
    
    task_key = get_task_key(task)
    tid = task_key.split('_')[0]
    title = f"【NGA-{tid}】{task['name']} 新回复"
    content = reply["content"][:200] + "..." if len(reply["content"]) > 200 else reply["content"]
    
    if DEBUG_MODE:
        print(f"\n[调试] 开始推送：")
        print(f"  推送标题：{title}")
        print(f"  推送内容：{content}")
    
    bark_api = f"https://api.day.app/{BARK_KEY}/"
    params = {
        "title": title,
        "body": content,
        "url": f"https://bbs.nga.cn/read.php?tid={tid}#pid{reply['pid']}Anchor",
        "isArchive": 1
    }
    
    try:
        response = requests.get(bark_api, params=params, timeout=10)
        response_data = response.json()
        if response.status_code == 200 and response_data.get("code") == 200:
            print(f"✅ 推送成功 | PID={reply['pid']} | 页码={reply['page']}")
        else:
            print(f"❌ 推送失败 | 状态码={response.status_code} | 响应={response_data}")
    except Exception as e:
        print(f"❌ 推送异常 | 错误={str(e)}")

# ===================== 单任务执行（修复核心逻辑） =====================
def run_task(task):
    """执行单个监控任务（修复首次运行+重复推送）"""
    print("\n" + "="*80)
    print(f"开始执行任务：{task['name']}")
    print(f"任务URL：{task['url']}")
    print("="*80)
    
    # 1. 加载元数据
    meta = load_task_meta(task)
    pushed_pids = set(meta["pushed_pids"])
    
    # 2. 遍历所有页
    all_replies, last_crawled_page = crawl_all_pages(task)
    
    # 3. 筛选新回复（全局唯一+未推送）
    new_replies = []
    for r in all_replies:
        if r["pid"] not in pushed_pids:
            new_replies.append(r)
    
    # 4. 按时间+PID倒序排序（保证最新在前）
    if new_replies:
        new_replies.sort(key=lambda x: (x["time"], x["pid"]), reverse=True)
        print(f"\n📊 筛选出新回复数量：{len(new_replies)}（按时间倒序排序）")
        
        if DEBUG_MODE:
            print(f"\n[调试] 新回复列表（前10条）：")
            for idx, r in enumerate(new_replies[:10]):
                print(f"  {idx+1}. PID={r['pid']} | 时间={r['time']} | 页码={r['page']} | 内容={r['content'][:30]}...")
    else:
        print(f"\nℹ️ 未发现新回复，无需推送")
        # 更新最后页数
        meta["last_page"] = last_crawled_page
        save_task_meta(task, meta)
        return
    
    # 5. 判定是否首次运行（已推送PID为空）
    is_first_run = len(pushed_pids) == 0
    if is_first_run:
        push_list = new_replies[:FIRST_RUN_PUSH_LIMIT]
        print(f"\n🎯 首次运行：推送最新{len(push_list)}条回复（共{len(new_replies)}条新回复）")
    else:
        push_list = new_replies
        print(f"\n🎯 非首次运行：推送全部{len(push_list)}条新回复")
    
    # 6. 执行推送（确保内容唯一）
    if push_list:
        # 再次去重（兜底）
        push_pid_set = set()
        final_push_list = []
        for r in push_list:
            if r["pid"] not in push_pid_set:
                push_pid_set.add(r["pid"])
                final_push_list.append(r)
        
        print(f"\n🎯 最终推送列表（去重后）：{len(final_push_list)}条")
        for idx, r in enumerate(final_push_list):
            print(f"\n--- 推送第{idx+1}条 ---")
            push(task, r)
        
        # 7. 更新元数据（添加新推送的PID）
        new_pids = [r["pid"] for r in new_replies]
        meta["pushed_pids"] = list(pushed_pids.union(new_pids))  # 合并已推送+新PID
        meta["last_page"] = last_crawled_page
        save_task_meta(task, meta)
    else:
        print(f"\nℹ️ 无需要推送的新回复")
        meta["last_page"] = last_crawled_page
        save_task_meta(task, meta)

# ===================== 主入口 =====================
if __name__ == "__main__":
    print(f"\n=== NGA多用户监控脚本启动 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"📝 调试模式：{'开启' if DEBUG_MODE else '关闭'}")
    print(f"📋 监控任务数量：{len(MONITOR_TASKS)}")
    print(f"🔧 连续空页停止阈值：{MAX_EMPTY_PAGES}页")
    print(f"🔧 最大遍历页数：{MAX_PAGE_LIMIT}页")
    print(f"🎯 首次推送限制：{FIRST_RUN_PUSH_LIMIT}条最新回复")
    
    # 遍历执行所有任务
    for task in MONITOR_TASKS:
        run_task(task)
    
    print(f"\n=== NGA多用户监控脚本结束 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
