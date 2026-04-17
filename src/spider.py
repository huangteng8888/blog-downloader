"""
Sina Blog spider - optimized with checkpoint/resume
Uses Node.js spider.js for list pages, curl for article content
"""
import re
import json
import time
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


class SinaSpider:
    """Sina blog spider with checkpoint/resume support"""

    def __init__(self, uid: str, output_dir: str = None):
        self.uid = uid
        self.output_dir = Path(output_dir) if output_dir else Path('/tmp/sina_download')
        self.cookie_file = f'/tmp/sina_cookies_{uid}.txt'
        self.checkpoint_file = self.output_dir / f'.checkpoint_{uid}.json'
        self._init_cookies()

    def _init_cookies(self):
        """Fetch blog homepage to get cookies"""
        try:
            cmd = f'curl -s -c {self.cookie_file} -o /dev/null -H "User-Agent: Mozilla/5.0" "https://blog.sina.com.cn/u/{self.uid}"'
            subprocess.run(cmd, shell=True, timeout=10)
        except Exception as e:
            logger.warning(f'Cookie init failed: {e}')

    def _curl(self, url: str) -> tuple:
        """Execute curl and return (status_code, content)"""
        import tempfile
        output_file = tempfile.mktemp(suffix='.html')
        cmd = [
            'curl', '-s', '-L',
            '-o', output_file,
            '-w', '%{http_code}',
            '-b', self.cookie_file,
            '-c', self.cookie_file,
            '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            '-H', 'Accept-Language: zh-CN,zh;q=0.9',
            '-H', 'Referer: https://blog.sina.com.cn/',
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        status = result.stdout.strip()
        content = ''
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                content = f.read()
        except:
            pass
        Path(output_file).unlink(missing_ok=True)
        return status, content

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

    def save_checkpoint(self, data: Dict):
        """Save checkpoint data"""
        try:
            self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
            self.checkpoint_file.write_text(json.dumps(data, indent=2))
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
                self.save_checkpoint(checkpoint)
        except:
            pass

    def fetch_article(self, article_url: str) -> str:
        """Fetch article page"""
        status, content = self._curl(article_url)
        if status != '200':
            logger.warning(f'Fetch returned {status}, re-initing cookies')
            self._init_cookies()
            status, content = self._curl(article_url)
        return content

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
        spider_js = Path(__file__).parent / 'spider.js'
        cmd = ['node', str(spider_js), '--uid', self.uid, '--page', str(page), '--list-only']
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

    def iter_articles(self, max_pages: int = 100, delay: float = 1.5, resume: bool = True) -> Iterator[Dict]:
        """
        Iterate all articles with checkpoint/resume support.
        Respects Sina's pagination: page 1 has ~10 articles, subsequent pages via AJAX.
        """
        checkpoint = self.load_checkpoint() if resume else {'lastPage': 1, 'lastArticleIndex': -1, 'downloaded': 0, 'failedUrls': []}

        for page in range(checkpoint['lastPage'], max_pages + 1):
            try:
                articles = self._get_article_list_via_node(page)
                if not articles:
                    logger.info(f'No articles at page {page}')
                    break

                logger.info(f'Page {page}: {len(articles)} articles')

                start_index = page == checkpoint['lastPage'] and checkpoint['lastArticleIndex'] + 1 or 0

                for i in range(start_index, len(articles)):
                    article = articles[i]
                    url = article['url']

                    # Skip failed URLs
                    if any(f['url'] == url for f in checkpoint.get('failedUrls', [])):
                        logger.info(f'Skipping previously failed: {url}')
                        continue

                    try:
                        html = self.fetch_article(url)
                        if html:
                            parsed = self.parse_article(html, url)
                            if parsed:
                                # Update checkpoint
                                checkpoint = {
                                    'lastPage': page,
                                    'lastArticleIndex': i,
                                    'downloaded': checkpoint['downloaded'] + 1,
                                    'failedUrls': checkpoint.get('failedUrls', [])
                                }
                                self.save_checkpoint(checkpoint)
                                yield parsed

                        time.sleep(delay)
                    except Exception as e:
                        logger.error(f'Error fetching {url}: {e}')
                        self.mark_failed(url, str(e))

            except Exception as e:
                logger.error(f'Page {page} error: {e}')
                break
