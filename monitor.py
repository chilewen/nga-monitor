import requests
import re
import os
from datetime import datetime

# 配置项（从环境变量读取）
BARK_KEY = os.getenv("BARK_KEY")
NGA_POST_URL = os.getenv("NGA_POST_URL")
TARGET_USER = os.getenv("TARGET_USER")
NGA_COOKIE = os.getenv("NGA_COOKIE")  # 新增：NGA登录Cookie
# 记录已推送的回复ID文件
RECORD_FILE = "pushed_replies.txt"

# 请求头（模拟浏览器，携带登录Cookie）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Cookie": NGA_COOKIE or "",  # 新增：携带Cookie
    "Referer": "https://bbs.nga.cn/",  # 新增：补充Referer，降低反爬概率
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
}

def load_pushed_replies():
    """加载已推送的回复ID"""
    if os.path.exists(RECORD_FILE):
        with open(RECORD_FILE, "r", encoding="utf-8") as f:
            return set(f.read().splitlines())
    return set()

def save_pushed_reply(reply_id):
    """保存已推送的回复ID"""
    with open(RECORD_FILE, "a", encoding="utf-8") as f:
        f.write(f"{reply_id}\n")

def check_login_status(html):
    """检查是否登录成功（简单校验）"""
    # 未登录时页面会包含"请登录后查看"或"登录"按钮
    if "请登录后查看" in html or "登录" in html and "退出" not in html:
        print("⚠️  Cookie 可能失效，未登录成功！")
        return False
    return True

def crawl_nga_post():
    """爬取NGA帖子（带登录态），提取目标用户的新发言"""
    try:
        # 会话保持，模拟真实浏览器的请求状态
        session = requests.Session()
        response = session.get(NGA_POST_URL, headers=HEADERS, timeout=15)
        response.encoding = "utf-8"
        html = response.text

        # 检查登录状态
        if not check_login_status(html):
            return []

        # 优化正则：适配NGA登录后的页面结构
        pattern = re.compile(
            r'<div id="post(\d+)" class="post.*?'  # 先匹配回复ID
            r'<a href="home.php\?mod=space&amp;uid=\d+" class="author">(.*?)</a>.*?'  # 用户名
            r'<div class="postcontent ubbcode">(.*?)</div>',  # 回复内容
            re.DOTALL
        )
        matches = pattern.findall(html)

        new_replies = []
        pushed_ids = load_pushed_replies()

        for reply_id, username, content in matches:
            # 过滤目标用户、未推送的回复
            if username == TARGET_USER and reply_id not in pushed_ids:
                # 清理HTML标签和多余空格，提取纯文本
                content = re.sub(r'<.*?>', '', content).strip()
                content = re.sub(r'\s+', ' ', content)  # 合并多空格为单空格
                if content:
                    new_replies.append({
                        "id": reply_id,
                        "username": username,
                        "content": content,
                        "url": f"{NGA_POST_URL}#post{reply_id}"
                    })
                    # 标记为已推送
                    save_pushed_reply(reply_id)

        return new_replies

    except requests.exceptions.RequestException as e:
        print(f"网络请求失败：{e}")
        return []
    except Exception as e:
        print(f"爬取失败：{e}")
        return []

def send_to_bark(reply):
    """推送消息到Bark App"""
    if not BARK_KEY:
        print("BARK_KEY未配置")
        return

    bark_url = f"https://api.day.app/{BARK_KEY}/"
    title = f"NGA新回复 - {reply['username']}"
    # 限制内容长度，避免Bark推送超限
    content = reply['content'][:300] if len(reply['content']) > 300 else reply['content']
    url = reply['url']

    # 构造Bark推送参数（增加铃声和图标，提升辨识度）
    params = {
        "title": title,
        "body": content,
        "url": url,
        "isArchive": 1,  # 保存到Bark历史
        "sound": "bell.caf",  # 推送铃声
        "icon": "https://img.nga.178.com/ngabbs/favicon.ico"  # NGA图标
    }

    try:
        response = requests.get(bark_url, params=params, timeout=10)
        if response.status_code == 200:
            print(f"✅ 推送成功：回复ID {reply['id']}")
        else:
            print(f"❌ 推送失败：{response.text}")
    except Exception as e:
        print(f"❌ 推送异常：{e}")

if __name__ == "__main__":
    print(f"📌 开始监控 NGA 帖子：{NGA_POST_URL}")
    print(f"👤 监控用户：{TARGET_USER}")
    new_replies = crawl_nga_post()
    
    if new_replies:
        print(f"🎉 发现 {len(new_replies)} 条新回复")
        for reply in new_replies:
            send_to_bark(reply)
    else:
        print("ℹ️  无新回复或爬取异常")
