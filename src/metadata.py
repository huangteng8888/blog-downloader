"""
Blog metadata layer - comprehensive metadata management for AI knowledge base
"""
import json
import re
import urllib.request
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
            'total_images': 0,
            'failed_urls': [],
        }
        self._save_download()

    def update_progress(self, page: int, article_index: int, downloaded: int,
                       images: int = 0):
        """Update download progress"""
        if hasattr(self, 'download_info'):
            self.download_info['last_page'] = page
            self.download_info['last_article_index'] = article_index
            self.download_info['total_downloaded'] = downloaded
            self.download_info['pages_completed'] = page
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
            'tags': post.get('tags', []),
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

        tags = [t for p in self.posts_index for t in p.get('tags', [])]
        dates = [p['published_date'] for p in self.posts_index if p.get('published_date')]

        tag_counts = Counter(tags).most_common(20)

        return {
            'total_posts': len(self.posts_index),
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


class SinaArticleVerifier:
    """Verify downloaded articles against Sina's article list"""

    def __init__(self, uid: str):
        self.uid = uid
        self.article_list_url = f"https://blog.sina.com.cn/s/articlelist_{uid}_0_1.html"

    def get_sina_article_count(self, max_pages_to_check: int = 300) -> Dict:
        """
        Fetch article count from Sina's article list.
        Dynamically detects actual last page with content.
        """
        try:
            # Get first page info
            req = urllib.request.Request(
                self.article_list_url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            )
            html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', errors='ignore')

            # Extract stated total pages from pagination
            page_match = re.search(r'共(\d+)页', html)
            stated_pages = int(page_match.group(1)) if page_match else 0

            # Count articles on first page
            articles = re.findall(r'<a[^>]+href="([^"]+blog_[^"]+)"[^>]*>([^<]+)</a>', html)
            first_page_count = len([h for h, t in articles if '/s/blog_' in h and t.strip()])

            # Dynamically find last page with content (binary search for efficiency)
            last_page = self._find_last_page(max_pages_to_check)

            # Calculate actual total (pages 1 to last-1 assumed 50 articles each, last page actual count)
            if last_page > 1:
                last_page_count = self._get_page_article_count(last_page)
                estimated_total = (last_page - 1) * 50 + last_page_count
            else:
                estimated_total = first_page_count

            return {
                'stated_pages': stated_pages,
                'actual_last_page': last_page,
                'articles_first_page': first_page_count,
                'estimated_total': estimated_total,
                'source_url': self.article_list_url,
            }
        except Exception as e:
            return {'error': str(e)}

    def _get_page_article_count(self, page: int) -> int:
        """Get article count for a specific page"""
        try:
            url = f"https://blog.sina.com.cn/s/articlelist_{self.uid}_0_{page}.html"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', errors='ignore')

            articles = re.findall(r'<a[^>]+href="([^"]+blog_[^"]+)"[^>]*>([^<]+)</a>', html)
            return len([h for h, t in articles if '/s/blog_' in h and t.strip()])
        except:
            return 0

    def _find_last_page(self, max_pages: int) -> int:
        """Binary search to find last page with content"""
        low, high = 1, max_pages

        while low < high:
            mid = (low + high + 1) // 2
            count = self._get_page_article_count(mid)
            if count > 0:
                low = mid
            else:
                high = mid - 1

        # Verify low page has content
        if self._get_page_article_count(low) == 0:
            return 0
        return low

    def verify_download(self, downloaded_count: int, index_count: int) -> Dict:
        """Compare downloaded articles against Sina's count"""
        sina_info = self.get_sina_article_count()

        if 'error' in sina_info:
            return {'status': 'error', 'message': sina_info['error']}

        sina_total = sina_info['estimated_total']
        diff = downloaded_count - sina_total if downloaded_count > sina_total else sina_total - downloaded_count
        completeness = (min(downloaded_count, sina_total) / max(downloaded_count, sina_total) * 100) if sina_total > 0 else 0

        return {
            'status': 'ok' if completeness >= 99 else 'incomplete',
            'sina_stated_pages': sina_info.get('stated_pages', 0),
            'sina_actual_last_page': sina_info.get('actual_last_page', 0),
            'sina_articles_first_page': sina_info.get('articles_first_page', 0),
            'sina_estimated_total': sina_total,
            'downloaded_count': downloaded_count,
            'index_count': index_count,
            'difference': diff,
            'completeness_percent': round(completeness, 1),
            'message': self._get_message(completeness, diff),
        }

    def _get_message(self, completeness: float, diff: int) -> str:
        """Generate human-readable status message"""
        if completeness >= 100:
            return f"下载完整 (与Sina估算一致)"
        elif completeness >= 99:
            return f"下载基本完整 (差异{diff}篇，可能为Sina估算误差)"
        elif completeness >= 90:
            return f"下载不完整 (缺失约{diff}篇)"
        else:
            return f"下载严重不完整 (缺失约{diff}篇，建议重新下载)"


class BlogIntegrityChecker:
    """
    博文完整性检查器
    - 数量完整: 所有Sina文章都已下载
    - 内容完整: 每篇文章有实际内容(非空、无占位符)
    """

    MIN_CONTENT_LENGTH = 100  # 最小内容长度(字符)
    MIN_TITLE_LENGTH = 3     # 最小标题长度

    def __init__(self, uid: str, posts_dir: Path):
        self.uid = uid
        self.posts_dir = Path(posts_dir)
        self.verifier = SinaArticleVerifier(uid)

    def check_quantity(self) -> Dict:
        """检查数量完整性 - 使用SinaArticleVerifier获取准确总数"""
        # 使用SinaArticleVerifier获取准确的估算总数
        sina_info = self.verifier.get_sina_article_count()
        sina_total = sina_info.get('estimated_total', 0)

        downloaded = self._get_downloaded_urls()
        downloaded_total = len(downloaded)

        # 获取Sina文章列表用于URL匹配
        sina_articles = self._get_sina_articles()
        sina_urls = set(sina_articles.keys())
        downloaded_urls = set(downloaded.keys())

        # 找出缺失的URL（在Sina列表中但未下载）
        missing_urls = sina_urls - downloaded_urls

        # 找出额外文件（已下载但不在Sina列表中，可能是已删除文章）
        extra_urls = downloaded_urls - sina_urls

        # 计算完整度：已下载的Sina文章数 / Sina总文章数
        matched_sina = len(sina_urls & downloaded_urls)
        completeness = (matched_sina / sina_total * 100) if sina_total > 0 else 0

        return {
            'sina_estimated_total': sina_total,
            'sina_listed_total': len(sina_urls),
            'downloaded_total': downloaded_total,
            'matched_sina_articles': matched_sina,
            'missing_from_sina_list': len(missing_urls),
            'extra_files_not_in_list': len(extra_urls),
            'completeness_percent': completeness,
            'missing_urls': list(missing_urls)[:50],
            'extra_urls_sample': list(extra_urls)[:10],
            # 状态: 如果Sina列表中的文章全部下载，则为ok
            # 额外文件可能是已删除文章，不影响完整性判断
            'status': 'ok' if len(missing_urls) == 0 else 'incomplete'
        }

    def _parse_markdown_file(self, filepath: Path) -> Optional[Dict]:
        """解析Markdown文件,返回结构化数据"""
        try:
            content = filepath.read_text(encoding='utf-8', errors='ignore')
            parts = content.split('---\n')
            if len(parts) < 3:
                return None

            frontmatter = parts[1]
            body_with_title = '\n'.join(parts[2:])

            # 解析frontmatter
            data = {}
            for line in frontmatter.split('\n'):
                if ':' in line:
                    key, val = line.split(':', 1)
                    data[key.strip()] = val.strip()

            # 解析正文标题
            title_match = re.search(r'^#\s+(.+?)\s*$', body_with_title, re.MULTILINE)
            data['title'] = title_match.group(1).strip() if title_match else ''

            # 解析正文(去掉标题行)
            body_lines = body_with_title.split('\n')
            data['body'] = '\n'.join(body_lines[1:]).strip() if len(body_lines) > 1 else ''

            data['source_url'] = data.get('source_url', '')
            data['published_at'] = data.get('published_at', '')

            return data
        except:
            return None

    def check_content_quality(self) -> Dict:
        """检查内容质量"""
        issues = []
        empty_count = 0
        short_count = 0
        no_date_count = 0
        no_title_count = 0
        valid_count = 0

        for f in self.posts_dir.glob('*.md'):
            if '.checkpoint' in f.name or 'index' in f.name:
                continue

            parsed = self._parse_markdown_file(f)
            if not parsed:
                issues.append({'file': f.name, 'issue': '文件解析失败'})
                continue

            title = parsed.get('title', '')
            body = parsed.get('body', '')
            date = parsed.get('published_at', '')
            url = parsed.get('source_url', '')

            # 检查标题
            if not title or len(title) < self.MIN_TITLE_LENGTH:
                issues.append({'file': f.name, 'issue': f'标题无效({title[:20]})'})
                no_title_count += 1
                continue

            # 检查日期
            if not date:
                issues.append({'file': f.name, 'issue': '缺少发布日期', 'url': url})
                no_date_count += 1
                continue

            # 检查正文长度
            if not body or len(body) < self.MIN_CONTENT_LENGTH:
                issues.append({'file': f.name, 'issue': f'内容过短({len(body)}字符)', 'url': url})
                short_count += 1
                continue

            # 检查是否为占位符
            if self._is_placeholder(body):
                issues.append({'file': f.name, 'issue': '内容为占位符'})
                short_count += 1
                continue

            valid_count += 1

        total = valid_count + short_count + no_date_count + no_title_count

        return {
            'total_files': total,
            'valid_count': valid_count,
            'empty_content': empty_count,
            'short_content': short_count,
            'missing_date': no_date_count,
            'missing_title': no_title_count,
            'issues': issues[:50],
            'quality_percent': valid_count / total * 100 if total else 0,
            'status': 'ok' if valid_count == total else 'degraded'
        }

    def check_file_integrity(self) -> Dict:
        """检查文件完整性"""
        issues = []
        valid_count = 0
        corrupted_count = 0

        for f in self.posts_dir.glob('*.md'):
            if '.checkpoint' in f.name or 'index' in f.name:
                continue

            try:
                content = f.read_text(encoding='utf-8', errors='ignore')

                # 检查frontmatter必需字段
                required_fields = ['id:', 'author_uid:', 'author_name:', 'published_at:', 'source_url:']
                missing_fields = [field for field in required_fields if field not in content]

                if missing_fields:
                    issues.append({'file': f.name, 'issue': f'缺少{missing_fields}'})
                    corrupted_count += 1
                    continue

                # 检查JSON格式(tags, images)
                if 'tags:' in content:
                    tags_match = re.search(r'tags:\s*(.+)', content)
                    if tags_match:
                        tags_str = tags_match.group(1).strip()
                        if not (tags_str.startswith('[') or tags_str == '[]'):
                            issues.append({'file': f.name, 'issue': 'tags格式错误'})
                            corrupted_count += 1
                            continue

                valid_count += 1

            except Exception as e:
                issues.append({'file': f.name, 'issue': f'读取错误'})
                corrupted_count += 1

        total = valid_count + corrupted_count

        return {
            'total_files': total,
            'valid_count': valid_count,
            'corrupted_count': corrupted_count,
            'issues': issues[:30],
            'integrity_percent': valid_count / total * 100 if total else 0,
            'status': 'ok' if corrupted_count == 0 else 'corrupted'
        }

    def full_report(self) -> Dict:
        """生成完整完整性报告"""
        quantity = self.check_quantity()
        quality = self.check_content_quality()
        integrity = self.check_file_integrity()

        # 计算总体状态
        all_ok = (quantity['status'] == 'ok' and
                  quality['status'] == 'ok' and
                  integrity['status'] == 'ok')

        return {
            'timestamp': datetime.now().isoformat(),
            'uid': self.uid,
            'posts_dir': str(self.posts_dir),
            'quantity': quantity,
            'content_quality': quality,
            'file_integrity': integrity,
            'overall_status': 'ok' if all_ok else 'issues_found',
            'recommendations': self._generate_recommendations(quantity, quality, integrity)
        }

    def _get_sina_articles(self) -> Dict:
        """获取Sina文章列表（使用动态页面检测）"""
        articles = {}
        max_pages = 500

        # 动态检测实际最后一页
        last_page = self._find_sina_last_page(max_pages)

        for page in range(1, last_page + 1):
            url = f"https://blog.sina.com.cn/s/articlelist_{self.uid}_0_{page}.html"
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', errors='ignore')

                links = re.findall(r'href="([^"]+)"[^>]*>([^<]+)</a>', html)
                for href, title in links:
                    if '/s/blog_' in href and title.strip():
                        full_url = 'https:' + href if href.startswith('//') else href
                        if full_url not in articles:
                            articles[full_url] = title.strip()
            except:
                continue

        return articles

    def _find_sina_last_page(self, max_pages: int = 500) -> int:
        """Binary search to find last page with content"""
        def get_page_article_count(page: int) -> int:
            url = f"https://blog.sina.com.cn/s/articlelist_{self.uid}_0_{page}.html"
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', errors='ignore')
                links = re.findall(r'href="([^"]+)"[^>]*>([^<]+)</a>', html)
                return len([h for h, t in links if '/s/blog_' in h and t.strip()])
            except:
                return 0

        low, high = 1, max_pages
        while low < high:
            mid = (low + high + 1) // 2
            if get_page_article_count(mid) > 0:
                low = mid
            else:
                high = mid - 1

        return low if get_page_article_count(low) > 0 else 0

    def _get_downloaded_urls(self) -> Dict:
        """获取已下载文章的URL"""
        urls = {}
        for f in self.posts_dir.glob('*.md'):
            if '.checkpoint' in f.name or 'index' in f.name:
                continue
            try:
                content = f.read_text(encoding='utf-8', errors='ignore')
                url_match = re.search(r'source_url:\s*(https?://[^\s]+)', content)
                if url_match:
                    urls[url_match.group(1).strip()] = f.name
            except:
                pass
        return urls

    def _is_placeholder(self, content: str) -> bool:
        """检查内容是否为占位符"""
        placeholder_patterns = [
            r'^#+\s*$',  # 只有标题
            r'^无内容$',
            r'^待补充$',
            r'^内容加载中',
            r'^请稍后',
        ]
        for pattern in placeholder_patterns:
            if re.match(pattern, content.strip()):
                return True
        return False

    def _generate_recommendations(self, quantity: Dict, quality: Dict,
                                  integrity: Dict) -> List[str]:
        """生成修复建议"""
        recommendations = []

        # 数量检查
        if quantity['status'] != 'ok':
            missing = quantity.get('missing_from_sina_list', 0)
            extra = quantity.get('extra_files_not_in_list', 0)
            completeness = quantity.get('completeness_percent', 0)

            if missing > 0:
                recommendations.append(
                    f"数量不完整: 缺失 {missing} 篇Sina列表文章，建议重新下载缺失部分"
                )
            if extra > 0:
                recommendations.append(
                    f"额外文件: {extra} 个文件不在Sina列表中（可能为已删除文章）"
                )
            if completeness < 99:
                recommendations.append(
                    f"完整度不足: 当前 {completeness:.1f}%，建议检查网络下载是否有丢失"
                )

        # 内容质量检查
        if quality['status'] != 'ok':
            if quality.get('short_content', 0) > 0:
                recommendations.append(
                    f"内容质量问题: {quality['short_content']} 篇文章内容过短（<100字符）"
                )
            if quality.get('missing_title', 0) > 0:
                recommendations.append(
                    f"内容质量问题: {quality['missing_title']} 篇文章缺少有效标题"
                )

        # 文件完整性检查
        if integrity['status'] != 'ok':
            recommendations.append(
                f"文件损坏: {integrity.get('corrupted_count', 0)} 个文件损坏"
            )

        if not recommendations:
            recommendations.append("所有检查通过，博文下载完整")

        return recommendations

        return recommendations


def verify_blog_download(output_dir: str, uid: str) -> Dict:
    """Convenience function to verify a blog download"""
    output_path = Path(output_dir)
    posts_dir = output_path / uid / 'posts'

    # Count downloaded files
    downloaded_count = len(list(posts_dir.glob('*.md'))) if posts_dir.exists() else 0

    # Count in index
    index_file = posts_dir / 'index.json'
    index_count = 0
    if index_file.exists():
        try:
            index_data = json.loads(index_file.read_text())
            index_count = index_data.get('total', 0)
        except:
            pass

    verifier = SinaArticleVerifier(uid)
    return verifier.verify_download(downloaded_count, index_count)
