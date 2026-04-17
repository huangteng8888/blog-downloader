"""
Blog metadata layer - comprehensive metadata management for AI knowledge base
"""
import json
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from collections import Counter


class BlogMetadata:
    """
    Three-layer metadata architecture:

    1. blogger.json    - Blogger profile info
    2. download.json   - Download session statistics
    3. index.json      - All posts index (fast lookup)
    """

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.blogger_file = output_dir / 'blogger.json'
        self.download_file = output_dir / 'download.json'
        self.index_file = output_dir / 'posts' / 'index.json'

    # ============ Layer 1: Blogger Profile ============

    def save_blogger_info(self, blogger: Dict):
        """Save/update blogger profile"""
        info = {
            'uid': blogger.get('uid', ''),
            'name': blogger.get('name', ''),
            'domain': f"https://blog.sina.com.cn/u/{blogger.get('uid', '')}",
            'total_articles': blogger.get('total_articles', 0),
            'profile': {
                'description': blogger.get('description', ''),
                'category': blogger.get('category', ''),
            },
            'created_at': datetime.now().isoformat(),
        }
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.blogger_file.write_text(json.dumps(info, indent=2, ensure_ascii=False))

    def load_blogger_info(self) -> Optional[Dict]:
        """Load blogger profile"""
        if self.blogger_file.exists():
            return json.loads(self.blogger_file.read_text())
        return None

    # ============ Layer 2: Download Statistics ============

    def init_download_session(self, uid: str, total_expected: int = 0):
        """Initialize a new download session"""
        self.download_info = {
            'uid': uid,
            'session_id': datetime.now().strftime('%Y%m%d_%H%M%S'),
            'started_at': datetime.now().isoformat(),
            'completed_at': None,
            'status': 'running',
            'total_expected': total_expected,
            'total_downloaded': 0,
            'total_failed': 0,
            'pages_completed': 0,
            'last_page': 0,
            'last_article_index': -1,
            'total_words': 0,
            'total_images': 0,
            'failed_urls': [],
        }
        self._save_download()

    def update_progress(self, page: int, article_index: int, downloaded: int,
                       words: int = 0, images: int = 0):
        """Update download progress"""
        if hasattr(self, 'download_info'):
            self.download_info['last_page'] = page
            self.download_info['last_article_index'] = article_index
            self.download_info['total_downloaded'] = downloaded
            self.download_info['pages_completed'] = page
            self.download_info['total_words'] += words
            self.download_info['total_images'] += images
            self._save_download()

    def mark_failed(self, url: str, error: str):
        """Record a failed URL"""
        if hasattr(self, 'download_info'):
            self.download_info['failed_urls'].append({
                'url': url,
                'error': error,
                'time': datetime.now().isoformat()
            })
            self.download_info['total_failed'] += 1
            self._save_download()

    def complete_session(self):
        """Mark session as completed"""
        if hasattr(self, 'download_info'):
            self.download_info['status'] = 'completed'
            self.download_info['completed_at'] = datetime.now().isoformat()
            self._save_download()

    def _save_download(self):
        """Save download info to file"""
        self.download_file.parent.mkdir(parents=True, exist_ok=True)
        self.download_file.write_text(json.dumps(self.download_info, indent=2, ensure_ascii=False))

    def load_download_info(self) -> Optional[Dict]:
        """Load download session info"""
        if self.download_file.exists():
            self.download_info = json.loads(self.download_file.read_text())
            return self.download_info
        return None

    # ============ Layer 3: Post Index ============

    def init_index(self):
        """Initialize empty post index or load existing"""
        self.posts_index = []
        if self.index_file.exists():
            try:
                data = json.loads(self.index_file.read_text())
                self.posts_index = data.get('posts', [])
            except:
                pass
        else:
            self._save_index()

    def add_post(self, post: Dict):
        """Add post to index"""
        self.posts_index.append({
            'id': post['id'],
            'title': post['title'],
            'published_at': post['published_at'],
            'published_date': post['published_at'][:10] if post.get('published_at') else '',
            'category': post.get('category', ''),
            'tags': post.get('tags', []),
            'word_count': post.get('word_count', 0),
            'images_count': len(post.get('images', [])),
            'filename': post.get('filename', ''),
            'source_url': post.get('source_url', ''),
            'indexed_at': datetime.now().isoformat(),
        })
        self._save_index()

    def _save_index(self):
        """Save posts index"""
        self.index_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'total': len(self.posts_index),
            'updated_at': datetime.now().isoformat(),
            'posts': self.posts_index,
        }
        self.index_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def generate_statistics(self) -> Dict:
        """Generate statistics from index"""
        if not self.posts_index:
            self.init_index()

        words = [p['word_count'] for p in self.posts_index]
        tags = [t for p in self.posts_index for t in p.get('tags', [])]
        dates = [p['published_date'] for p in self.posts_index if p.get('published_date')]

        tag_counts = Counter(tags).most_common(20)

        return {
            'total_posts': len(self.posts_index),
            'total_words': sum(words),
            'avg_words': sum(words) // len(words) if words else 0,
            'total_images': sum(p['images_count'] for p in self.posts_index),
            'date_range': {
                'earliest': min(dates) if dates else None,
                'latest': max(dates) if dates else None,
            },
            'top_tags': [{'tag': t, 'count': c} for t, c in tag_counts],
            'posts_by_month': self._posts_by_month(),
        }

    def _posts_by_month(self) -> Dict:
        """Group posts by month"""
        by_month = {}
        for p in self.posts_index:
            if p.get('published_date'):
                month = p['published_date'][:7]  # YYYY-MM
                by_month[month] = by_month.get(month, 0) + 1
        return dict(sorted(by_month.items(), reverse=True)[:24])

    def get_checkpoint_data(self) -> Dict:
        """Get checkpoint data for resuming"""
        if hasattr(self, 'download_info'):
            return {
                'lastPage': self.download_info.get('last_page', 1),
                'lastArticleIndex': self.download_info.get('last_article_index', -1),
                'downloaded': self.download_info.get('total_downloaded', 0),
                'failedUrls': [f['url'] for f in self.download_info.get('failed_urls', [])],
            }
        return {'lastPage': 1, 'lastArticleIndex': -1, 'downloaded': 0, 'failedUrls': []}


class PostMetadata:
    """Individual post frontmatter - Graphify compatible"""

    FRONTMATTER_TEMPLATE = """---
id: {id}
author_uid: {author_uid}
author_name: {author_name}
published_at: {published_at}
fetched_at: {fetched_at}
type: {post_type}
category: {category}
tags: {tags}
word_count: {word_count}
source_url: {source_url}
likes: {likes}
comments: {comments}
images_count: {images_count}
images: {images}
keywords: {keywords}
---

# {title}

{content}
"""

    @staticmethod
    def generate(content: str, images: List[str], **kwargs) -> str:
        """Generate markdown with frontmatter"""
        # Extract keywords from content
        keywords = PostMetadata._extract_keywords(content)

        return PostMetadata.FRONTMATTER_TEMPLATE.format(
            id=kwargs.get('id', ''),
            author_uid=kwargs.get('author_uid', ''),
            author_name=kwargs.get('author_name', ''),
            published_at=kwargs.get('published_at', ''),
            fetched_at=datetime.now().isoformat(),
            post_type=kwargs.get('type', 'article'),
            category=kwargs.get('category', ''),
            tags=json.dumps(kwargs.get('tags', []), ensure_ascii=False),
            word_count=len(content),
            source_url=kwargs.get('source_url', ''),
            likes=kwargs.get('likes', 0),
            comments=kwargs.get('comments', 0),
            images_count=len(images),
            images=json.dumps(images, ensure_ascii=False),
            keywords=json.dumps(keywords, ensure_ascii=False),
            title=kwargs.get('title', ''),
            content=content
        )

    @staticmethod
    def _extract_keywords(text: str, top_n: int = 10) -> List[str]:
        """Extract keywords from content"""
        # Chinese characters
        chinese = re.findall(r'[\u4e00-\u9fff]+', text)
        # English words
        english = re.findall(r'[a-zA-Z]{3,}', text.lower())

        # Count frequencies
        words = {}
        for w in chinese + english:
            if len(w) >= 2:
                words[w] = words.get(w, 0) + 1

        # Return top N
        return sorted(words, key=words.get, reverse=True)[:top_n]
