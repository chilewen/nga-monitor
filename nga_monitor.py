import requests
import re
import os
import json
import sys
import subprocess
from datetime import datetime

# ===================== 核心配置区 =====================
MONITOR_TASKS = [
    {
        "url": "https://bbs.nga.cn/read.php?tid=45502551&authorid=370218",
        "name": "猫猫",
        "meta_file": "nga_monitor/45502551_370218_meta.json"
    },
    {
        "url": "https://bbs.nga.cn/read.php?tid=45974302&authorid=150058",
        "name": "小狼",
        "meta_file": "nga_monitor/45974302_150058_meta.json"
    },
    {
        "url": "https://bbs.nga.cn/read.php?tid=45502551&authorid=26529713",
        "name": "小雨",
        "meta_file": "nga_monitor/45502551_26529713_meta.json",
        "debug_print": False  # 如需调试页面内容，改为True
    },
]

# 环境变量配置（GitHub Actions中通过Secrets设置）
BARK_KEY = os.getenv("BARK_KEY")
NGA_COOKIE = os.getenv("NGA_COOKIE")

# 固定配置
FIRST_RUN_PUSH_LIMIT = 3  # 首次运行推送最后3条
MAX_EMPTY_PAGES = 3       # 连续3页无回复停止遍历
MAX_PAGE_LIMIT = 100      # 最大遍历页码
MAX_RETRY_TIMES = 2       # 每页爬取重试次数
NGA_AUTHOR_OPT = "opt=262144"  # 全局强制添加的参数
# ======================================================

# ===================== Cookie失效提醒 =====================
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

# ===================== 元数据操作 =====================
def git_config():
    try:
        subprocess.run(["git", "config", "--global", "user.name", "GitHub Actions"], check=True, capture_output=True)
        subprocess.run(["git", "config", "--global", "user.email", "actions@github.com"], check=True, capture_output=True)
        print("✅ Git配置完成")
    except Exception as e:
        print(f"⚠️ Git配置警告：{e}")

def load_meta(meta_file_path):
    os.makedirs(os.path.dirname(meta_file_path), exist_ok=True)
    default_meta = {"last_page": 0, "pushed_pids": []}
    
    try:
        if os.path.exists(meta_file_path):
            with open(meta_file_path, "r", encoding="utf-8") as fp:
                meta = json.load(fp)
                if not isinstance(meta, dict) or "last_page" not in meta or "pushed_pids" not in meta:
                    raise ValueError("格式错误")
                meta["last_page"] = int(meta.get("last_page", 0))
                meta["pushed_pids"] = list(meta.get("pushed_pids", []))
            print(f"✅ 读取元数据成功：{meta_file_path}")
            return meta
        else:
            with open(meta_file_path, "w", encoding="utf-8") as fp:
                json.dump(default_meta, fp, ensure_ascii=False, indent=2)
            print(f"ℹ️ 首次创建元数据文件：{meta_file_path}")
            return default_meta
    except Exception as e:
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

# ===================== 页面爬取（核心修复） =====================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Cookie": NGA_COOKIE or "",
    "Referer": "https://bbs.nga.cn/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache"
}

def is_page_valid(html):
    """彻底废掉无效判定，强制所有页面有效"""
    return True

def get_correct_url(task_url, page):
    """全局强制添加opt=262144参数"""
    base_url = re.sub(r'&page=\d+', '', task_url)
    if NGA_AUTHOR_OPT not in base_url:
        base_url += f"&{NGA_AUTHOR_OPT}"
    if page > 1:
        final_url = f"{base_url}&page={page}"
    else:
        final_url = base_url
    return final_url

def crawl_page(task, page):
    """精准匹配HTML结构提取回复，永不误判"""
    crawl_url = get_correct_url(task["url"], page)
    debug_print = task.get("debug_print", False)
    
    for retry in range(MAX_RETRY_TIMES + 1):
        try:
            print(f"\n[调试] 爬取第{page}页（重试{retry}/{MAX_RETRY_TIMES}）：{crawl_url}")
            response = requests.get(crawl_url, headers=HEADERS, timeout=20, allow_redirects=True)
            
            # 调试打印（可选）
            if debug_print:
                print(f"🔍 响应状态码：{response.status_code}")
                print(f"🔍 页面片段：\n{response.text[:1000]}")
                print("="*50 + " 调试内容结束 " + "="*50)
            
            response.encoding = "gbk"
            html = response.text
            
            # Cookie失效检测
            if is_cookie_invalid(html):
                print("❌ 检测到Cookie失效！")
                push_cookie_expired_alert()
                sys.exit(1)
            
            # 强制判定页面有效
            print(f"✅ 第{page}页页面有效（强制判定）")
            
            # ===================== 精准提取回复 =====================
            replies = []
            pid_set = set()
            
            # 匹配所有回复块（精准匹配你的HTML结构）
            post_blocks = re.findall(r'<table class=\'forumbox postbox\'[^>]*>[\s\S]*?</table>', html)
            print(f"🔍 找到{len(post_blocks)}个回复块")
            
            for block in post_blocks:
                # 1. 提取PID（匹配pidXXXAnchor）
                pid_match = re.search(r'pid(\d+)Anchor', block)
                pid = pid_match.group(1) if pid_match else ""
                if not pid or pid in pid_set:
                    continue
                pid_set.add(pid)
                
                # 2. 提取回复时间
                time_match = re.search(r'<span id=\'postdate\d+\' title=\'reply time\'>(\d{4}-\d{2}-\d{2} \d{2}:\d{2})</span>', block)
                reply_time = time_match.group(1) if time_match else "1970-01-01 00:00"
                
                # 3. 提取回复内容
                content_match = re.search(r'<span id=\'postcontent\d+\' class=\'postcontent ubbcode\'>([\s\S]*?)</span>', block)
                content = content_match.group(1) if content_match else ""
                
                # 4. 清理内容（适配你的HTML格式）
                content = re.sub(r'<br\s*/?>', ' ', content)       # 替换换行
                content = re.sub(r'\[img\].*?\[/img\]', '[图片]', content)  # 替换图片标签
                content = re.sub(r'<.*?>', '', content)            # 移除所有HTML标签
                content = re.sub(r'\s+', ' ', content).strip()     # 清理多余空格
                
                # 5. 只保留有内容的回复
                if len(content) > 0:
                    replies.append({
                        "pid": pid,
                        "time": reply_time,
                        "content": content[:300],
                        "page": page
                    })
                    print(f"✅ 提取到回复：PID={pid} | 内容={content[:50]}...")
            
            print(f"✅ 第{page}页最终提取到{len(replies)}条有效回复")
            return replies
        
        except Exception as e:
            print(f"❌ 爬取第{page}页失败：{str(e)[:80]}")
            if retry >= MAX_RETRY_TIMES:
                print(f"⚠️ 第{page}页重试耗尽，返回空列表")
                return []
    return []

def crawl_all_pages(task):
    """遍历页面，记录最后一页回复"""
    meta = load_meta(task["meta_file"])
    start_page = meta["last_page"] + 1
    
    # 异常页码重置
    if start_page > 100:
        print(f"⚠️ 检测到异常页码{start_page}，重置为1重新开始")
        start_page = 1
        meta["last_page"] = 0
    
    all_replies = []
    empty_page_count = 0
    current_page = start_page
    global_pid_set = set(meta["pushed_pids"])
    last_page_replies = []  # 记录最后一页的回复
    
    print(f"\n🚀 开始遍历页面：从第{start_page}页开始，连续{MAX_EMPTY_PAGES}页无回复则停止")
    print(f"🎯 全局强制带opt=262144参数")
    
    while empty_page_count < MAX_EMPTY_PAGES and current_page <= MAX_PAGE_LIMIT:
        page_replies = crawl_page(task, current_page)
        
        # 更新最后一页回复（有内容才更新）
        if page_replies:
            last_page_replies = page_replies
        
        # 筛选未推送的回复
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
    return all_replies, last_crawled_page, meta, last_page_replies

# ===================== 回复推送 =====================
def push_new_reply(task, reply):
    if not BARK_KEY:
        print("⚠️ BARK_KEY未配置，跳过推送")
        return
    
    # 提取帖子ID
    tid_match = re.search(r'tid=(\d+)', task["url"])
    tid = tid_match.group(1) if tid_match else "unknown"
    
    # 构造推送内容
    title = f"🐱 NGA-{tid} | {task['name']} 新回复"
    content = reply["content"]
    if len(content) > 200:
        content = content[:200] + "..."
    
    try:
        # 调用Bark接口推送
        bark_api = f"https://api.day.app/{BARK_KEY}/{title}/{content}"
        params = {
            "isArchive": 1,
            "group": "NGA监控",
            "copy": content,
            "autoCopy": 0
        }
        response = requests.get(bark_api, params=params, timeout=8)
        
        if response.status_code == 200 and response.json().get("code") == 200:
            print(f"✅ 推送成功 | PID={reply['pid']} | 页码={reply['page']}")
        else:
            print(f"❌ 推送失败 | 状态码={response.status_code} | 响应={response.text[:50]}")
    except Exception as e:
        print(f"❌ 推送异常：{str(e)[:50]}")

def run_task(task):
    """执行单个任务，控制首次/非首次推送逻辑"""
    print("\n" + "="*80)
    print(f"开始执行任务：{task['name']}")
    print(f"任务URL：{task['url']}")
    print(f"元数据文件：{task['meta_file']}")
    print("="*80)
    
    try:
        all_replies, last_crawled_page, meta, last_page_replies = crawl_all_pages(task)
        
        # 筛选未推送的新回复
        new_replies = [r for r in all_replies if r["pid"] not in meta["pushed_pids"]]
        
        if new_replies:
            # 判断是否首次运行（已推送PID为空）
            is_first_run = len(meta["pushed_pids"]) == 0
            
            if is_first_run:
                print(f"\n🎉 首次运行：只推送最后一页的最后{FIRST_RUN_PUSH_LIMIT}条回复")
                # 首次运行：最后一页回复按时间排序，取最后3条
                last_page_sorted = sorted(last_page_replies, key=lambda x: x["time"])
                push_replies = last_page_sorted[-FIRST_RUN_PUSH_LIMIT:]
            else:
                print(f"\n🎉 非首次运行：推送所有{len(new_replies)}条新回复")
                # 非首次运行：所有新回复按时间倒序推送
                push_replies = sorted(new_replies, key=lambda x: x["time"], reverse=True)
            
            # 执行推送
            print(f"\n📤 开始推送{len(push_replies)}条回复...")
            for idx, reply in enumerate(push_replies):
                print(f"\n--- 推送第{idx+1}条 ---")
                push_new_reply(task, reply)
            
            # 记录已推送的PID（避免重复推送）
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
    print("="*80)
    
    # 遍历执行所有任务
    for task in MONITOR_TASKS:
        run_task(task)
    
    print("\n" + "="*80)
    print(f"=== NGA监控脚本结束 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    
    # 打印元数据文件状态
    print(f"\n📂 元数据文件状态：")
    for task in MONITOR_TASKS:
        meta_path = task["meta_file"]
        if os.path.exists(meta_path):
            size = os.path.getsize(meta_path)
            print(f"   - {meta_path}：存在（大小：{size} 字节）")
        else:
            print(f"   - {meta_path}：不存在")
