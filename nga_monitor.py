import requests
import re
import os
from datetime import datetime

# ===================== 配置项（从环境变量读取） =====================
BARK_KEY = os.getenv("BARK_KEY")
NGA_POST_URL = os.getenv("NGA_POST_URL")
TARGET_UID = os.getenv("TARGET_USER")  # 目标用户UID（纯数字）
NGA_COOKIE = os.getenv("NGA_COOKIE")
# 记录已推送的回复PID文件
RECORD_FILE = "pushed_replies.txt"
# 新增配置：强制爬取页数（解决帖子页数多的问题）
FORCE_CRAWL_PAGES = int(os.getenv("FORCE_CRAWL_PAGES", 10))  # 默认爬10页，可通过环境变量调整

# 请求头（模拟浏览器，携带登录态）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Cookie": NGA_COOKIE or "",
    "Referer": "https://bbs.nga.cn/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
}

# ===================== 工具函数 =====================
def load_pushed_replies():
    """加载已推送的回复PID，避免重复推送"""
    if os.path.exists(RECORD_FILE):
        with open(RECORD_FILE, "r", encoding="utf-8") as f:
            pushed_pids = set(f.read().splitlines())
            print(f"✅ 加载到已推送的回复PID数量：{len(pushed_pids)}")
            return pushed_pids
    print("⚠️  首次运行，无已推送记录，将初始化历史ID")
    return set()

def save_pushed_ids(new_pids):
    """批量保存新的回复PID"""
    if not new_pids:
        return
    existing_pids = load_pushed_replies()
    # 去重：只保存未记录的PID
    to_save = [pid for pid in new_pids if pid not in existing_pids]
    if to_save:
        with open(RECORD_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(to_save) + "\n")
        print(f"✅ 已记录 {len(to_save)} 个新回复PID到文件")

def check_login_status(html):
    """检查是否登录成功（核心：避免Cookie失效）"""
    if not html:
        print("❌ 爬取到空页面！")
        return False
    # NGA未登录的特征关键词
    unlogin_keywords = ["请登录后查看", "登录", "未登录", "游客"]
    login_keywords = ["退出", "我的帖子", "个人中心"]
    
    has_unlogin = any(keyword in html for keyword in unlogin_keywords)
    has_login = any(keyword in html for keyword in login_keywords)
    
    if has_unlogin and not has_login:
        print("❌ Cookie失效或未登录！")
        print(f"📝 页面片段（前500字符）：{html[:500]}")
        return False
    return True

# ===================== 核心：爬取单页（增强版） =====================
def crawl_one_page(url):
    """
    爬取单页内容，增强版提取逻辑
    - 扩大内容搜索范围
    - 增加多种PID/内容/用户名提取规则
    - 输出更多调试信息
    """
    target_replies = []
    try:
        # 发起请求（带超时和重试）
        session = requests.Session()
        response = session.get(
            url, 
            headers=HEADERS, 
            timeout=20,
            allow_redirects=True
        )
        # 自动识别编码（优先GBK，兼容NGA）
        response.encoding = response.apparent_encoding if response.apparent_encoding else "GBK"
        html = response.text
        page_num = url.split("page=")[-1] if "page=" in url else "1"
        
        # ========== 调试日志：增强版 ==========
        print(f"\n===== 第 {page_num} 页调试信息 =====")
        print(f"📥 页面URL：{url}")
        print(f"📏 页面内容长度：{len(html)} 字符")
        print(f"🔤 页面编码：{response.encoding}")
        print(f"🔓 登录状态：{'已登录' if check_login_status(html) else '未登录'}")
        
        # 输出页面中间部分（找回复内容，不再只看前1000字符）
        mid_start = max(0, len(html)//2 - 500)
        mid_end = min(len(html), len(html)//2 + 500)
        print(f"📝 页面中间1000字符（找回复内容）：\n{html[mid_start:mid_end]}")
        
        # 搜索页面中所有UID，确认是否有目标UID
        all_uids = re.findall(r'userClick\(event,[""](\d+)[""]\)', html)
        all_uids += re.findall(r'userClick\(event,&quot;(\d+)&quot;\)', html)
        unique_uids = list(set(all_uids))
        print(f"🔍 页面中所有匹配到的UID（去重后前20个）：{unique_uids[:20]}")
        print(f"🎯 目标UID {TARGET_UID} 是否存在：{'是' if TARGET_UID in unique_uids else '否'}")
        print("===== 调试信息结束 =====\n")
        
        # 登录校验失败，直接返回
        if not check_login_status(html):
            return target_replies
        
        # ========== 增强版提取逻辑 ==========
        # 1. 提取所有用户回复块（先定位回复区域）
        # 匹配NGA的回复块结构
        post_blocks = re.findall(r'<div class="postrow"[^>]*>[\s\S]*?<div class="postsep">', html, re.IGNORECASE)
        print(f"📦 第 {page_num} 页找到回复块数量：{len(post_blocks)}")
        
        if not post_blocks:
            # 兜底：匹配其他回复块结构
            post_blocks = re.findall(r'<table class="postbox"[^>]*>[\s\S]*?</table>', html, re.IGNORECASE)
            print(f"📦 兜底匹配回复块数量：{len(post_blocks)}")
        
        # 2. 遍历每个回复块提取信息
        for block_idx, block in enumerate(post_blocks):
            # 提取UID（多种规则）
            uid_matches = re.findall(r'userClick\(event,[""](\d+)[""]\)', block)
            if not uid_matches:
                uid_matches = re.findall(r'userClick\(event,&quot;(\d+)&quot;\)', block)
            if not uid_matches:
                continue  # 无UID的回复块跳过
            
            current_uid = uid_matches[0].strip()
            if current_uid != TARGET_UID:
                continue  # 只处理目标UID
            
            # 提取用户名（增强版规则）
            username = "未知用户"
            name_patterns = [
                re.compile(r'<a href="nuke.php\?func=ucp&amp;uid=\d+"[^>]*>([^<]+)</a>', re.IGNORECASE),
                re.compile(r'<b class="block_txt"[^>]*>([^<]+)</b>', re.IGNORECASE),
                re.compile(r'class="author"><a[^>]*>([^<]+)</a>', re.IGNORECASE),
                re.compile(r'username=[""]([^""]+)[""]', re.IGNORECASE)
            ]
            for np in name_patterns:
                nm = np.search(block)
                if nm:
                    username = nm.group(1).strip()
                    break
            
            # 提取回复内容（增强版规则，清理更多无用标签）
            content = ""
            content_patterns = [
                re.compile(r'<div class="postcontent ubbcode"[^>]*>([\s\S]*?)</div>', re.IGNORECASE),
                re.compile(r'<span class="postcontent ubbcode"[^>]*>([\s\S]*?)</span>', re.IGNORECASE),
                re.compile(r'id="postcontent\d+"[^>]*>([\s\S]*?)</div>', re.IGNORECASE),
                re.compile(r'<div class="message"[^>]*>([\s\S]*?)</div>', re.IGNORECASE)
            ]
            for cp in content_patterns:
                cm = cp.search(block)
                if cm:
                    # 清理HTML标签、空格、换行、特殊字符
                    content = re.sub(r'<.*?>', '', cm.group(1))
                    content = re.sub(r'&nbsp;|&gt;|&lt;|&amp;', ' ', content)
                    content = re.sub(r'\s+', ' ', content).strip()
                    break
            
            # 提取PID（增强版规则）
            pid = f"page{page_num}_block{block_idx}"  # 兜底PID
            pid_patterns = [
                re.compile(r'pid(\d+)Anchor', re.IGNORECASE),
                re.compile(r'a name="l(\d+)"', re.IGNORECASE),
                re.compile(r'id="post1strow(\d+)"', re.IGNORECASE),
                re.compile(r'postid=(\d+)', re.IGNORECASE),
                re.compile(r'#pid(\d+)', re.IGNORECASE)
            ]
            for pp in pid_patterns:
                pm = pp.search(block)
                if pm:
                    pid = pm.group(1).strip()
                    break
            
            # 拼接回复链接
            reply_url = f"{NGA_POST_URL.split('#')[0]}#pid{pid}Anchor" if pid.isdigit() else url
            
            # 只保留有内容的回复（降低内容长度阈值）
            if content and len(content) > 2:  # 从5字符降到2字符，减少漏检
                reply_info = {
                    "pid": pid,
                    "uid": current_uid,
                    "username": username,
                    "content": content,
                    "url": reply_url
                }
                target_replies.append(reply_info)
                print(f"✅ 第 {page_num} 页提取到目标回复：PID={pid} | 用户名={username} | 内容={content[:100]}...")
    
    except requests.exceptions.RequestException as e:
        print(f"❌ 第 {page_num} 页网络请求失败：{type(e).__name__} - {e}")
    except Exception as e:
        print(f"❌ 第 {page_num} 页爬取出错：{type(e).__name__} - {e}")
        import traceback
        print(f"📝 异常堆栈：{traceback.format_exc()[:1000]}")
    
    print(f"📊 第 {page_num} 页最终提取到目标回复数：{len(target_replies)}")
    return target_replies

# ===================== 自动翻页爬取全帖（增强版） =====================
def crawl_all_pages():
    """
    自动翻页爬取全帖（增强版）
    - 强制爬取指定页数（FORCE_CRAWL_PAGES）
    - 不再因空页面提前停止
    - 优化页码拼接逻辑
    """
    all_target_replies = []
    
    # 提取帖子基础URL（优化页码拼接）
    if "&page=" in NGA_POST_URL:
        base_url = NGA_POST_URL.split("&page=")[0]
    else:
        base_url = NGA_POST_URL
    # 确保base_url以&结尾（避免页码拼接错误）
    if not base_url.endswith("&") and not base_url.endswith("?"):
        base_url += "&"
    
    print(f"\n🚀 开始爬取全帖：{base_url}")
    print(f"🎯 监控目标UID：{TARGET_UID}")
    print(f"📖 计划爬取页数：{FORCE_CRAWL_PAGES} 页")
    
    # 强制爬取指定页数，不提前停止
    for page in range(1, FORCE_CRAWL_PAGES + 1):
        # 拼接带页码的URL（修复拼接逻辑）
        if page == 1:
            page_url = base_url.replace("&", "", 1)  # 第一页去掉多余的&
        else:
            page_url = f"{base_url}page={page}"
        
        # 爬取当前页
        page_replies = crawl_one_page(page_url)
        if page_replies:
            all_target_replies.extend(page_replies)
    
    print(f"\n📈 全帖爬取完成：共爬取 {FORCE_CRAWL_PAGES} 页，找到目标UID回复 {len(all_target_replies)} 条")
    return all_target_replies

# ===================== Bark推送函数 =====================
def send_to_bark(reply):
    """推送回复到Bark App"""
    if not BARK_KEY:
        print("❌ Bark Key未配置，跳过推送")
        return
    
    # 构造推送参数
    bark_api = f"https://api.day.app/{BARK_KEY}/"
    title = f"【NGA新回复】{reply['username']}(UID:{reply['uid']})"
    # 内容截断（避免超出Bark限制）
    content = reply['content'][:300] + "..." if len(reply['content']) > 300 else reply['content']
    # 推送参数
    params = {
        "title": title,
        "body": content,
        "url": reply['url'],
        "isArchive": 1,  # 保存到Bark历史
        "sound": "bell.caf",  # 推送铃声
        "icon": "https://img.nga.178.com/ngabbs/favicon.ico"  # NGA图标
    }
    
    try:
        response = requests.get(bark_api, params=params, timeout=10)
        if response.status_code == 200 and response.json().get("code") == 200:
            print(f"✅ 推送成功：PID={reply['pid']}")
        else:
            print(f"❌ 推送失败：{response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ 推送异常：{type(e).__name__} - {e}")

# ===================== 主程序逻辑 =====================
if __name__ == "__main__":
    print(f"\n=== NGA监控脚本启动 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    
    # 1. 爬取全帖所有目标回复
    all_target_replies = crawl_all_pages()
    
    # 2. 加载已推送的PID
    pushed_pids = load_pushed_replies()
    
    # 3. 处理回复（首次运行/非首次运行）
    if not pushed_pids:
        # 首次运行：记录所有历史PID，不推送（避免刷屏）
        if all_target_replies:
            all_pids = [reply['pid'] for reply in all_target_replies]
            save_pushed_ids(all_pids)
            print(f"\n🎉 首次运行初始化完成：记录 {len(all_pids)} 条历史回复PID")
            print("ℹ️  下次运行将只推送新回复")
        else:
            print("\nℹ️  首次运行未找到任何目标回复，无PID可记录")
    else:
        # 非首次运行：推送新回复
        new_replies = [r for r in all_target_replies if r['pid'] not in pushed_pids]
        if new_replies:
            print(f"\n🎊 发现 {len(new_replies)} 条新回复，开始推送...")
            for reply in new_replies:
                send_to_bark(reply)
            # 记录新推送的PID
            new_pids = [r['pid'] for r in new_replies]
            save_pushed_ids(new_pids)
        else:
            print("\nℹ️  未发现新回复，无需推送")
    
    print(f"\n=== NGA监控脚本结束 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
