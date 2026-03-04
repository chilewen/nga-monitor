import requests
import re
import os
import json
import sys
import subprocess
from datetime import datetime

# ===================== 【配置区：只改这里】 =====================
MONITOR_TASKS = [
    {
        "url": "https://bbs.nga.cn/read.php?tid=45502551&authorid=370218",
        "name": "猫猫",
        "meta_file": "nga_monitor/45502551_370218_meta.json"
    },
]

BARK_KEY = os.getenv("BARK_KEY")
NGA_COOKIE = os.getenv("NGA_COOKIE")

FIRST_RUN_PUSH_LIMIT = 3
MAX_EMPTY_PAGES = 3
MAX_PAGE_LIMIT = 100
MAX_RETRY_TIMES = 2
# =====================================================================

# ===================== GitHub文件操作 =====================
def git_config_and_checkout():
    try:
        subprocess.run(["git", "config", "--global", "user.name", "GitHub Actions"], check=True, capture_output=True)
        subprocess.run(["git", "config", "--global", "user.email", "actions@github.com"], check=True, capture_output=True)
        subprocess.run(["git", "checkout", "main"], check=True, capture_output=True)
        subprocess.run(["git", "pull", "origin", "main", "--allow-unrelated-histories"], check=True, capture_output=True)
        print("✅ Git配置+分支切换+拉取成功")
    except Exception as e:
        print(f"⚠️ Git配置/拉取警告（不影响本地保存）：{e}")

def load_github_meta(meta_file_path):
    os.makedirs(os.path.dirname(meta_file_path), exist_ok=True)
    default_meta = {"last_page": 0, "pushed_pids": []}
    
    try:
        if os.path.exists(meta_file_path):
            with open(meta_file_path, "r", encoding="utf-8") as fp:
                meta = json.load(fp)
                meta["last_page"] = meta.get("last_page", 0)
                meta["pushed_pids"] = meta.get("pushed_pids", [])
                print(f"✅ 读取元数据成功：{meta_file_path}")
            return meta
        else:
            with open(meta_file_path, "w", encoding="utf-8") as fp:
                json.dump(default_meta, fp, ensure_ascii=False, indent=2)
            print(f"ℹ️ 首次创建元数据文件：{meta_file_path}")
            return default_meta
    except Exception as e:
        print(f"⚠️ 读取元数据失败，使用默认值：{e}")
        return default_meta

def save_github_meta(meta_file_path, meta):
    try:
        with open(meta_file_path, "w", encoding="utf-8") as fp:
            json.dump(meta, fp, ensure_ascii=False, indent=2)
        print(f"✅ 元数据文件本地写入成功：{meta_file_path}")
        
        if os.getenv("GITHUB_ACTIONS") == "true" and os.getenv("GITHUB_TOKEN"):
            try:
                git_config_and_checkout()
                subprocess.run(["git", "add", meta_file_path], check=True, capture_output=True)
                status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
                if status:
                    commit_msg = f"更新NGA监控元数据：{os.path.basename(meta_file_path)}"
                    subprocess.run(["git", "commit", "-m", commit_msg], check=True, capture_output=True)
                    remote_url = f"https://{os.getenv('GITHUB_TOKEN')}@github.com/{os.getenv('GITHUB_REPO', 'unknown')}.git"
                    subprocess.run(["git", "push", remote_url, "main"], check=True, capture_output=True)
                    print(f"✅ 元数据文件提交到GitHub成功")
                else:
                    print(f"ℹ️ 无文件变更，无需提交")
            except Exception as e:
                print(f"⚠️ 提交到GitHub失败（本地文件已保存）：{e}")
        else:
            print(f"ℹ️ 非Actions环境/无token，仅本地保存文件")
    except Exception as e:
        print(f"❌ 保存元数据失败：{e}")

# ===================== 核心修复：页面有效性校验 =====================
def is_page_valid(html):
    """彻底放宽校验：只排除明确无效的页面（避免误杀有效内容）"""
    # 只判定包含以下关键词的页面为无效（绝对无效场景）
    invalid_keywords = [
        "404 Not Found",
        "500 Internal Server Error",
        "服务器错误",
        "页面不存在",
        "该帖子已被删除",
        "访问被拒绝",
        "请登录后才能查看"  # 未登录的无效页面
    ]
    # 只要包含任意一个绝对无效关键词，才判定为无效
    for keyword in invalid_keywords:
        if keyword in html:
            return False
    # 内容长度≥1000字符（排除空页面/极小页面）
    if len(html) < 1000:
        return False
    # 其余情况均判定为有效页面
    return True

# ===================== 爬取逻辑 =====================
def crawl_page_with_retry(task, page):
    if page < 1 or page > MAX_PAGE_LIMIT:
        print(f"[调试] 第{page}页：页码非法，返回空列表")
        return []
    
    base_url = task["url"].split("&page=")[0] if "&page=" in task["url"] else task["url"]
    crawl_url = f"{base_url}&page={page}" if page > 1 else base_url
    
    for retry in range(MAX_RETRY_TIMES + 1):
        try:
            print(f"\n[调试] 爬取第{page}页（重试{retry}/{MAX_RETRY_TIMES}），URL：{crawl_url}")
            
            session = requests.Session()
            session.headers.update(HEADERS)
            response = session.get(crawl_url, timeout=30, allow_redirects=True)
            response.encoding = "gbk"
            html = response.text
            
            if not is_page_valid(html):
                print(f"⚠️ 第{page}页无效（包含无效关键词/长度不足），重试中...")
                continue
            
            # 多规则匹配回复块（适配NGA实际结构）
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
                    print(f"[调试] 第{page}页：匹配到{len(posts)}个回复块")
                    break
            
            page_replies = []
            pid_set = set()
            for idx, post in enumerate(posts):
                # 提取核心信息（适配NGA实际格式）
                pid_match = re.search(r'pid(\d+)Anchor', post) or re.search(r'id="pid(\d+)"', post)
                time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2})', post)
                content_match = re.search(r'class=\'(?:postcontent ubbcode|message)\'>([\s\S]*?)</(?:span|div)>', post)
                
                if not (pid_match and content_match):
                    continue
                
                pid = pid_match.group(1)
                if pid in pid_set:
                    continue
                pid_set.add(pid)
                
                reply_time = time_match.group(1) if time_match else "1970-01-01 00:00"
                content = content_match.group(1)
                # 清理内容（保留核心文本）
                content = re.sub(r"<.*?>", "", content)
                content = re.sub(r"\[quote\][\s\S]*?\[/quote\]", "", content)
                content = re.sub(r"\[img\].*?\[/img\]", "[图片]", content)
                content = re.sub(r"\s+", " ", content).strip()
                content = re.sub(r"[\x00-\x1f\x7f]", "", content)
                
                if len(content) < 3:
                    continue
                
                page_replies.append({
                    "pid": pid,
                    "time": reply_time,
                    "content": content,
                    "page": page
                })
            
            print(f"[调试] 第{page}页：提取到{len(page_replies)}条有效回复")
            return page_replies
        
        except Exception as e:
            print(f"❌ 爬取第{page}页失败（重试{retry}/{MAX_RETRY_TIMES}）：{str(e)[:100]}")
            if retry >= MAX_RETRY_TIMES:
                print(f"⚠️ 第{page}页重试耗尽，返回空列表")
                return []
    
    return []

def crawl_all_pages(task):
    try:
        meta = load_github_meta(task["meta_file"])
        start_page = meta["last_page"] + 1
        all_replies = []
        empty_page_count = 0
        current_page = start_page
        global_pid_set = set()
        
        print(f"\n🚀 开始遍历页面：从第{start_page}页开始，连续{MAX_EMPTY_PAGES}页无回复则停止")
        
        while empty_page_count < MAX_EMPTY_PAGES and current_page <= MAX_PAGE_LIMIT:
            page_replies = crawl_page_with_retry(task, current_page)
            if not isinstance(page_replies, list):
                page_replies = []
            
            # 全局去重
            unique_replies = []
            for r in page_replies:
                if r["pid"] not in global_pid_set:
                    global_pid_set.add(r["pid"])
                    unique_replies.append(r)
            
            if unique_replies:
                all_replies.extend(unique_replies)
                empty_page_count = 0
                print(f"✅ 第{current_page}页：爬取到{len(unique_replies)}条唯一回复")
            else:
                empty_page_count += 1
                print(f"ℹ️ 第{current_page}页：无有效回复（连续空页{empty_page_count}/{MAX_EMPTY_PAGES}）")
            
            current_page += 1
        
        last_crawled_page = current_page - empty_page_count - 1
        if last_crawled_page < start_page:
            last_crawled_page = start_page - 1
        
        print(f"\n[调试] 遍历完成：最后有效页码={last_crawled_page}，累计唯一回复={len(all_replies)}")
        return all_replies, last_crawled_page, meta
    except Exception as e:
        print(f"❌ 遍历页面失败：{e}")
        return [], 0, {"last_page": 0, "pushed_pids": []}

# ===================== 推送函数+请求头 =====================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Cookie": NGA_COOKIE or "",
    "Referer": "https://bbs.nga.cn/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
}

def push(task, reply):
    if not BARK_KEY:
        print("[调试] 跳过推送：BARK_KEY未配置")
        return
    
    task_key = re.search(r'tid=(\d+)', task["url"]).group(1) if re.search(r'tid=(\d+)', task["url"]) else "unknown"
    title = f"【NGA-{task_key}】{task['name']} 新回复"
    content = reply["content"][:200] + "..." if len(reply["content"]) > 200 else reply["content"]
    
    print(f"\n[调试] 推送：标题={title}，内容={content[:50]}...")
    
    try:
        bark_api = f"https://api.day.app/{BARK_KEY}/"
        params = {"title": title, "body": content, "isArchive": 1}
        response = requests.get(bark_api, params=params, timeout=10)
        response_data = response.json()
        if response.status_code == 200 and response_data.get("code") == 200:
            print(f"✅ 推送成功 | PID={reply['pid']} | 页码={reply['page']}")
        else:
            print(f"❌ 推送失败 | 状态码={response.status_code} | 响应={response_data}")
    except Exception as e:
        print(f"❌ 推送异常：{e}")

# ===================== 主逻辑 =====================
def run_task(task):
    print("\n" + "="*80)
    print(f"开始执行任务：{task['name']}")
    print(f"任务URL：{task['url']}")
    print(f"元数据文件：{task['meta_file']}")
    print("="*80)
    
    try:
        all_replies, last_crawled_page, meta = crawl_all_pages(task)
        pushed_pids = set(meta["pushed_pids"])
        
        new_replies = [r for r in all_replies if r["pid"] not in pushed_pids]
        if new_replies:
            new_replies.sort(key=lambda x: (x["time"], x["pid"]), reverse=True)
            print(f"\n📊 筛选出新回复数量：{len(new_replies)}")
            
            print(f"\n[调试] 新回复前5条：")
            for idx, r in enumerate(new_replies[:5]):
                print(f"  {idx+1}. PID={r['pid']} | 时间={r['time']} | 内容={r['content'][:30]}...")
        else:
            print(f"\nℹ️ 未发现新回复，无需推送")
            meta["last_page"] = last_crawled_page
            save_github_meta(task["meta_file"], meta)
            return
        
        is_first_run = len(pushed_pids) == 0
        push_list = new_replies[:FIRST_RUN_PUSH_LIMIT] if is_first_run else new_replies
        print(f"\n🎯 {'首次运行：推送最新' if is_first_run else '非首次运行：推送全部'}{len(push_list)}条回复")
        
        if push_list:
            push_pid_set = set()
            final_push_list = []
            for r in push_list:
                if r["pid"] not in push_pid_set:
                    push_pid_set.add(r["pid"])
                    final_push_list.append(r)
            
            print(f"\n🎯 最终推送{len(final_push_list)}条回复")
            for idx, r in enumerate(final_push_list):
                print(f"\n--- 推送第{idx+1}条 ---")
                push(task, r)
            
            new_pids = [r["pid"] for r in new_replies]
            meta["pushed_pids"] = list(pushed_pids.union(new_pids))
            meta["last_page"] = last_crawled_page
            save_github_meta(task["meta_file"], meta)
        else:
            print(f"\nℹ️ 无有效推送内容")
            meta["last_page"] = last_crawled_page
            save_github_meta(task["meta_file"], meta)
    except Exception as e:
        print(f"❌ 执行任务失败：{e}")

if __name__ == "__main__":
    print(f"\n=== NGA多用户监控脚本启动 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"📝 GitHub Actions环境：{os.getenv('GITHUB_ACTIONS', 'false')}")
    
    if not os.getenv("GITHUB_REPO"):
        os.environ["GITHUB_REPO"] = os.getenv("GITHUB_REPOSITORY", "unknown/unknown")
    
    for task in MONITOR_TASKS:
        run_task(task)
    
    print(f"\n=== NGA多用户监控脚本结束 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"\n📂 元数据文件状态：")
    for task in MONITOR_TASKS:
        if os.path.exists(task["meta_file"]):
            print(f"   - {task['meta_file']}：存在（大小：{os.path.getsize(task['meta_file'])} 字节）")
        else:
            print(f"   - {task['meta_file']}：不存在")
