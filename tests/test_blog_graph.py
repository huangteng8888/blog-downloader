"""
Tests for blog graph builder

对齐当前实现（blog_graph.py 已从 markdown 直读改为优先读 posts/index.json）：
  - load_from_index()     : 主路径，从 index.json 建图
  - load_from_markdown()  : 兼容入口，目录内有 index.json 则转调 load_from_index
  - _extract_keywords()   : 关键词抽取
"""
from pathlib import Path
import json
import tempfile

from src.blog_graph import BlogGraphBuilder


def _write_index(posts_dir: Path, posts: list):
    posts_dir.mkdir(parents=True, exist_ok=True)
    (posts_dir / 'index.json').write_text(
        json.dumps(
            {'version': '2.0', 'uid': '1300871220', 'total': len(posts), 'posts': posts},
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )


def test_extract_keywords():
    """Test keyword extraction"""
    builder = BlogGraphBuilder()
    text = "股票 市场 投资 股票 投资 风险 股票"
    keywords = builder._extract_keywords(text, top_n=3)
    assert len(keywords) <= 3
    assert '股票' in keywords
    # 高频词应排在前面
    assert keywords[0] == '股票'


def test_load_from_index():
    """Test building graph from index.json"""
    with tempfile.TemporaryDirectory() as tmpdir:
        posts_dir = Path(tmpdir)
        _write_index(posts_dir, [
            {
                'article_id': 'aaaabbbbccccdddd',
                'title': '第一篇文章',
                'pub_time': '2026-04-01 09:00:00',
                'author_name': '徐小明',
                'tags': [],
            },
            {
                'article_id': 'eeeeffffgggghhhh',
                'title': '第二篇文章',
                'pub_time': '2026-04-02 09:00:00',
                'author_name': '徐小明',
                'tags': [],
            },
        ])

        builder = BlogGraphBuilder()
        graph = builder.load_from_index(posts_dir / 'index.json')

        assert graph.number_of_nodes() > 0
        assert graph.number_of_edges() > 0

        types = {n[1].get('type') for n in graph.nodes(data=True)}
        assert 'post' in types
        assert 'author' in types

        # 作者枢纽边 wrote
        assert graph.has_edge('徐小明', 'aaaabbbbccccdddd')
        assert graph.edges['徐小明', 'aaaabbbbccccdddd']['relation'] == 'wrote'

        # 时间相邻边 next_post
        assert graph.has_edge('aaaabbbbccccdddd', 'eeeeffffgggghhhh')
        assert graph.edges['aaaabbbbccccdddd', 'eeeeffffgggghhhh']['relation'] == 'next_post'


def test_load_from_markdown():
    """Test fallback path: 无 index.json 时从 markdown frontmatter 建图"""
    with tempfile.TemporaryDirectory() as tmpdir:
        posts_dir = Path(tmpdir)
        (posts_dir / 'aaaabbbbccccdddd.md').write_text(
            '---\narticle_id: aaaabbbbccccdddd\n---\n\n股票 走势 中枢 走势 中枢\n',
            encoding='utf-8',
        )

        builder = BlogGraphBuilder()
        graph = builder.load_from_markdown(posts_dir)

        assert 'aaaabbbbccccdddd' in graph
        assert graph.nodes['aaaabbbbccccdddd']['type'] == 'post'
        # 关键词节点 + contains_keyword 边
        kw_edges = [
            e for e in graph.edges(data=True) if e[2].get('relation') == 'contains_keyword'
        ]
        assert len(kw_edges) > 0


def test_export_json():
    """Test Graphify-compatible JSON export"""
    with tempfile.TemporaryDirectory() as tmpdir:
        posts_dir = Path(tmpdir)
        _write_index(posts_dir, [
            {
                'article_id': 'aaaabbbbccccdddd',
                'title': '文章',
                'pub_time': '2026-04-01 09:00:00',
                'author_name': '徐小明',
                'tags': [],
            },
        ])
        builder = BlogGraphBuilder()
        builder.load_from_index(posts_dir / 'index.json')

        out = posts_dir / 'knowledge_graph.json'
        builder.export_json(out)
        data = json.loads(out.read_text(encoding='utf-8'))
        assert 'nodes' in data
        assert 'links' in data