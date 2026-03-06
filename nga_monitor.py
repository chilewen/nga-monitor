import requests
import re
import os
import json
import sys
import subprocess
from datetime import datetime

# ===================== 【核心配置区】 =====================
MONITOR_TASKS = [
    {
        "url": "https://bbs.nga.cn/read.php?tid=45502551&authorid=370218",
        "name": "猫猫",
        "meta_file": "nga_monitor/45502551_370218_meta.json"
    },
     {
        "url": "https://bbs.nga.cn/read.php?tid=45502551&authorid=26529713",
        "name": "小雨",
        "meta_file": "nga_monitor/45502551_26529713_meta.json"
    },
    {
        "url": "https://bbs.nga.cn/read.php?tid=45974302&authorid=150058",
        "name": "小狼",
        "meta_file": "nga_monitor/45974302_150058_meta.json"
    }
]

BARK_KEY = os.getenv("BARK_KEY")
NGA_COOKIE = os.getenv("NGA_COOKIE")

# 固定配置
FIRST_RUN_PUSH_LIMIT = 3
MAX_EMPTY_PAGES = 3
MAX_PAGE_LIMIT = 100
MAX_RETRY_TIMES = 2
NGA_AUTHOR_OPT = "opt=262144"  # 两个帖子都支持该参数
# =====================================================================

# ===================== 1. Cookie失效提醒 =====================
def push_cookie_expired_alert():
    if not BARK_KEY:
        print("⚠️ BARK_KEY未配置，无法推送Cookie过期提醒")
        return
    
    alert_title = "🚨 NGA Cookie 已过期！紧急更新！"
    alert_content = "Cookie失效导致无法爬取内容，请立即更新GitHub Secrets中的NGA_COOKIE！"
    
    for i in range(3):
        try:
            bark_api = f"https://api.day.app/{BARK_KEY}/{alert_title}/{alert_content}"
            params = {"sound": "alert", "isArchive": 1, "group": "NGA监控"}
            response = requests.get(bark_api, params=params, timeout=8)
            if response.status_code == 200:
                print(f"✅ Cookie过期提醒第 {i+1} 条推送成功")
        except Exception as e:
            print(f"❌ Cookie过期提醒第 {i+1} 条推送异常：{str(e)[:50]}")

def is_cookie_invalid(html):
    invalid_keywords = ["请登录后查看", "请登录后继续", "您需要登录", "登录后使用", "用户登录", "passport", "登录NGA"]
    return any(kw in html for kw in invalid_keywords)

# ===================== 2. 元数据操作（强制重建损坏文件） =====================
def git_config():
    try:
        subprocess.run(["git", "config", "--global", "user.name", "GitHub Actions"], check=True, capture_output=True)
        subprocess.run(["git", "config", "--global", "user.email", "actions@github.com"], check=True, capture_output=True)
        print("✅ Git配置完成")
    except Exception as e:
        print(f"⚠️ Git配置警告：{e}")

def load_meta(meta_file_path):
    """强制重建损坏的元数据文件"""
    os.makedirs(os.path.dirname(meta_file_path), exist_ok=True)
    default_meta = {"last_page": 0, "pushed_pids": []}
    
    try:
        if os.path.exists(meta_file_path):
            # 尝试读取，失败则直接删除重建
            with open(meta_file_path, "r", encoding="utf-8") as fp:
                meta = json.load(fp)
                # 验证元数据格式是否正确
                if not isinstance(meta, dict) or "last_page" not in meta or "pushed_pids" not in meta:
                    raise ValueError("元数据格式错误")
                meta["last_page"] = int(meta.get("last_page", 0))
                meta["pushed_pids"] = list(meta.get("pushed_pids", []))
            print(f"✅ 读取元数据成功：{meta_file_path}")
            return meta
        else:
            # 新建文件
            with open(meta_file_path, "w", encoding="utf-8") as fp:
                json.dump(default_meta, fp, ensure_ascii=False, indent=2)
            print(f"ℹ️ 首次创建元数据文件：{meta_file_path}")
            return default_meta
    except Exception as e:
        # 读取失败/格式错误 → 删除并重建
        print(f"⚠️ 元数据文件损坏：{e}，删除并重建")
        if os.path.exists(meta_file_path):
            os.remove(meta_file_path)
        with open(meta_file_path, "w", encoding="utf-8") as fp:
            json.dump(default_meta, fp, ensure_ascii=False, indent=2)
        return default_meta

def save_meta(meta_file_path, meta):
    try:
        with open(meta_file_path, "w", encoding="utf-8") as fp:
            json.dump(meta, fp, ensure_ascii=False, indent=2)
        print(f"✅ 元数据本地保存成功：{meta_file_path}")
        
        if os.getenv("GITHUB_ACTIONS") == "true":
            try:
                git_config()
                subprocess.run(["git", "add", meta_file_path], check=True, capture_output=True)
                status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
                if status:
                    subprocess.run(["git", "commit", "-m", f"更新NGA元数据：{os.path.basename(meta_file_path)}"], check=True, capture_output=True)
                    subprocess.run(["git", "push", "origin", "main"], check=True, capture_output=True)
                    print(f"✅ 元数据提交到GitHub成功")
                else:
                    print(f"ℹ️ 无元数据变更，无需提交")
            except Exception as e:
                print(f"⚠️ 元数据提交失败（本地已保存）：{e}")
    except Exception as e:
        print(f"❌ 保存元数据失败：{e}")

# ===================== 3. 页面爬取（核心修复：适配opt参数的页面解析） =====================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Cookie": NGA_COOKIE or "",
    "Referer": "https://bbs.nga.cn/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache"
}

def is_page_valid(html):
    """优化：适配opt参数的页面有效性判定"""
    # 只保留绝对无效的关键词
    invalid_keywords = ["404", "500", "服务器错误", "页面不存在", "该帖子已被删除"]
    if any(kw in html for kw in invalid_keywords):
        return False
    # 极低阈值：适配opt页面内容少的情况
    return len(html) > 100  # 从300降到100

def get_correct_url(task_url, page):
    """生成带opt=262144的正确URL（两个帖子都支持）"""
    base_url = re.sub(r'&page=\d+', '', task_url)
    if NGA_AUTHOR_OPT not in base_url:
        base_url += f"&{NGA_AUTHOR_OPT}"
    if page > 1:
        final_url = f"{base_url}&page={page}"
    else:
        final_url = base_url
    return final_url

def crawl_page(task, page):
    """修复：适配opt页面的回复提取正则"""
    if page < 1 or page > MAX_PAGE_LIMIT:
        return []
    
    crawl_url = get_correct_url(task["url"], page)
    
    for retry in range(MAX_RETRY_TIMES + 1):
        try:
            print(f"\n[调试] 爬取第{page}页（重试{retry}/{MAX_RETRY_TIMES}）：{crawl_url}")
            response = requests.get(crawl_url, headers=HEADERS, timeout=20, allow_redirects=True)
            response.encoding = "gbk"
            html = response.text
            
            # Cookie失效检测
            if is_cookie_invalid(html):
                print("❌ 检测到Cookie失效！")
                push_cookie_expired_alert()
                sys.exit(1)
            
            if not is_page_valid(html):
                print(f"⚠️ 第{page}页内容无效，重试中...")
                continue
            
            # ===================== 核心修复：适配opt页面的正则 =====================
            # 优化正则：匹配opt页面的回复结构（更宽松）
            post_pattern = re.compile(r'<table[^>]*class="?forumbox postbox"?[^>]*>[\s\S]*?</table>', re.IGNORECASE)
            posts = post_pattern.findall(html)
            
            if not posts:
                # 备用正则：匹配NGA另一种回复结构
                post_pattern = re.compile(r'<div[^>]*class="?postbox"?[^>]*>[\s\S]*?</div>', re.IGNORECASE)
                posts = post_pattern.findall(html)
            
            replies = []
            pid_set = set()
            for post in posts:
                # 适配opt页面的PID提取
                pid_match = re.search(r'pid(\d+)Anchor|data-pid="(\d+)"|id="pid(\d+)"', post)
                time_match = re.search(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', post)
                # 适配opt页面的内容提取（更宽松）
                content_match = re.search(r'class=["\']postcontent[^"\']*["\']>([\s\S]*?)</(span|div)>', post)
                
                if not (pid_match and content_match):
                    continue
                
                # 处理PID匹配的多组结果
                pid = pid_match.group(1) or pid_match.group(2) or pid_match.group(3)
                if not pid or pid in pid_set:
                    continue
                pid_set.add(pid)
                
                reply_time = f"{time_match.group(1)} {time_match.group(2)}" if time_match else "1970-01-01 00:00"
                content = content_match.group(1)
                # 清理内容（更彻底）
                content = re.sub(r'<[^>]*>', '', content)  # 移除所有标签
                content = re.sub(r'\[quote[\s\S]*?\[/quote\]', '', content)  # 移除引用
                content = re.sub(r'\[img[\s\S]*?\[/img\]', '[图片]', content)
                content = re.sub(r'\s+', ' ', content).strip()
                
                if len(content) < 2:
                    continue
                
                replies.append({
                    "pid": pid,
                    "time": reply_time,
                    "content": content[:300],
                    "page": page
                })
            
            print(f"✅ 第{page}页提取到{len(replies)}条有效回复")
            return replies
        
        except Exception as e:
            print(f"❌ 爬取第{page}页失败：{str(e)[:80]}")
            if retry >= MAX_RETRY_TIMES:
                print(f"⚠️ 第{page}页重试耗尽，返回空列表")
                return []
    return []

def crawl_all_pages(task):
    meta = load_meta(task["meta_file"])
    start_page = meta["last_page"] + 1
    
    # 异常页码重置（小狼帖子阈值60，猫猫帖子可根据实际调整）
    if task["name"] == "小狼" and start_page > 60:
        print(f"⚠️ 检测到异常页码{start_page}，重置为1重新开始")
        start_page = 1
        meta["last_page"] = 0
    
    all_replies = []
    empty_page_count = 0
    current_page = start_page
    global_pid_set = set(meta["pushed_pids"])
    
    print(f"\n🚀 开始遍历页面：从第{start_page}页开始，连续{MAX_EMPTY_PAGES}页无回复则停止")
    
    while empty_page_count < MAX_EMPTY_PAGES and current_page <= MAX_PAGE_LIMIT:
        page_replies = crawl_page(task, current_page)
        
        unique_replies = [r for r in page_replies if r["pid"] not in global_pid_set]
        
        if unique_replies:
            all_replies.extend(unique_replies)
            empty_page_count = 0
            for r in unique_replies:
                global_pid_set.add(r["pid"])
            print(f"✅ 第{current_page}页：新增{len(unique_replies)}条唯一回复")
        else:
            empty_page_count += 1
            print(f"ℹ️ 第{current_page}页：无新回复（连续空页{empty_page_count}/{MAX_EMPTY_PAGES}）")
        
        current_page += 1
    
    last_crawled_page = current_page - empty_page_count - 1
    if last_crawled_page < start_page:
        last_crawled_page = start_page - 1
    
    meta["last_page"] = last_crawled_page
    save_meta(task["meta_file"], meta)
    
    print(f"\n📊 遍历完成：最后有效页码={last_crawled_page}，累计新回复={len(all_replies)}")
    return all_replies, last_crawled_page, meta

# ===================== 4. 新回复推送 =====================
def push_new_reply(task, reply):
    if not BARK_KEY:
        print("⚠️ BARK_KEY未配置，跳过推送")
        return
    
    tid_match = re.search(r'tid=(\d+)', task["url"])
    tid = tid_match.group(1) if tid_match else "unknown"
    
    title = f"🐱 NGA-{tid} | {task['name']} 新回复"
    content = reply["content"]
    if len(content) > 200:
        content = content[:200] + "..."
    
    try:
        bark_api = f"https://api.day.app/{BARK_KEY}/{title}/{content}"
        params = {"isArchive": 1, "group": "NGA监控"}
        response = requests.get(bark_api, params=params, timeout=8)
        if response.status_code == 200 and response.json().get("code") == 200:
            print(f"✅ 推送成功 | PID={reply['pid']} | 页码={reply['page']}")
        else:
            print(f"❌ 推送失败 | 状态码={response.status_code} | 响应={response.text[:50]}")
    except Exception as e:
        print(f"❌ 推送异常：{str(e)[:50]}")

# ===================== 5. 主任务执行 =====================
def run_task(task):
    print("\n" + "="*80)
    print(f"开始执行任务：{task['name']}")
    print(f"任务URL：{task['url']}")
    print(f"元数据文件：{task['meta_file']}")
    print("="*80)
    
    try:
        all_replies, last_crawled_page, meta = crawl_all_pages(task)
        
        new_replies = [r for r in all_replies if r["pid"] not in meta["pushed_pids"]]
        
        if new_replies:
            new_replies.sort(key=lambda x: x["time"], reverse=True)
            print(f"\n🎉 发现{len(new_replies)}条新回复，开始推送...")
            
            is_first_run = len(meta["pushed_pids"]) == 0
            push_replies = new_replies[:FIRST_RUN_PUSH_LIMIT] if is_first_run else new_replies
            
            for idx, reply in enumerate(push_replies):
                print(f"\n--- 推送第{idx+1}条 ---")
                push_new_reply(task, reply)
            
            meta["pushed_pids"].extend([r["pid"] for r in new_replies])
            save_meta(task["meta_file"], meta)
        else:
            print(f"\nℹ️ 未发现新回复，更新最后爬取页码")
            save_meta(task["meta_file"], meta)
        
        print(f"\n✅ 任务执行完成：{task['name']}")
    except Exception as e:
        print(f"❌ 任务执行失败：{str(e)}")

# ===================== 程序入口 =====================
if __name__ == "__main__":
    print(f"\n=== NGA监控脚本启动 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"📌 GitHub Actions环境：{os.getenv('GITHUB_ACTIONS', 'false')}")
    print(f"📌 监控任务数量：{len(MONITOR_TASKS)}")
    
    for task in MONITOR_TASKS:
        run_task(task)
    
    print(f"\n=== NGA监控脚本结束 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"\n📂 元数据文件状态：")
    for task in MONITOR_TASKS:
        if os.path.exists(task["meta_file"]):
            size = os.path.getsize(task["meta_file"])
            print(f"   - {task['meta_file']}：存在（大小：{size} 字节）")
        else:
            print(f"   - {task['meta_file']}：不存在")
