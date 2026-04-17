"""
Tests for blog graph builder
"""
from pathlib import Path
import tempfile
from src.blog_graph import BlogGraphBuilder


def test_parse_tags():
    """Test tag parsing"""
    builder = BlogGraphBuilder()

    # Test YAML-like format
    assert builder._parse_tags('[徐小明, 股票, 交易师]') == ['徐小明', '股票', '交易师']

    # Test empty
    assert builder._parse_tags('') == []

    # Test JSON-like (strips brackets, doesn't strip quotes)
    assert builder._parse_tags('["tag1", "tag2"]') == ['"tag1"', '"tag2"']


def test_extract_keywords():
    """Test keyword extraction"""
    builder = BlogGraphBuilder()
    text = "股票 市场 投资 股票 投资 风险 股票"
    keywords = builder._extract_keywords(text, top_n=3)
    assert len(keywords) <= 3
    assert '股票' in keywords


def test_build_graph_from_markdown():
    """Test building graph from existing markdown"""
    builder = BlogGraphBuilder()

    # Use existing test data
    test_dir = Path('/tmp/blog-downloader-test/output/1300871220/posts')
    if test_dir.exists():
        graph = builder.load_from_markdown(test_dir)
        assert graph.number_of_nodes() > 0
        assert graph.number_of_edges() > 0

        # Check node types
        nodes = list(graph.nodes(data=True))
        types = [n[1].get('type') for n in nodes]
        assert 'post' in types or 'author' in types
