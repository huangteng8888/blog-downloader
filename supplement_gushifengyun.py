#!/usr/bin/env python3
"""
补充下载股市风云 (1285707277) 第146页缺失的文章
已知: 页面有31篇(过滤后), 我们有7281篇=145*50+31, 所以page 146应该全在但扫描显示缺31篇
直接下载31篇并保存
"""
import os
import re
import sys
import time
import requests
from pathlib import Path
from datetime import datetime
from lxml import html

UID = '1285707277'
POSTS_DIR = Path('/mnt/data/blog-downloader') / UID / 'posts'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Referer': 'https://blog.sina.com.cn/',
}

def get_page_146_ids():
    """获取第146页文章ID (过滤掉非文章链接)"""
    url = f'https://blog.sina.com.cn/s/articlelist_{UID}_0_146.html'
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.encoding = 'utf-8'
    tree = html.fromstring(r.text)
    
    all_links = tree.xpath('//a[contains(@href, "/s/blog_")]')
    article_ids = []
    for a in all_links:
        href = a.get('href', '')
        title = (a.text_content() or '').strip()
        if '/s/blog_' in href and len(title) > 5:
            m = re.search(r'blog_([0-9a-z]+)', href)
            if m:
                article_ids.append(m.group(1))
    return list(set(article_ids))

def build_existing_set():
    """用 scandir 扫描已存在文件，避免 glob"""
    existing = set()
    try:
        with os.scandir(POSTS_DIR) as it:
            for entry in it:
                if entry.name.endswith('.md'):
                    parts = entry.name.split('_')
                    if parts:
                        existing.add(parts[0])
    except Exception as e:
        print(f'Warning: scandir error: {e}')
    return existing

def download_article(article_id: str) -> tuple:
    """下载单篇文章，返回 (ok, filename)"""
    url = f'https://blog.sina.com.cn/s/blog_{article_id}.html'
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return False, f'HTTP {r.status_code}'
        r.encoding = 'utf-8'
        tree = html.fromstring(r.text)
        
        title_el = tree.xpath('//h2[contains(@class, "titName")]')
        title = title_el[0].text_content().strip() if title_el else article_id
        
        date_el = tree.xpath('//span[contains(@class, "time")]')
        date = ''
        if date_el:
            date_match = re.search(r'\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\)', date_el[0].text_content())
            if date_match:
                date = date_match.group(1)
        
        content_el = tree.xpath('//div[contains(@class, "articalContent")]')
        content = content_el[0].text_content() if content_el else ''
        content = re.sub(r'\s+', ' ', content).strip()
        
        safe_title = re.sub(r'[^\w\u4e00-\u9fff]', '_', title)[:50]
        filename = f'{article_id}_{safe_title}.md'
        
        frontmatter = f"""---
id: {article_id}
author_uid: {UID}
author_name: 股市风云
published_at: {date or 'unknown'}
fetched_at: {datetime.now().isoformat()}
source_url: {url}
---

# {title}

{content}
"""
        filepath = POSTS_DIR / filename
        filepath.write_text(frontmatter, encoding='utf-8')
        return True, filename
    except Exception as e:
        return False, str(e)

def main():
    print(f'股市风云 (1285707277) 补充下载')
    print(f'Posts目录: {POSTS_DIR}')
    
    article_ids = get_page_146_ids()
    print(f'第146页文章: {len(article_ids)} 篇')
    
    if not article_ids:
        print('无法获取文章列表，退出')
        return
    
    existing = build_existing_set()
    print(f'已存在文章ID: {len(existing)}')
    
    missing = [aid for aid in article_ids if aid not in existing]
    print(f'缺失: {len(missing)} 篇')
    
    if not missing:
        print('没有缺失文章，退出')
        return
    
    print(f'\n开始下载缺失文章...')
    ok_count = 0
    for i, aid in enumerate(missing):
        print(f'[{i+1}/{len(missing)}] {aid} ... ', end='', flush=True)
        ok, result = download_article(aid)
        if ok:
            print(f'OK -> {result[:40]}...')
            ok_count += 1
        else:
            print(f'FAIL: {result}')
        time.sleep(0.8)  # 避免触发反爬
    
    print(f'\n完成: 成功 {ok_count}/{len(missing)} 篇')

if __name__ == '__main__':
    main()
