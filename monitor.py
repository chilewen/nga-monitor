import requests
import json
import os
import re
from datetime import datetime

# ================= 配置区域 (请修改这里) =================
# 1. 你的 Bark 链接 (去掉末尾的斜杠，例如: https://api.day.app/xxxxxxxx)
BARK_KEY = "https://api.day.app/H4TvmYKzupRxHAgrpTD65N" 

# 2. 目标 NGA 用户的 UID (在用户主页 URL 中可以看到，如 uid=123456)
TARGET_UID = "26529713" 

# 3. 记录文件的名称 (用于在 GitHub  Actions 中保存上次检查的帖子 ID)
RECORD_FILE = "last_post_id.txt"
# =======================================================

def get_latest_post():
    """
    获取指定用户最新的帖子标题和 ID
    注意：NGA 移动端接口经常变动，这里使用模拟移动端网页的方式
    """
    url = f"https://m.nga.cn/nuke.php?__lib=ucp&__act=topic&uid={TARGET_UID}&page=1"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.0",
        "Referer": "https://m.nga.cn/"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"请求失败，状态码: {response.status_code}")
            return None, None

        content = response.text
        
        # 正则解析：寻找帖子列表中的第一个帖子 (tid 和 标题)
        # 注意：正则表达式需要根据 NGA 当前网页结构微调，以下为通用匹配逻辑
        # 匹配类似 <a href="read.php?tid=xxxxx">标题</a> 的结构
        match = re.search(r'read\.php\?tid=(\d+)".*?>(.*?)</a>', content)
        
        if match:
            tid = match.group(1)
            title = match.group(2).strip()
            # 清理标题中的 HTML 标签
            title = re.sub(r'<[^>]+>', '', title)
            return tid, title
        else:
            print("未解析到帖子，可能页面结构变更或无发帖")
            return None, None
            
    except Exception as e:
        print(f"发生错误: {e}")
        return None, None

def send_bark_notification(title, content):
    """发送推送 Bark"""
    # Bark 支持参数：level (紧急程度), sound (铃声), group (分组)
    url = f"{BARK_KEY}/{title}"
    params = {
        "body": content,
        "sound": "minuet", # 铃声
        "isArchive": "1",  # 是否归档
        "group": "NGA监控" # 通知分组
    }
    try:
        resp = requests.get(url, params=params, timeout=5)
        print(f"推送结果: {resp.text}")
    except Exception as e:
        print(f"推送失败: {e}")

def main():
    # 1. 获取上次记录的 TID (从 GitHub Secrets 或 本地文件模拟)
    # 在 GitHub Actions 环境中，我们利用 Artifact 或 简单的文件读写来记录状态
    last_tid = ""
    if os.path.exists(RECORD_FILE):
        with open(RECORD_FILE, "r", encoding="utf-8") as f:
            last_tid = f.read().strip()
    
    # 2. 获取最新帖子
    current_tid, current_title = get_latest_post()
    
    if not current_tid:
        print("未能获取最新帖子信息")
        return

    print(f"当前最新帖 ID: {current_tid}, 标题: {current_title}")
    print(f"上次记录 ID: {last_tid}")

    # 3. 比对并发送
    if current_tid != last_tid:
        msg = f"NGA 用户 (UID:{TARGET_UID}) 发了新帖:\n{current_title}\n点击查看详情: https://bbs.nga.cn/read.php?tid={current_tid}"
        send_bark_notification("🔔 NGA 新帖提醒", msg)
        
        # 更新记录文件
        with open(RECORD_FILE, "w", encoding="utf-8") as f:
            f.write(current_tid)
        print("记录已更新")
    else:
        print("没有新帖")

if __name__ == "__main__":
    main()
