#!/usr/bin/env python3
"""
补充下载徐小明 (1300871220) 第248页剩余文章
Checkpoint显示: lastPage=248, lastArticleIndex=28
第248页有44篇文章，已下载29篇（index 0-28），还缺16篇
"""
import os
import re
import sys
import time
import json
import requests
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from src.spider_fast import SinaSpiderFast

UID = '1300871220'
POSTS_DIR = Path('/mnt/data/blog-downloader') / UID / 'posts'
CHECKPOINT_FILE = POSTS_DIR / f'.checkpoint_{UID}.json'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Referer': 'https://blog.sina.com.cn/',
}

def get_page_article_ids(page=248):
    """获取指定页所有文章ID"""
    url = f'https://blog.sina.com.cn/s/articlelist_{UID}_0_{page}.html'
    r = requests.get(url, headers=HEADERS, timeout=15)
    ids = re.findall(r'/s/blog_([0-9a-z]+)', r.text)
    titles = re.findall(r'title="([^"]+)"[^>]*>\s*([^<]{5,})\s*</a>', r.text)
    return ids, titles

def download_article(article_id: str) -> bool:
    """下载单篇文章"""
    url = f'https://blog.sina.com.cn/s/blog_{article_id}.html'
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f'HTTP {r.status_code}', end='')
            return False
        r.encoding = 'utf-8'
        
        tree = __import__('lxml.html').fromstring(r.text)
        
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
        
        existing = list(POSTS_DIR.glob(f'{article_id}_*.md'))
        if existing:
            return True
        
        frontmatter = f"""---
id: {article_id}
author_uid: {UID}
author_name: 徐小明
published_at: {date or 'unknown'}
fetched_at: {datetime.now().isoformat()}
source_url: {url}
---

# {title}

{content}
"""
        (POSTS_DIR / filename).write_text(frontmatter, encoding='utf-8')
        return True
    except Exception as e:
        return False

def main():
    print(f'徐小明 (1300871220) 补充下载 - 第248页剩余文章')
    
    # 读取 checkpoint 获取 lastArticleIndex
    if CHECKPOINT_FILE.exists():
        ckpt = json.loads(CHECKPOINT_FILE.read_text())
        last_idx = ckpt.get('lastArticleIndex', -1)
        print(f'Checkpoint: lastArticleIndex={last_idx}')
    else:
        last_idx = -1
        print('无checkpoint，从头开始')
    
    # 获取第248页文章
    ids, titles = get_page_article_ids(248)
    print(f'第248页共 {len(ids)} 篇文章')
    
    # 找出缺失的（从last_idx+1开始）
    start_idx = last_idx + 1
    missing_ids = ids[start_idx:]
    print(f'从索引 {start_idx} 开始，需要下载 {len(missing_ids)} 篇')
    
    if not missing_ids:
        print('没有缺失文章，退出')
        return
    
    print('开始下载...')
    for i, aid in enumerate(missing_ids):
        title_map = dict(titles)
        t = title_map.get(aid, aid)[:30]
        print(f'[{i+1}/{len(missing_ids)}] {aid} ({t})', end=' ... ', flush=True)
        ok = download_article(aid)
        print('OK' if ok else 'FAIL')
        time.sleep(0.5)
    
    # 更新 checkpoint
    if CHECKPOINT_FILE.exists():
        ckpt = json.loads(CHECKPOINT_FILE.read_text())
    else:
        ckpt = {'lastPage': 248, 'lastArticleIndex': -1, 'downloaded': 0, 'failedUrls': []}
    ckpt['lastArticleIndex'] = len(ids) - 1  # 全部下载完成
    CHECKPOINT_FILE.write_text(json.dumps(ckpt, indent=2))
    print(f'Checkpoint已更新: lastArticleIndex={ckpt["lastArticleIndex"]}')
    print('完成!')

if __name__ == '__main__':
    main()
