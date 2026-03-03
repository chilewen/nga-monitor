import requests
import json
import os
import re
from datetime import datetime

# ================= 配置区域 (请修改这里) =================
# 1. 你的 Bark 链接 (例如: https://api.day.app/xxxxxxxx)
BARK_KEY = "https://api.day.app/H4TvmYKzupRxHAgrpTD65N" 

# 2. 目标 NGA 用户的 UID (你要监控的用户)
TARGET_UID = "26529713" 

# 3. 记录文件名称
RECORD_FILE = "last_reply_record.txt"
# =======================================================

def get_latest_reply():
    """
    获取指定用户最新的回复记录
    接口：__act=reply
    """
    # 注意：NGA 移动端回复列表接口
    url = f"https://m.nga.cn/nuke.php?__lib=ucp&__act=reply&uid={TARGET_UID}&page=1"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.0",
        "Referer": "https://m.nga.cn/"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"请求失败，状态码: {response.status_code}")
            return None, None, None

        content = response.text
        
        # --- 关键解析逻辑 ---
        # 回复列表的结构通常是：
        # <a href="read.php?tid=XXXXX...">帖子标题</a> ... <span class="date">回复时间</span>
        # 或者包含 blockid (pid)
        
        # 策略：提取第一个回复块的 tid 和 帖子标题
        # 匹配 read.php?tid=数字 的部分
        tid_match = re.search(r'read\.php\?tid=(\d+)', content)
        
        # 匹配帖子标题 (通常在 tid 链接的文本内容里，或者附近的 > 标签内)
        # 这种正则比较脆弱，因为移动端 HTML 结构可能微调
        # 尝试匹配：<a ... >帖子标题</a>
        title_match = re.search(r'read\.php\?tid=\d+[^>]*>(.*?)</a>', content)
        
        # 尝试匹配回复时间 (用于辅助判断，防止同贴多次回复误判，可选)
        time_match = re.search(r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}', content)

        if tid_match and title_match:
            tid = tid_match.group(1)
            title = title_match.group(1).strip()
            # 清理标题中的 HTML
            title = re.sub(r'<[^>]+>', '', title)
            
            # 截取标题前20个字，防止太长
            if len(title) > 20:
                title = title[:20] + "..."
                
            reply_time = time_match.group(0) if time_match else "未知时间"
            
            return tid, title, reply_time
        else:
            print("未解析到回复记录，可能用户无回复或页面结构变更")
            # 打印一部分内容用于调试 (在 GitHub Logs 中查看)
            # print(content[:500]) 
            return None, None, None
            
    except Exception as e:
        print(f"发生错误: {e}")
        return None, None, None

def send_bark_notification(title, content, jump_url):
    """发送推送 Bark"""
    url = f"{BARK_KEY}/{title}"
    params = {
        "body": content,
        "url": jump_url,      # 点击通知直接跳转到该帖子
        "sound": "minuet", 
        "isArchive": "1",
        "group": "NGA回复监控"
    }
    try:
        resp = requests.get(url, params=params, timeout=5)
        print(f"推送结果: {resp.text}")
    except Exception as e:
        print(f"推送失败: {e}")

def main():
    # 1. 获取上次记录的标识 (格式: "tid|时间")
    last_record = ""
    if os.path.exists(RECORD_FILE):
        with open(RECORD_FILE, "r", encoding="utf-8") as f:
            last_record = f.read().strip()
    
    # 2. 获取最新回复
    current_tid, current_title, current_time = get_latest_reply()
    
    if not current_tid:
        print("未能获取最新回复信息")
        return

    # 构造当前记录标识：使用 tid + 时间 作为唯一键，防止同帖多次回复只提醒一次的问题
    # 如果只想监测“在哪个帖子里回复了”，只用 tid 即可。
    # 如果想监测“每一次回复”，建议用 tid + 时间
    current_record = f"{current_tid}|{current_time}"
    
    print(f"当前最新回复: TID={current_tid}, 标题={current_title}, 时间={current_time}")
    print(f"上次记录: {last_record}")

    # 3. 比对并发送
    if current_record != last_record:
        jump_url = f"https://bbs.nga.cn/read.php?tid={current_tid}"
        msg = f"NGA 用户 (UID:{TARGET_UID}) 有了新回复!\n在帖子: {current_title}\n时间: {current_time}"
        
        send_bark_notification("💬 NGA 新回复提醒", msg, jump_url)
        
        # 更新记录文件
        with open(RECORD_FILE, "w", encoding="utf-8") as f:
            f.write(current_record)
        print("记录已更新")
    else:
        print("没有新回复")

if __name__ == "__main__":
    main()
