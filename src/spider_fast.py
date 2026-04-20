"""
High-performance Sina Blog Spider
- Pure Python (no Node.js/Playwright needed for article lists)
- Static HTML article lists parsed directly with lxml
- asyncio + aiohttp for concurrent article downloads
"""
import re
import json
import time
import logging
import asyncio
import aiohttp
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, AsyncIterator, Iterator
from lxml import html
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# Global session for connection pooling
_session = None

def get_session() -> requests.Session:
    """Get or create global requests session with connection pooling"""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        })
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=3
        )
        _session.mount('http://', adapter)
        _session.mount('https://', adapter)
    return _session


class SinaSpiderFast:
    """High-performance Sina blog spider using async IO"""

    def __init__(self, uid: str, output_dir: str = None):
        self.uid = uid
        self.output_dir = Path(output_dir) if output_dir else Path('/tmp/sina_download')
        self.checkpoint_file = self.output_dir / f'.checkpoint_{uid}.json'
        self._checkpoint_lock = asyncio.Lock()
        self._pending_checkpoint = None
        self._downloaded = 0
        self.CHECKPOINT_BATCH = 50

    def load_checkpoint(self) -> Dict:
        """Load checkpoint data"""
        try:
            if self.checkpoint_file.exists():
                data = json.loads(self.checkpoint_file.read_text())
                logger.info(f"Checkpoint: page {data.get('lastPage', 1)}, downloaded {data.get('downloaded', 0)}")
                self._downloaded = data.get('downloaded', 0)
                return data
        except Exception as e:
            logger.warning(f'Checkpoint load failed: {e}')
        return {'lastPage': 1, 'lastArticleIndex': -1, 'downloaded': 0, 'failedUrls': []}

    def save_checkpoint(self, data: Dict, immediate: bool = False):
        """Save checkpoint data"""
        try:
            self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
            self.checkpoint_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f'Checkpoint save failed: {e}')

    def get_article_list(self, page: int) -> List[Dict]:
        """Fetch article list from static HTML page (no Playwright needed!)"""
        url = f"https://blog.sina.com.cn/s/articlelist_{self.uid}_0_{page}.html"
        session = get_session()

        try:
            response = session.get(url, timeout=15)
            if response.status_code != 200:
                logger.warning(f"Page {page} returned {response.status_code}")
                return []

            # Parse HTML with lxml (much faster than regex)
            tree = html.fromstring(response.text)

            articles = []
            seen_ids = set()

            # XPath for article links
            for a in tree.xpath('//a[contains(@href, "/s/blog_")]'):
                href = a.get('href', '')
                title = a.text_content().strip()

                if '/s/blog_' not in href or not title or len(title) < 5:
                    continue

                # Extract article ID
                match = re.search(r'blog_([0-9a-z]+)', href)
                if not match:
                    continue

                article_id = match.group(1)
                if article_id in seen_ids:
                    continue
                seen_ids.add(article_id)

                # Build full URL
                if href.startswith('//'):
                    url = 'https:' + href
                elif href.startswith('/'):
                    url = 'https://blog.sina.com.cn' + href
                else:
                    url = href

                articles.append({
                    'id': article_id,
                    'title': title,
                    'url': url
                })

            return articles

        except Exception as e:
            logger.error(f"Failed to get article list page {page}: {e}")
            return []

    def parse_article(self, html_content: str, url: str) -> Optional[Dict]:
        """Parse article HTML"""
        try:
            tree = html.fromstring(html_content)

            # Extract title
            title_el = tree.xpath('//h2[contains(@class, "titName")]')
            title = title_el[0].text_content().strip() if title_el else ''

            # Extract date
            date_el = tree.xpath('//span[contains(@class, "time")]')
            date = ''
            if date_el:
                date_match = re.search(r'\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\)', date_el[0].text_content())
                if date_match:
                    date = date_match.group(1)

            # Extract content
            content_el = tree.xpath('//div[contains(@class, "articalContent")]')
            if content_el:
                content_text = content_el[0].text_content()
                content = re.sub(r'\s+', ' ', content_text).strip()
            else:
                content = ''

            # Extract images
            images = [img.get('src', '') for img in tree.xpath('//div[contains(@class, "articalContent")]//img')]
            images = [img for img in images if img]

            # Extract article ID
            article_id_match = re.search(r'blog_([0-9a-z]+)', url)
            article_id = article_id_match.group(1) if article_id_match else ''

            # Extract tags
            tags = []
            tag_script = tree.xpath('//script[contains(text(), "$tag")]')
            if tag_script:
                tag_match = re.search(r"var \$tag='([^']+)'", tag_script[0].text_content())
                if tag_match:
                    tags = [t.strip() for t in tag_match.group(1).split(',') if t.strip()]

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

    async def fetch_article_async(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict]:
        """Fetch and parse single article asynchronously"""
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status != 200:
                    return None
                html_content = await response.text()
                return self.parse_article(html_content, url)
        except Exception as e:
            logger.debug(f'Fetch error for {url}: {e}')
            return None

    async def download_page_async(self, page: int, concurrent: int = 20) -> List[Dict]:
        """Download all articles on a page concurrently"""
        articles = self.get_article_list(page)
        if not articles:
            return []

        logger.info(f"Page {page}: {len(articles)} articles to download")

        results = []
        connector = aiohttp.TCPConnector(limit=concurrent, limit_per_host=10)
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            tasks = [self.fetch_article_async(session, art['url']) for art in articles]

            for i, coro in enumerate(asyncio.as_completed(tasks)):
                result = await coro
                if result:
                    results.append(result)
                    self._downloaded += 1

                    # Batch checkpoint
                    if self._downloaded % self.CHECKPOINT_BATCH == 0:
                        checkpoint = self.load_checkpoint()
                        checkpoint['downloaded'] = self._downloaded
                        checkpoint['lastPage'] = page
                        checkpoint['lastArticleIndex'] = i
                        self.save_checkpoint(checkpoint)

        return results

    def find_last_page(self, max_pages: int = 300) -> int:
        """Binary search to find actual last page"""
        session = get_session()
        low, high = 1, max_pages

        def check_page(p: int) -> bool:
            url = f"https://blog.sina.com.cn/s/articlelist_{self.uid}_0_{p}.html"
            try:
                r = session.get(url, timeout=10)
                return r.status_code == 200 and len(r.text) > 5000
            except:
                return False

        while low < high:
            mid = (low + high + 1) // 2
            if check_page(mid):
                low = mid
            else:
                high = mid - 1

        return low if check_page(low) else 0

    async def iter_articles_async(self, max_pages: int = 100, concurrent: int = 20,
                                   resume: bool = True, delay: float = 0.1) -> AsyncIterator[Dict]:
        """Async iterator for articles"""
        checkpoint = self.load_checkpoint() if resume else {'lastPage': 1, 'lastArticleIndex': -1, 'downloaded': 0, 'failedUrls': []}

        # Find actual last page if starting fresh
        if checkpoint['lastPage'] == 1:
            logger.info("Finding last page...")
            last_page = self.find_last_page(max_pages)
            logger.info(f"Last page: {last_page}")
            max_pages = min(max_pages, last_page)

        for page in range(checkpoint['lastPage'], max_pages + 1):
            try:
                articles = await self.download_page_async(page, concurrent)
                logger.info(f"Page {page}: downloaded {len(articles)} articles")

                for i, article in enumerate(articles):
                    checkpoint['lastPage'] = page
                    checkpoint['lastArticleIndex'] = i
                    checkpoint['downloaded'] = self._downloaded
                    yield article

                # Save checkpoint after page
                self.save_checkpoint(checkpoint)

                if delay > 0:
                    await asyncio.sleep(delay)

            except Exception as e:
                logger.error(f"Page {page} error: {e}")
                break

        # Final checkpoint
        self.save_checkpoint(checkpoint, immediate=True)


# Synchronous wrapper for compatibility
class SinaSpider(SinaSpiderFast):
    """Synchronous wrapper for backward compatibility"""

    def iter_articles(self, max_pages: int = 100, delay: float = 0.1, resume: bool = True,
                      concurrent: int = 10) -> Iterator[Dict]:
        """Sync iter_articles using asyncio.run"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            iter_obj = self.iter_articles_async(max_pages, concurrent, resume, delay)
            while True:
                try:
                    yield loop.run_until_complete(iter_obj.__anext__())
                except StopAsyncIteration:
                    break
        finally:
            loop.close()
