#!/usr/bin/env python3
"""
Blog downloader runner - fetch and build knowledge graph with checkpoint/resume
"""
import argparse
import logging
import yaml
from pathlib import Path

from spider import SinaSpider
from storage import BlogStorage
from blog_graph import BlogGraphBuilder
from metadata import BlogMetadata


def load_bloggers(config_path: Path) -> list:
    with open(config_path) as f:
        return yaml.safe_load(f)['bloggers']


def main():
    parser = argparse.ArgumentParser(description='Blog downloader')
    parser.add_argument('--config', default=Path(__file__).parent.parent / 'config/bloggers.yaml')
    parser.add_argument('--uid', help='Specific blogger UID')
    parser.add_argument('--max-pages', type=int, default=100)
    parser.add_argument('--delay', type=float, default=1.5)
    parser.add_argument('--no-resume', action='store_true', help='Start fresh without resuming')
    parser.add_argument('--output', default='output', help='Output directory')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    bloggers = load_bloggers(Path(args.config))
    for blogger in bloggers:
        if args.uid and blogger['uid'] != args.uid:
            continue
        print(f"Processing: {blogger['name']} ({blogger['uid']})")

        output_dir = Path(args.output) / blogger['uid']
        posts_dir = output_dir / 'posts'

        # Initialize metadata layer
        metadata = BlogMetadata(output_dir)
        metadata.save_blogger_info(blogger)
        metadata.init_download_session(blogger['uid'])
        metadata.init_index()

        spider = SinaSpider(blogger['uid'], output_dir=str(posts_dir))
        storage = BlogStorage(posts_dir)

        total_saved = 0
        for article in spider.iter_articles(max_pages=args.max_pages, delay=args.delay, resume=not args.no_resume):
            post = {
                'id': article['id'],
                'author_uid': blogger['uid'],
                'author_name': blogger['name'],
                'published_at': article['date'],
                'source_url': article['url'],
                'title': article['title'],
                'tags': article['tags'],
            }
            filepath = storage.save_post(post, article['content'], article['images'])
            post['filename'] = str(filepath.relative_to(posts_dir))

            # Update metadata
            metadata.add_post(post)
            total_saved += 1
            print(f"  [{spider.load_checkpoint()['downloaded']}] {article['title'][:50]}")

        # Build knowledge graph
        if posts_dir.exists():
            builder = BlogGraphBuilder()
            builder.load_from_markdown(posts_dir)
            builder.export_json(output_dir / 'knowledge_graph.json')
            print(f"\nCompleted: {total_saved} articles saved")
            print(f"Knowledge graph: {builder.graph.number_of_nodes()} nodes, {builder.graph.number_of_edges()} edges")

        # Generate statistics
        stats = metadata.generate_statistics()
        print(f"Statistics: {stats['total_posts']} posts, {stats['total_images']} images")
        print(f"Date range: {stats['date_range']['earliest']} ~ {stats['date_range']['latest']}")


if __name__ == '__main__':
    main()
