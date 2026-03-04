import requests
import re
import os
from datetime import datetime

# ===================== 【配置区：你只需要改这里】 =====================
# 格式："https://bbs.nga.cn/read.php?tid=XXX&authorid=XXX" : "备注名"
MONITOR_TASKS = [
    {
        "url": "https://bbs.nga.cn/read.php?tid=45502551&authorid=370218",
        "name": "猫猫"
    },
    {
        "url": "https://bbs.nga.cn/read.php?tid=45502551&authorid=26529713",
        "name": "小雨"
    }
    # 想加多少就加多少，每个独立记录页数
    # {
    #     "url": "https://bbs.nga.cn/read.php?tid=123456&authorid=789012",
    #     "name": "另一个楼主"
    # },
]

BARK_KEY = os.getenv("BARK_KEY")
NGA_COOKIE = os.getenv("NGA_COOKIE")

FIRST_RUN_PUSH_LIMIT = 3  # 首次只推最新3条
# =====================================================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Cookie": NGA_COOKIE,
    "Referer": "https://bbs.nga.cn/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

os.makedirs("nga_monitor", exist_ok=True)

# ===================== 工具：每个任务独立文件 =====================
def get_task_key(task):
    url = task["url"]
    tid = re.search(r"tid=(\d+)", url).group(1)
    aid = re.search(r"authorid=(\d+)", url).group(1)
    return f"{tid}_{aid}"

def last_page_file(task):
    return f"nga_monitor/last_page_{get_task_key(task)}.txt"

def pushed_file(task):
    return f"nga_monitor/pushed_{get_task_key(task)}.txt"

def load_last_page(task):
    f = last_page_file(task)
    if os.path.exists(f):
        with open(f, encoding="utf-8") as fp:
            return int(fp.read().strip())
    return None  # 首次运行

def save_last_page(task, page):
    with open(last_page_file(task), "w", encoding="utf-8") as fp:
        fp.write(str(page))

def load_pushed_pids(task):
    f = pushed_file(task)
    if not os.path.exists(f):
        return set()
    with open(f, encoding="utf-8") as fp:
        return set(line.strip() for line in fp if line.strip())

def append_pushed_pids(task, pids):
    with open(pushed_file(task), "a", encoding="utf-8") as fp:
        for pid in pids:
            fp.write(pid + "\n")

# ===================== 核心：获取总页数 =====================
def get_total_page(task):
    url = task["url"]
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.encoding = "gbk"
    html = r.text
    match = re.search(r"共 (\d+) 页", html)
    return int(match.group(1)) if match else 1

# ===================== 爬单页：提取所有回复 =====================
def crawl_page(task, page):
    base = task["url"].split("&page=")[0]
    url = f"{base}&page={page}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.encoding = "gbk"
    html = r.text

    posts = re.findall(
        r'<table class=\'forumbox postbox\'[^>]*>[\s\S]*?</table>',
        html, re.I
    )
    replies = []
    for p in posts:
        pid_match = re.search(r'pid(\d+)Anchor', p)
        time_match = re.search(r'title=\'reply time\'>(\d{4}-\d{2}-\d{2} \d{2}:\d{2})', p)
        cont_match = re.search(r'class=\'postcontent ubbcode\'>([\s\S]*?)</span>', p)

        if not (pid_match and cont_match):
            continue

        pid = pid_match.group(1)
        rt = time_match.group(1) if time_match else "0000-00-00"
        cont = cont_match.group(1)
        cont = re.sub(r"<.*?>", "", cont)
        cont = re.sub(r"\[img\].*?\[/img\]", "[图片]", cont)
        cont = re.sub(r"\s+", " ", cont).strip()

        replies.append({
            "pid": pid,
            "time": rt,
            "content": cont,
            "page": page
        })
    return replies

# ===================== 推送 =====================
def push(task, reply):
    if not BARK_KEY:
        print("跳过推送：BARK_KEY 未设置")
        return
    title = f"【NGA】{task['name']} 新回复"
    content = reply["content"][:300]
    api = f"https://api.day.app/{BARK_KEY}/{title}/{content}"
    try:
        requests.get(api, timeout=5)
        print(f"✅ 推送成功 PID:{reply['pid']}")
    except:
        print(f"❌ 推送失败")

# ===================== 单任务执行 =====================
def run_task(task):
    print("-" * 60)
    print(f"任务：{task['name']}")
    last_page = load_last_page(task)
    total_page = get_total_page(task)
    print(f"总页数：{total_page} | 上次记录：{last_page}")

    # 1. 首次运行：只爬最后一页，取最新3条
    if last_page is None:
        print("🆕 首次运行，只爬最后一页")
        replies = crawl_page(task, total_page)
        replies.sort(key=lambda x: x["pid"], reverse=True)
        new_replies = replies
        push_list = new_replies[:FIRST_RUN_PUSH_LIMIT]
        save_last_page(task, total_page)

    # 2. 非首次：从上次页+1 到最新页
    else:
        start = last_page + 1
        end = total_page
        if start > end:
            print("ℹ️ 无新页")
            return
        print(f"📖 增量爬取：{start} → {end}")
        all_replies = []
        for p in range(start, end + 1):
            all_replies += crawl_page(task, p)
        all_replies.sort(key=lambda x: x["pid"], reverse=True)
        pushed = load_pushed_pids(task)
        new_replies = [r for r in all_replies if r["pid"] not in pushed]
        push_list = new_replies
        save_last_page(task, end)

    # 推送
    if push_list:
        print(f"🎯 本次推送 {len(push_list)} 条")
        for r in push_list:
            push(task, r)
        append_pushed_pids(task, [r["pid"] for r in push_list])
    else:
        print("ℹ️ 无新回复")

# ===================== 主入口 =====================
if __name__ == "__main__":
    print(f"=== NGA 多用户监控 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    for t in MONITOR_TASKS:
        run_task(t)
    print("=== 全部任务完成 ===")
