import requests
import re
import os
from datetime import datetime

# 配置项（从环境变量读取）
# TARGET_USER 现在存储的是用户ID（uid），如 "123456"
BARK_KEY = os.getenv("BARK_KEY")
NGA_POST_URL = os.getenv("NGA_POST_URL")
TARGET_USER_UID = os.getenv("TARGET_USER")  # 重命名变量更清晰（值是用户ID）
NGA_COOKIE = os.getenv("NGA_COOKIE")
# 记录已推送的回复ID文件
RECORD_FILE = "pushed_replies.txt"

# 请求头（模拟浏览器，携带登录Cookie）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Cookie": NGA_COOKIE or "",
    "Referer": "https://bbs.nga.cn/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
}

def load_pushed_replies():
    """加载已推送的回复ID"""
    if os.path.exists(RECORD_FILE):
        with open(RECORD_FILE, "r", encoding="utf-8") as f:
            pushed_ids = set(f.read().splitlines())
            print(f"✅ 加载到已推送的回复ID数量：{len(pushed_ids)}")
            return pushed_ids
    print("⚠️  首次运行，无已推送记录，将初始化历史ID")
    return set()

def save_pushed_reply(reply_ids):
    """批量保存回复ID（适配首次初始化）"""
    # 去重后写入
    existing_ids = load_pushed_replies()
    new_ids = [rid for rid in reply_ids if rid not in existing_ids]
    if new_ids:
        with open(RECORD_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(new_ids) + "\n")
        print(f"✅ 已记录 {len(new_ids)} 个回复ID到文件")

def check_login_status(html):
    """检查是否登录成功"""
    if "请登录后查看" in html or ("登录" in html and "退出" not in html):
        print("❌ Cookie 可能失效，未登录成功！")
        print(f"📝 页面关键内容片段：{html[:500]}")  # 输出页面开头，方便调试
        return False
    return True

def crawl_nga_post():
    """爬取NGA帖子，返回目标用户ID的所有发言+首次运行标记"""
    all_target_replies = []  # 目标用户ID的所有发言
    is_first_run = not os.path.exists(RECORD_FILE)  # 是否首次运行

    try:
        session = requests.Session()
        response = session.get(NGA_POST_URL, headers=HEADERS, timeout=15)
        response.encoding = "utf-8"
        html = response.text

        # 打印页面长度，确认是否爬取到内容
        print(f"📥 爬取到页面内容长度：{len(html)} 字符")

        # 检查登录状态
        if not check_login_status(html):
            return [], is_first_run

        # 核心修改：正则匹配 回复ID + 用户ID(uid) + 用户名 + 回复内容
        # 正则说明：
        # 1. id="post(\d+)" 匹配回复ID
        # 2. home.php\?mod=space&amp;uid=(\d+) 匹配用户ID(uid)
        # 3. class="author">([^<]+)</a> 匹配用户名（仅用于推送显示）
        # 4. postcontent ubbcode">([\s\S]*?)</div> 匹配回复内容
        pattern = re.compile(
            r'id="post(\d+)"[^>]*?>.*?'
            r'<a[^>]+href="home\.php\?mod=space&amp;uid=(\d+)"[^>]*?>.*?'
            r'<span class="author">([^<]+)</span>.*?'  # 或 class="author">([^<]+)</a>
            r'<div class="postcontent ubbcode">([\s\S]*?)</div>',
            re.DOTALL | re.IGNORECASE
        )
        matches = pattern.findall(html)
        print(f"🔍 正则匹配到的总回复数：{len(matches)}")

        # 筛选目标用户ID的发言（核心修改：匹配uid而非用户名）
        for reply_id, user_uid, username, content in matches:
            user_uid = user_uid.strip()  # 用户ID（数字字符串）
            username = username.strip()  # 用户名（仅用于推送标题显示）
            # 匹配目标用户ID（你的TARGET_USER变量值）
            if user_uid == TARGET_USER_UID:
                # 清理内容：移除HTML标签、多余空格
                content = re.sub(r'<.*?>', '', content).strip()
                content = re.sub(r'\s+', ' ', content)
                # 过滤空内容
                if content:
                    reply_info = {
                        "id": reply_id,
                        "uid": user_uid,
                        "username": username,  # 保留用户名用于推送显示
                        "content": content,
                        "url": f"{NGA_POST_URL}#post{reply_id}"
                    }
                    all_target_replies.append(reply_info)

        print(f"👤 匹配到目标用户ID {TARGET_USER_UID} 的发言数：{len(all_target_replies)}")
        return all_target_replies, is_first_run

    except requests.exceptions.RequestException as e:
        print(f"❌ 网络请求失败：{type(e).__name__} - {e}")
        return [], is_first_run
    except Exception as e:
        print(f"❌ 爬取异常：{type(e).__name__} - {e}")
        import traceback
        print(f"📝 异常堆栈：{traceback.format_exc()[:1000]}")  # 输出堆栈，方便调试
        return [], is_first_run

def process_replies(all_replies, is_first_run):
    """处理发言：首次运行记录所有ID，非首次推送新回复"""
    pushed_ids = load_pushed_replies()
    new_replies = []

    if is_first_run:
        # 首次运行：记录所有历史ID，仅推送最新1条（可选）
        if all_replies:
            # 按回复ID排序（数字越大越新）
            sorted_replies = sorted(all_replies, key=lambda x: int(x['id']), reverse=True)
            # 记录所有ID
            all_ids = [r['id'] for r in sorted_replies]
            save_pushed_reply(all_ids)
            # 仅推送最新1条（避免首次推送大量历史消息）
            new_replies = [sorted_replies[0]]
            print(f"🚀 首次运行：记录 {len(all_ids)} 个历史ID，推送最新1条")
    else:
        # 非首次运行：推送未记录的新回复
        for reply in all_replies:
            if reply['id'] not in pushed_ids:
                new_replies.append(reply)
                save_pushed_reply([reply['id']])  # 实时记录

    return new_replies

def send_to_bark(reply):
    """推送消息到Bark App（显示用户名+用户ID）"""
    if not BARK_KEY:
        print("❌ BARK_KEY未配置")
        return

    bark_url = f"https://api.day.app/{BARK_KEY}/"
    # 推送标题：显示用户名+用户ID，更清晰
    title = f"{'【首次初始化】' if not os.path.exists(RECORD_FILE) else '【新回复】'}NGA - {reply['username']}(UID:{reply['uid']})"
    content = reply['content'][:300]
    url = reply['url']

    params = {
        "title": title,
        "body": content,
        "url": url,
        "isArchive": 1,
        "sound": "bell.caf",
        "icon": "https://img.nga.178.com/ngabbs/favicon.ico"
    }

    try:
        response = requests.get(bark_url, params=params, timeout=10)
        if response.status_code == 200:
            print(f"✅ 推送成功：回复ID {reply['id']}")
        else:
            print(f"❌ 推送失败：{response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ 推送异常：{type(e).__name__} - {e}")

if __name__ == "__main__":
    print(f"\n=== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 开始监控 ===")
    print(f"📌 监控帖子：{NGA_POST_URL}")
    print(f"👤 监控用户ID：{TARGET_USER_UID}")  # 日志显示用户ID

    # 爬取所有目标发言 + 判断是否首次运行
    all_target_replies, is_first_run = crawl_nga_post()

    # 处理发言（初始化/推送新回复）
    new_replies = process_replies(all_target_replies, is_first_run)

    # 推送新回复
    if new_replies:
        print(f"🎉 待推送新回复数：{len(new_replies)}")
        for reply in new_replies:
            send_to_bark(reply)
    else:
        if not all_target_replies:
            print("ℹ️  未匹配到目标用户ID的发言（或爬取异常）")
        else:
            print("ℹ️  无新回复（所有发言已记录）")

    print(f"=== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 监控结束 ===\n")
