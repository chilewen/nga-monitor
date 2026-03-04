import requests
import re
import os
from datetime import datetime

# ===================== 配置 =====================
BARK_KEY = os.getenv("BARK_KEY")
NGA_POST_URL = os.getenv("NGA_POST_URL")
TARGET_UID = os.getenv("TARGET_USER")
NGA_COOKIE = os.getenv("NGA_COOKIE")
RECORD_FILE = "pushed_replies.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win10; x64) AppleWebKit/537.36",
    "Cookie": NGA_COOKIE or "",
    "Referer": "https://bbs.nga.cn/",
    "Accept-Language": "zh-CN,zh;q=0.9"
}

# ===================== 工具函数 =====================
def load_pushed_replies():
    if os.path.exists(RECORD_FILE):
        with open(RECORD_FILE, "r", encoding="utf-8") as f:
            return set(f.read().splitlines())
    return set()

def save_pushed_ids(ids):
    existing = load_pushed_replies()
    new_ids = [i for i in ids if i not in existing]
    if new_ids:
        with open(RECORD_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(new_ids) + "\n")

def check_login(html):
    if "请登录后查看" in html or ("登录" in html and "退出" not in html):
        print("❌ Cookie 已失效")
        return False
    return True

# ===================== 核心：爬单页 =====================
def crawl_one_page(url):
    replies = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.encoding = "utf-8"
        html = r.text
        if not check_login(html):
            return []

        pattern = re.compile(
            r'<tr id="post1strow(\d+)" class="postrow.*?'
            r'userClick\(event,&quot;(\d+)&quot;\)">.*?'
            r'<b class="block_txt".*?>([^<]+)</b>([^<]+)</a>.*?'
            r'<span id="postcontent\d+" class="postcontent ubbcode">([\s\S]*?)</span>',
            re.DOTALL | re.IGNORECASE
        )

        for m in pattern.findall(html):
            post_row_id = m[0].strip()
            uid = m[1].strip()
            name1 = m[2].strip()
            name2 = m[3].strip()
            content_raw = m[4].strip()

            username = (name1 + name2).strip()
            content = re.sub(r'<.*?>', '', content_raw).strip()
            content = re.sub(r'\s+', ' ', content)

            # 从页面提取真实 pid（用于跳转）
            pid_match = re.search(r'pid(\d+)Anchor', html)
            reply_url = f"{NGA_POST_URL.split('#')[0]}#pid{pid_match.group(1)}Anchor" if pid_match else url

            replies.append({
                "pid": pid_match.group(1) if pid_match else post_row_id,
                "uid": uid,
                "username": username,
                "content": content,
                "url": reply_url
            })
    except Exception as e:
        print(f"⚠️ 爬取失败: {e}")
    return replies

# ===================== 自动翻页爬全帖 =====================
def crawl_all_pages():
    base_url = NGA_POST_URL.split("&page=")[0]
    all_replies = []
    page = 1

    print(f"\n🔁 开始自动翻页: {base_url}")

    while True:
        page_url = f"{base_url}&page={page}"
        print(f"📄 正在爬第 {page} 页: {page_url}")

        current = crawl_one_page(page_url)
        if not current:
            print(f"✅ 第 {page} 页无内容，结束翻页")
            break

        all_replies.extend(current)

        # 防止死循环，最多20页（可自己改）
        if page >= 20:
            break
        page += 1

    # 只保留目标用户
    target = [r for r in all_replies if r["uid"] == TARGET_UID]
    print(f"\n🎯 全帖共找到目标用户发言: {len(target)} 条")
    return target

# ===================== 推送 =====================
def send_bark(reply):
    if not BARK_KEY:
        return
    title = f"【NGA新回复】{reply['username']}({reply['uid']})"
    body = reply["content"][:300]
    url = reply["url"]
    api = f"https://api.day.app/{BARK_KEY}"
    try:
        requests.get(api, params={
            "title": title,
            "body": body,
            "url": url,
            "isArchive": 1
        }, timeout=8)
    except:
        pass

# ===================== 主逻辑 =====================
if __name__ == "__main__":
    print(f"=== 开始运行 {datetime.now()} ===")
    print(f"帖子: {NGA_POST_URL}")
    print(f"监控UID: {TARGET_UID}")

    all_target = crawl_all_pages()
    pushed = load_pushed_replies()

    # 首次运行：全记录，不推送
    if not pushed:
        pids = [r["pid"] for r in all_target]
        save_pushed_ids(pids)
        print(f"✅ 首次运行：已记录全部 {len(pids)} 条历史，下次只推新回复")

    # 非首次：推送新的
    else:
        new_replies = [r for r in all_target if r["pid"] not in pushed]
        if new_replies:
            print(f"🎉 发现 {len(new_replies)} 条新回复")
            for rp in new_replies:
                send_bark(rp)
            save_pushed_ids([r["pid"] for r in new_replies])
        else:
            print("ℹ️ 无新回复")

    print("=== 运行结束 ===")
