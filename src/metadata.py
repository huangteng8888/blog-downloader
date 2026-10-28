"""
Blog metadata layer - two-layer architecture for AI knowledge base

Layer 1: metadata.json     - Blogger info only
Layer 2: posts/index.json  - Post index with aggregated tags/dates
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from collections import Counter

logger = logging.getLogger(__name__)


class BlogMetadata:
    """
    Two-layer metadata architecture:

    1. output/{uid}/metadata.json
       - bloggers: {uid: {name}}

    2. output/{uid}/posts/index.json
       - posts: [{article_id, title, pub_time, ...}]
       - date_index: {YYYY-MM: count}
    """

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.metadata_file = output_dir / 'metadata.json'
        self.posts_index_file = output_dir / 'posts' / 'index.json'
        self._dirty = False  # batch-write flag

    # ============ Layer 1: metadata.json ============

    def init_blogger(self, uid: str, name: str):
        """Initialize blogger info in metadata"""
        data = self._load_metadata()

        if 'bloggers' not in data:
            data['bloggers'] = {}

        data['bloggers'][uid] = {
            'name': name
        }

        self._save_metadata(data)

    def _load_metadata(self) -> Dict:
        """Load or initialize metadata.json"""
        if self.metadata_file.exists():
            return json.loads(self.metadata_file.read_text(encoding='utf-8'))

        return {
            'version': '2.0',
            'bloggers': {}
        }

    def _save_metadata(self, data: Dict):
        """Save metadata.json"""
        data['version'] = '2.0'
        self.metadata_file.parent.mkdir(parents=True, exist_ok=True)
        self.metadata_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8'
        )

    # ============ Layer 2: posts/index.json ============

    def init_posts_index(self):
        """Initialize or load posts index"""
        self.posts = []
        if self.posts_index_file.exists():
            data = json.loads(self.posts_index_file.read_text(encoding='utf-8'))
            self.posts = data.get('posts', [])
        else:
            self._save_posts_index()

    def add_post(self, post: Dict):
        """Add post to index (idempotent — dedupes by article_id)"""
        article_id = post.get('article_id', '')
        # Skip if already indexed (idempotent — safe to call multiple times)
        if article_id and any(p.get('article_id') == article_id for p in self.posts):
            return
        post_entry = {
            'article_id': article_id,
            'title': post.get('title', ''),
            'pub_time': post.get('pub_time', ''),
            'pub_date': post.get('pub_time', '')[:10] if post.get('pub_time') else '',
            'timestamp': self._parse_timestamp(post.get('pub_time')),
            'author_uid': post.get('author_uid', ''),
            'author_name': post.get('author_name', ''),
            'tags': post.get('tags', []),
            'images_count': len(post.get('image_urls', [])),
            'filename': post.get('filename', ''),
            'source_url': post.get('source_url', ''),
            'indexed_at': datetime.now().isoformat(),
        }
        self.posts.append(post_entry)
        self._dirty = True  # mark dirty, will be saved on save()

    def generate_indexes(self):
        """Generate date indexes from posts"""
        date_counter = Counter()

        for p in self.posts:
            pub_date = p.get('pub_date') or (p.get('published_date', '')[:10] if p.get('published_date') else '')
            if pub_date:
                date_counter[pub_date[:7]] += 1  # YYYY-MM

        return {
            'date_index': dict(sorted(date_counter.items(), reverse=True)[:24]),
        }

    def save(self):
        """Save index if dirty (call this at end of run)"""
        if self._dirty:
            self._save_posts_index()
            self._dirty = False

    def rebuild_from_files(self, preserve_metadata: bool = True):
        """Rebuild index by scanning all .md files on disk (recovery mechanism).

        Extracts article_id from frontmatter (article_id:/id:) or from
        source_url in content. Handles both new-format and legacy-format files.

        ⚠️ 重要：v2 的单篇 md frontmatter 只含 article_id，正文也不含时间/作者信息，
        因此"从 md 反推"无法恢复 pub_time / author_name。为避免重建时静默丢失元数据，
        本方法默认（preserve_metadata=True）会：

          1. 先把现有 index.json 备份为 index.json.bak（不覆盖已有备份）；
          2. 读入现有 index.json 建立 article_id → 元数据 的存续映射；
          3. 重建时用该映射回填缺失的 pub_time / author_uid / author_name / tags 等字段；
          4. 结束后打印元数据覆盖率，覆盖率过低时发出告警。
        """
        import re
        posts_dir = self.posts_index_file.parent
        seen_ids = set()
        legacy_meta = {}

        # ── 1. 备份并加载现有索引作为元数据来源 ──────────────────────
        if preserve_metadata and self.posts_index_file.exists():
            try:
                raw = self.posts_index_file.read_text(encoding='utf-8')
                old = json.loads(raw)
                for p in old.get('posts', []):
                    aid = p.get('article_id')
                    if aid:
                        legacy_meta[aid] = p

                bak = self.posts_index_file.with_name('index.json.bak')
                if not bak.exists():
                    bak.write_text(raw, encoding='utf-8')
            except Exception as e:
                logger.warning('加载现有索引失败，将无法保留元数据: %s', e)

        self.posts = []

        for md_file in posts_dir.glob('*.md'):
            content = md_file.read_text(encoding='utf-8')

            # Try frontmatter first (new format: article_id: or id:)
            article_id = ''
            title = ''
            pub_time = ''
            author_uid = ''
            author_name = ''
            source_url = ''
            tags = []
            images_count = 0

            # Parse frontmatter
            fm_match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
            if fm_match:
                fm_text = fm_match.group(1)
                for line in fm_text.split('\n'):
                    if line.startswith('article_id:') or line.startswith('id:'):
                        article_id = line.split(':', 1)[1].strip()
                    elif line.startswith('title:'):
                        title = line.split(':', 1)[1].strip()
                    elif line.startswith('pub_time:') or line.startswith('published_at:'):
                        pub_time = line.split(':', 1)[1].strip()
                    elif line.startswith('author_uid:'):
                        author_uid = line.split(':', 1)[1].strip()
                    elif line.startswith('author_name:'):
                        author_name = line.split(':', 1)[1].strip()
                    elif line.startswith('tags:'):
                        raw_tags = line.split(':', 1)[1].strip()
                        if raw_tags.startswith('[') and raw_tags.endswith(']'):
                            tags = [
                                t.strip().strip('"').strip("'")
                                for t in raw_tags[1:-1].split(',')
                                if t.strip().strip('"').strip("'")
                            ]

            # Fallback: extract article_id from source_url in content (must be exactly 16 chars)
            if not article_id:
                url_match = re.search(r'blog_([a-z0-9]{16})\.html', content)
                if url_match:
                    article_id = url_match.group(1)

            if not article_id or article_id in seen_ids:
                continue
            seen_ids.add(article_id)

            # ── 2. 用现有索引回填 md 里拿不到的字段 ──────────────────
            old = legacy_meta.get(article_id) or {}
            if not pub_time:
                pub_time = old.get('pub_time', '') or ''
            if not author_uid:
                author_uid = old.get('author_uid', '') or ''
            if not author_name:
                author_name = old.get('author_name', '') or ''
            if not tags:
                tags = old.get('tags', []) or []
            if not title:
                title = old.get('title', '') or ''

            # Reconstruct source_url from article_id (URL pattern is always blog_{article_id}.html)
            if not source_url:
                source_url = f'https://blog.sina.com.cn/s/blog_{article_id}.html'

            # Extract title from first non-empty line of article content (skip YAML frontmatter)
            if not title:
                body = re.split(r'^---\n.*?\n---', content, maxsplit=1, flags=re.DOTALL)
                # 有 frontmatter 时取正文部分；无 frontmatter 的遗留文件直接取全文首行
                body_text = body[1] if len(body) > 1 else body[0]
                lines = body_text.strip().split('\n')
                for line in lines:
                    stripped = line.strip()
                    # Skip markdown headers (# title) and empty lines
                    if stripped and not stripped.startswith('#'):
                        title = stripped
                        break

            post_entry = {
                'article_id': article_id,
                'title': title,
                'pub_time': pub_time,
                'pub_date': pub_time[:10] if pub_time else '',
                'timestamp': self._parse_timestamp(pub_time),
                'author_uid': author_uid,
                'author_name': author_name,
                'tags': tags,
                'images_count': images_count,
                'filename': md_file.name,
                'source_url': source_url,
                'indexed_at': datetime.now().isoformat(),
            }
            self.posts.append(post_entry)
            self._dirty = True

        # Sort by timestamp descending
        self.posts.sort(key=lambda p: p.get('timestamp') or 0, reverse=True)
        self.save()

        # ── 3. 元数据覆盖率告警 ────────────────────────────────────
        total = len(self.posts)
        if total:
            with_time = sum(1 for p in self.posts if p.get('pub_time'))
            with_author = sum(1 for p in self.posts if p.get('author_name'))
            logger.info(
                'rebuild 完成：%d 篇；pub_time 覆盖 %d (%.1f%%)，author_name 覆盖 %d (%.1f%%)',
                total, with_time, with_time / total * 100,
                with_author, with_author / total * 100,
            )
            if with_time / total < 0.5:
                logger.warning(
                    '⚠️ 元数据覆盖率过低（pub_time %.1f%%）！'
                    'v2 的单篇 md 不含时间/作者信息，从 md 反推无法恢复。'
                    '已尽力从 index.json.bak 回填；若仍偏低，请从 v1 旧数据（legacy/）'
                    '回填 published_at 与 author_name，而不是直接信任重建结果。',
                    with_time / total * 100,
                )
        return total

    def generate_statistics(self) -> Dict:
        """Generate overall statistics"""
        if not self.posts:
            self.init_posts_index()

        dates = []
        for p in self.posts:
            pd = p.get('pub_date') or (p.get('published_date', '')[:10] if p.get('published_date') else '')
            if pd:
                dates.append(pd)
        indexes = self.generate_indexes()

        return {
            'total_posts': len(self.posts),
            'total_images': sum(p['images_count'] for p in self.posts),
            'date_range': {
                'earliest': min(dates) if dates else None,
                'latest': max(dates) if dates else None,
            },
            'posts_by_month': indexes['date_index']
        }

    def _save_posts_index(self):
        """Save posts index with aggregated data"""
        indexes = self.generate_indexes()

        data = {
            'version': '2.0',
            'uid': (self.posts[0].get('author_uid') or self.posts[0].get('uid') or '') if self.posts else '',
            'total': len(self.posts),
            'updated_at': datetime.now().isoformat(),
            'posts': self.posts,
            'date_index': indexes['date_index'],
        }

        self.posts_index_file.parent.mkdir(parents=True, exist_ok=True)
        self.posts_index_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8'
        )

    @staticmethod
    def _parse_timestamp(pub_time: str) -> Optional[int]:
        """Parse pub_time string to unix timestamp"""
        if not pub_time:
            return None
        try:
            for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d']:
                try:
                    return int(datetime.strptime(pub_time[:19], fmt).timestamp())
                except ValueError:
                    continue
            return None
        except Exception:
            return None


class PostMetadata:
    """Frontmatter generator - minimal, only article_id"""

    FRONTMATTER_TEMPLATE = """---
article_id: {article_id}
---

{content}
"""

    @staticmethod
    def generate(content: str, **kwargs) -> str:
        """Generate markdown with minimal frontmatter"""
        return PostMetadata.FRONTMATTER_TEMPLATE.format(
            article_id=kwargs.get('article_id', ''),
            content=content
        )