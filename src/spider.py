"""
Sina Blog spider - optimized with concurrent downloading and batch checkpoint
Uses Python requests for article content, Node.js for article list
"""
import re
import json
import time
import logging
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
import threading

logger = logging.getLogger(__name__)

# Thread-local session for connection pooling
thread_local = threading.local()

def get_session():
    """Get or create thread-local requests session"""
    if not hasattr(thread_local, 'session'):
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': 'https://blog.sina.com.cn/',
        })
        thread_local.session = session
    return thread_local.session


class SinaSpider:
    """Sina blog spider with checkpoint/resume and concurrent downloading"""

    # Batch size for checkpoint saves
    CHECKPOINT_BATCH = 10

    def __init__(self, uid: str, output_dir: str = None):
        self.uid = uid
        self.output_dir = Path(output_dir) if output_dir else Path('/tmp/sina_download')
        self.checkpoint_file = self.output_dir / f'.checkpoint_{uid}.json'
        self._checkpoint_lock = threading.Lock()
        self._pending_checkpoint = None
        self._last_save_time = 0

    def load_checkpoint(self) -> Dict:
        """Load checkpoint data"""
        try:
            if self.checkpoint_file.exists():
                data = json.loads(self.checkpoint_file.read_text())
                logger.info(f"Checkpoint loaded: page {data.get('lastPage', 1)}, downloaded {data.get('downloaded', 0)}")
                return data
        except Exception as e:
            logger.warning(f'Checkpoint load failed: {e}')
        return {'lastPage': 1, 'lastArticleIndex': -1, 'downloaded': 0, 'failedUrls': []}

    def save_checkpoint(self, data: Dict, immediate: bool = False):
        """Save checkpoint data (batched for performance)"""
        now = time.time()
        with self._checkpoint_lock:
            # Always update pending data
            self._pending_checkpoint = data

            # Save immediately if forced or enough time passed
            if immediate or (now - self._last_save_time) > 5:
                try:
                    self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
                    self.checkpoint_file.write_text(json.dumps(data, indent=2))
                    self._last_save_time = now
                except Exception as e:
                    logger.error(f'Checkpoint save failed: {e}')

    def mark_failed(self, url: str, error: str):
        """Mark URL as failed"""
        try:
            checkpoint = self.load_checkpoint()
            if not any(f['url'] == url for f in checkpoint.get('failedUrls', [])):
                checkpoint.setdefault('failedUrls', []).append({
                    'url': url,
                    'error': error,
                    'time': datetime.now().isoformat()
                })
                self.save_checkpoint(checkpoint, immediate=True)
        except:
            pass

    def fetch_article(self, article_url: str) -> Optional[str]:
        """Fetch article page using requests (connection pooled)"""
        try:
            session = get_session()
            response = session.get(article_url, timeout=30, allow_redirects=True)
            if response.status_code == 200:
                return response.text
            elif response.status_code == 404:
                return None
            else:
                logger.warning(f'Fetch returned {response.status_code}')
                return None
        except Exception as e:
            logger.error(f'Fetch error for {article_url}: {e}')
            return None

    def parse_article(self, html: str, url: str) -> Optional[Dict]:
        """Parse article from HTML"""
        try:
            # Extract title
            title_match = re.search(r'<h2[^>]+class="titName[^"]*"[^>]*>([^<]+)</h2>', html)
            title = title_match.group(1).strip() if title_match else ''

            # Extract date
            date_match = re.search(r'class="time[^"]*">\(([^<]+)\)</span>', html)
            date = date_match.group(1).strip() if date_match else ''

            # Extract content
            content_match = re.search(
                r'<div[^>]+class="articalContent[^"]*"[^>]*>(.*?)<!-- 正文结束 -->',
                html, re.DOTALL)
            if content_match:
                text = re.sub(r'<[^>]+>', '', content_match.group(1))
                text = re.sub(r'&nbsp;', ' ', text)
                text = re.sub(r'&lt;', '<', text)
                text = re.sub(r'&gt;', '>', text)
                text = re.sub(r'&#\d+;', '', text)
                content = re.sub(r'\s+', ' ', text).strip()
            else:
                content = ''

            # Extract images
            images = re.findall(r'<img[^>]+src="([^"]+)"', html)

            # Extract article ID (includes hex suffix like 'o', 'n', etc.)
            article_id_match = re.search(r'blog_([0-9a-z]+)', url)
            article_id = article_id_match.group(1) if article_id_match else ''

            # Extract tags
            tags_match = re.search(r"var \$tag='([^']+)'", html)
            tags = [t.strip() for t in tags_match.group(1).split(',')] if tags_match else []

            return {
                'id': article_id,
                'title': title,
                'url': url,
                'date': date,
                'tags': tags,
                'content': content,
                'images': images,
            }
        except Exception as e:
            logger.error(f'Parse error for {url}: {e}')
            return None

    def _get_article_list_via_node(self, page: int = 1) -> List[Dict]:
        """Get article list using node/spider.js"""
        import subprocess
        spider_js = Path(__file__).parent / 'spider.js'
        cmd = ['node', str(spider_js), f'--uid={self.uid}', f'--page={page}', '--list-only']
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and result.stdout:
                text = result.stdout.strip()
                json_start = text.find('[')
                if json_start >= 0:
                    return json.loads(text[json_start:])
        except Exception as e:
            logger.error(f'Node spider failed: {e}')
        return []

    def _fetch_and_parse(self, url: str) -> Optional[Dict]:
        """Fetch and parse single article"""
        html = self.fetch_article(url)
        if html:
            return self.parse_article(html, url)
        return None

    def iter_articles(self, max_pages: int = 100, delay: float = 0.1, resume: bool = True,
                      concurrent: int = 5) -> Iterator[Dict]:
        """
        Iterate all articles with checkpoint/resume and concurrent downloading.
        """
        checkpoint = self.load_checkpoint() if resume else {'lastPage': 1, 'lastArticleIndex': -1, 'downloaded': 0, 'failedUrls': []}
        articles_since_checkpoint = 0

        for page in range(checkpoint['lastPage'], max_pages + 1):
            try:
                articles = self._get_article_list_via_node(page)
                if not articles:
                    logger.info(f'No articles at page {page}')
                    break

                logger.info(f'Page {page}: {len(articles)} articles')

                start_index = page == checkpoint['lastPage'] and checkpoint['lastArticleIndex'] + 1 or 0
                page_articles = articles[start_index:]

                if not page_articles:
                    continue

                # Concurrent fetching for this page's articles
                with ThreadPoolExecutor(max_workers=min(concurrent, len(page_articles))) as executor:
                    future_to_idx = {
                        executor.submit(self._fetch_and_parse, art['url']): (i + start_index, art['url'])
                        for i, art in enumerate(page_articles)
                    }

                    for future in as_completed(future_to_idx):
                        idx, url = future_to_idx[future]
                        try:
                            parsed = future.result()
                            if parsed:
                                checkpoint['downloaded'] += 1
                                checkpoint['lastPage'] = page
                                checkpoint['lastArticleIndex'] = idx
                                articles_since_checkpoint += 1
                                yield parsed

                                # Batch checkpoint saves
                                if articles_since_checkpoint >= self.CHECKPOINT_BATCH:
                                    self.save_checkpoint(checkpoint)
                                    articles_since_checkpoint = 0

                        except Exception as e:
                            logger.error(f'Error processing {url}: {e}')
                            self.mark_failed(url, str(e))

                # Save checkpoint after each page
                self.save_checkpoint(checkpoint)

                # Small delay between pages
                if delay > 0:
                    time.sleep(delay)

            except Exception as e:
                logger.error(f'Page {page} error: {e}')
                break

        # Final checkpoint save
        self.save_checkpoint(checkpoint, immediate=True)
