"""
Blog Statistics Module - Reusable statistics and date distribution analysis
for any Sina blog download.

Usage:
    from statistics import BlogStatistics

    stats = BlogStatistics('/path/to/posts_dir')
    report = stats.full_report()

    # Or use class methods directly
    BlogStatistics.print_date_distribution('/path/to/posts_dir')
"""
import json
import re
from pathlib import Path
from datetime import datetime
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple


class BlogStatistics:
    """
    博客统计分析模块 - 适用于任何博主下载的博文

    功能:
    - 日期分布统计（年/月/日）
    - 文章数量统计
    - 内容质量分析
    - 时间范围检测
    """

    MIN_CONTENT_LENGTH = 100
    MIN_TITLE_LENGTH = 3

    def __init__(self, posts_dir: str or Path):
        """
        初始化统计模块

        Args:
            posts_dir: 帖子目录路径 (e.g., /mnt/data/blog-downloader/1300871220/posts)
        """
        self.posts_dir = Path(posts_dir)
        self.articles = []  # List of parsed article metadata
        self._parse_all_articles()

    def _parse_all_articles(self):
        """解析所有Markdown文件"""
        for f in self.posts_dir.glob('*.md'):
            if self._should_skip(f):
                continue

            try:
                content = f.read_text(encoding='utf-8', errors='ignore')
                article = self._parse_markdown_file(content, f.name)
                if article:
                    self.articles.append(article)
            except Exception:
                continue

    def _should_skip(self, filepath: Path) -> bool:
        """检查是否跳过文件"""
        name = filepath.name
        skip_patterns = ['.checkpoint', 'index.json', 'index.md',
                         'MISSING_ARTICLES', 'DELETED_ARTICLES', 'STATISTICS']
        return any(p in name for p in skip_patterns)

    def _parse_markdown_file(self, content: str, filename: str) -> Optional[Dict]:
        """
        解析Markdown文件提取元数据

        Args:
            content: 文件内容
            filename: 文件名（用于调试）

        Returns:
            解析后的文章数据字典，解析失败返回None
        """
        try:
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

            # 提取关键字段
            return {
                'filename': filename,
                'id': data.get('id', ''),
                'title': data.get('title', ''),
                'published_at': data.get('published_at', ''),
                'published_date': data.get('published_at', '')[:10] if data.get('published_at') else '',
                'published_year': data.get('published_at', '')[:4] if data.get('published_at') else '',
                'published_month': data.get('published_at', '')[:7] if data.get('published_at') else '',
                'source_url': data.get('source_url', ''),
                'tags': self._parse_json_field(data.get('tags', '[]')),
                'images_count': int(data.get('images_count', 0)),
                'body_length': len(data.get('body', '')),
                'body': data.get('body', ''),
            }
        except Exception:
            return None

    def _parse_json_field(self, field: str) -> List:
        """解析JSON格式字段"""
        try:
            if field.startswith('[') or field.startswith('{'):
                return json.loads(field)
            return []
        except:
            return []

    # ==================== 日期分布统计 ====================

    def get_date_distribution(self) -> Dict:
        """
        获取完整的日期分布统计

        Returns:
            包含年/月/日分布的字典
        """
        dates = [a['published_date'] for a in self.articles if a.get('published_date')]

        if not dates:
            return {'error': 'No articles with valid dates found'}

        year_counts = Counter([d[:4] for d in dates])
        month_counts = Counter([d[:7] for d in dates])

        dates_sorted = sorted(dates)
        date_range = {
            'earliest': dates_sorted[0],
            'latest': dates_sorted[-1],
        }

        # 按年月分组统计每月详情
        monthly_stats = defaultdict(list)
        for a in self.articles:
            if a.get('published_month'):
                monthly_stats[a['published_month']].append(a)

        monthly_details = {}
        for ym, articles in sorted(monthly_stats.items()):
            monthly_details[ym] = {
                'count': len(articles),
                'avg_body_length': sum(a['body_length'] for a in articles) // len(articles) if articles else 0,
            }

        return {
            'total_articles': len(dates),
            'date_range': date_range,
            'years': dict(sorted(year_counts.items())),
            'months': dict(sorted(month_counts.items())),
            'monthly_details': monthly_details,
        }

    def get_year_distribution(self) -> Dict[str, int]:
        """获取年份分布"""
        years = [a['published_year'] for a in self.articles if a.get('published_year')]
        return dict(sorted(Counter(years).items()))

    def get_month_distribution(self) -> Dict[str, int]:
        """获取年月分布"""
        months = [a['published_month'] for a in self.articles if a.get('published_month')]
        return dict(sorted(Counter(months).items()))

    # ==================== 内容质量统计 ====================

    def get_content_quality_stats(self) -> Dict:
        """
        获取内容质量统计

        Returns:
            内容质量统计数据
        """
        valid = 0
        short_content = 0
        missing_title = 0
        missing_date = 0
        empty = 0

        for a in self.articles:
            if not a.get('body') or len(a['body']) < self.MIN_CONTENT_LENGTH:
                short_content += 1
                continue
            if not a.get('title') or len(a['title']) < self.MIN_TITLE_LENGTH:
                missing_title += 1
                continue
            if not a.get('published_date'):
                missing_date += 1
                continue
            valid += 1

        total = len(self.articles)
        body_lengths = [a['body_length'] for a in self.articles if a.get('body_length') > 0]

        return {
            'total_files': total,
            'valid_count': valid,
            'short_content': short_content,
            'missing_title': missing_title,
            'missing_date': missing_date,
            'quality_percent': (valid / total * 100) if total > 0 else 0,
            'avg_body_length': sum(body_lengths) // len(body_lengths) if body_lengths else 0,
            'min_body_length': min(body_lengths) if body_lengths else 0,
            'max_body_length': max(body_lengths) if body_lengths else 0,
        }

    # ==================== 标签统计 ====================

    def get_tag_stats(self) -> Dict:
        """
        获取标签统计

        Returns:
            标签统计数据
        """
        all_tags = []
        for a in self.articles:
            all_tags.extend(a.get('tags', []))

        tag_counts = Counter(all_tags)
        return {
            'total_tags': len(tag_counts),
            'top_tags': tag_counts.most_common(20),
            'tags_distribution': dict(tag_counts.most_common(50)),
        }

    # ==================== 完整报告 ====================

    def full_report(self) -> Dict:
        """
        生成完整统计报告

        Returns:
            完整的统计数据报告
        """
        date_dist = self.get_date_distribution()
        quality = self.get_content_quality_stats()
        tags = self.get_tag_stats()

        return {
            'generated_at': datetime.now().isoformat(),
            'posts_dir': str(self.posts_dir),
            'total_articles': len(self.articles),
            'date_distribution': date_dist,
            'content_quality': quality,
            'tag_statistics': tags,
        }

    # ==================== 输出格式化 ====================

    @staticmethod
    def print_date_distribution(posts_dir: str or Path, limit: int = None) -> str:
        """
        打印日期分布统计（用于命令行输出）

        Args:
            posts_dir: 帖子目录路径
            limit: 可选，限制显示的月份数量

        Returns:
            格式化的字符串报告
        """
        stats = BlogStatistics(posts_dir)
        dist = stats.get_date_distribution()

        if 'error' in dist:
            return dist['error']

        lines = []
        lines.append("=" * 60)
        lines.append("文章日期分布统计")
        lines.append("=" * 60)
        lines.append(f"总计文章: {dist['total_articles']}")
        lines.append(f"日期范围: {dist['date_range']['earliest']} ~ {dist['date_range']['latest']}")
        lines.append("")

        lines.append("--- 年份分布 ---")
        for year, count in dist['years'].items():
            bar = "█" * (count // 20)
            lines.append(f"{year}: {count:>5} 篇 {bar}")

        lines.append("")
        lines.append("--- 月份分布 ---")

        months = list(dist['months'].items())
        if limit:
            # 显示最近的月份
            months = months[-limit:]

        for ym, count in months:
            year, month = ym.split('-')
            bar = "█" * (count // 3)
            lines.append(f"{year}-{month}: {count:>4} 篇 {bar}")

        lines.append("=" * 60)

        return "\n".join(lines)

    def save_report(self, output_path: str or Path = None) -> Path:
        """
        保存完整报告到JSON文件

        Args:
            output_path: 输出文件路径，默认保存到 posts_dir/STATISTICS.json

        Returns:
            保存的文件路径
        """
        if output_path is None:
            output_path = self.posts_dir / 'STATISTICS.json'
        else:
            output_path = Path(output_path)

        report = self.full_report()
        output_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )

        return output_path


# ==================== 命令行接口 ====================

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='博客文章统计分析')
    parser.add_argument('posts_dir', help='帖子目录路径')
    parser.add_argument('--output', '-o', help='输出JSON文件路径')
    parser.add_argument('--limit', '-n', type=int, help='显示最近N个月份')
    parser.add_argument('--format', '-f', choices=['text', 'json'], default='text',
                        help='输出格式')

    args = parser.parse_args()

    stats = BlogStatistics(args.posts_dir)

    if args.format == 'json':
        report = stats.full_report()
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        # 文本格式输出
        print(BlogStatistics.print_date_distribution(args.posts_dir, args.limit))

        print()
        print("--- 内容质量统计 ---")
        quality = stats.get_content_quality_stats()
        print(f"总文章数: {quality['total_files']}")
        print(f"有效文章: {quality['valid_count']} ({quality['quality_percent']:.1f}%)")
        print(f"内容过短: {quality['short_content']}")
        print(f"缺少标题: {quality['missing_title']}")
        print(f"平均正文长度: {quality['avg_body_length']} 字符")

        print()
        print("--- TOP 10 标签 ---")
        tag_stats = stats.get_tag_stats()
        for tag, count in tag_stats['top_tags'][:10]:
            print(f"  {tag}: {count}")

    # 保存报告
    if args.output:
        save_path = stats.save_report(args.output)
        print(f"\n报告已保存到: {save_path}")
