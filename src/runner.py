#!/usr/bin/env python3
"""
Blog downloader runner - simplified with logging
"""
import argparse
import logging
import re
from pathlib import Path

from spider import SinaSpider
from storage import BlogStorage
from metadata import BlogMetadata

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
logger = logging.getLogger(__name__)


def load_existing_ids(posts_dir: Path) -> set:
    """Load article_ids from existing posts for incremental sync"""
    existing_ids = set()
    if posts_dir.exists():
        for md_file in posts_dir.glob('*.md'):
            # Extract article_id from frontmatter (supports both article_id: and id:)
            content = md_file.read_text(encoding='utf-8')
            match = re.search(r'article_id:\s*(\S+)', content)
            if not match:
                match = re.search(r'^id:\s*(\S+)$', content, re.MULTILINE)
            if match:
                existing_ids.add(match.group(1))
    logger.info(f"Found {len(existing_ids)} existing articles")
    return existing_ids


def main():
    parser = argparse.ArgumentParser(description='Blog downloader')
    parser.add_argument('--uid', required=True)
    parser.add_argument('--blogger-name', default='徐小明')
    parser.add_argument('--max-pages', type=int, default=1309)
    parser.add_argument('--delay', type=float, default=1.0)
    parser.add_argument('--output', default='output')
    parser.add_argument('--rebuild-index', action='store_true',
                        help='Rebuild index.json from all .md files on disk (recovery mode)')
    args = parser.parse_args()

    blogger = {'uid': args.uid, 'name': args.blogger_name}

    output_dir = Path(args.output) / args.uid
    posts_dir = output_dir / 'posts'
    posts_dir.mkdir(parents=True, exist_ok=True)

    # 初始化博主信息
    meta = BlogMetadata(output_dir)
    meta.init_blogger(uid=blogger['uid'], name=blogger['name'])

    # Recovery mode: rebuild index from all .md files
    if args.rebuild_index:
        logger.info("Rebuilding index from .md files on disk...")
        total = meta.rebuild_from_files()
        logger.info(f"Index rebuilt: {total} posts indexed from disk")
        stats = meta.generate_statistics()
        logger.info(f"Total: {stats['total_posts']} posts")
        logger.info(f"Date range: {stats['date_range']}")
        return

    spider = SinaSpider(args.uid, output_dir=str(posts_dir))
    storage = BlogStorage(posts_dir)
    meta.init_posts_index()

    # 增量同步：加载已存在的文章ID
    existing_ids = load_existing_ids(posts_dir)

    # 开始下载
    logger.info(f"Starting download for {blogger['name']} ({args.uid})")

    total = 0
    new_count = 0
    seen_ids = set()
    for article in spider.iter_articles(
        max_pages=args.max_pages,
        delay=args.delay,
        resume=True,
        existing_ids=existing_ids,
        stop_on_existing_pages=3
    ):
        article_id = article['article_id']

        # Skip duplicates (same article_id across pages)
        if article_id in seen_ids:
            continue
        seen_ids.add(article_id)

        post = {
            'article_id': article_id,
            'author_uid': blogger['uid'],
            'author_name': blogger['name'],
            'pub_time': article['pub_time'],
            'source_url': article['source_url'],
            'title': article['title'],
            'image_urls': article.get('image_urls', []),
        }
        filepath = storage.save_post(post, article['content'])
        post['filename'] = str(filepath.relative_to(posts_dir))

        # 更新元数据索引
        meta.add_post(post)
        total += 1
        new_count += 1

        logger.info(f"[{total}] {article_id} - {article['title'][:40]}")

    # 批量写入索引（只写一次）
    meta.save()

    # 完成
    stats = meta.generate_statistics()
    logger.info(f"\n=== Download Complete ===")
    logger.info(f"New posts: {new_count}")
    logger.info(f"Total: {stats['total_posts']} posts")
    logger.info(f"Images: {stats['total_images']}")
    logger.info(f"Date range: {stats['date_range']}")
    logger.info(f"Posts by month: {stats['posts_by_month']}")

    # 构建知识图谱
    from blog_graph import BlogGraphBuilder
    builder = BlogGraphBuilder()
    builder.load_from_markdown(posts_dir)
    builder.export_json(output_dir / 'knowledge_graph.json')
    logger.info(f"Knowledge graph: {builder.graph.number_of_nodes()} nodes, {builder.graph.number_of_edges()} edges")


if __name__ == '__main__':
    main()