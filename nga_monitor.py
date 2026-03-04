def crawl_one_page(url):
    replies = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.encoding = "utf-8"
        html = r.text
        print(f"第 {url.split('page=')[-1]} 页内容长度：{len(html)}")
        
        # 暴力提取所有UID和对应的内容
        # 第一步：提取所有UID和所在位置
        uid_pattern = re.compile(r'userClick\(event,&quot;(\d+)&quot;\)', re.IGNORECASE)
        uid_matches = list(uid_pattern.finditer(html))
        
        # 第二步：遍历每个UID，提取附近的内容
        for uid_match in uid_matches:
            uid = uid_match.group(1)
            if uid != TARGET_UID:
                continue  # 只处理目标UID
            
            # 取UID前后2000字符，提取内容
            start = max(0, uid_match.start() - 2000)
            end = min(len(html), uid_match.end() + 2000)
            context = html[start:end]
            
            # 提取用户名
            name_pattern = re.compile(r'<b class="block_txt"[^>]*>([^<]+)</b>([^<]+)</a>', re.DOTALL)
            name_match = name_pattern.search(context)
            username = (name_match.group(1) + name_match.group(2)).strip() if name_match else "未知用户"
            
            # 提取内容
            content_pattern = re.compile(r'<span class="postcontent ubbcode"[^>]*>([\s\S]*?)</span>', re.DOTALL)
            content_match = content_pattern.search(context)
            content = ""
            if content_match:
                content = re.sub(r'<.*?>', '', content_match.group(1)).strip()
                content = re.sub(r'\s+', ' ', content)
            
            # 提取pid
            pid_pattern = re.compile(r'pid(\d+)Anchor', re.IGNORECASE)
            pid_match = pid_pattern.search(context)
            pid = pid_match.group(1) if pid_match else f"page{url.split('page=')[-1]}_{uid_match.start()}"
            
            if content:  # 只保留有内容的回复
                replies.append({
                    "pid": pid,
                    "uid": uid,
                    "username": username,
                    "content": content,
                    "url": f"{url}#pid{pid}Anchor" if pid_match else url
                })
                
        print(f"第 {url.split('page=')[-1]} 页提取到目标UID回复：{len(replies)} 条")
    except Exception as e:
        print(f"⚠️ 爬取失败: {e}")
        import traceback
        print(traceback.format_exc())
    return replies
