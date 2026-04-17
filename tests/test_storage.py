"""
Tests for blog storage
"""
from pathlib import Path
import json
import tempfile
from src.storage import BlogStorage


def test_generate_filepath():
    """Test semantic filepath generation"""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = BlogStorage(Path(tmpdir))
        post = {
            'id': 'abc123',
            'title': '测试文章',
            'published_at': '2024-12-21T10:30:00+08:00',
        }
        filepath = storage._generate_filepath(post)
        assert filepath.parent.name == '2024-12-21'
        assert 'abc123' in filepath.name
        assert filepath.suffix == '.md'


def test_save_post():
    """Test post saving"""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = BlogStorage(Path(tmpdir))
        post = {
            'id': 'test123',
            'author_uid': '1300871220',
            'author_name': '徐小明',
            'published_at': '2024-12-21T10:30:00+08:00',
            'source_url': 'https://blog.sina.com.cn/s/blog_test123.html',
            'title': '测试文章',
            'tags': ['股票', '分析'],
        }
        filepath = storage.save_post(post, '正文内容', ['img1.jpg'])
        assert filepath.exists()
        content = filepath.read_text()
        assert '徐小明' in content
        assert '测试文章' in content
        assert 'Graphify' in content or '---' in content


def test_save_index():
    """Test index.json generation"""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = BlogStorage(Path(tmpdir))
        blogger = {'uid': '1300871220', 'name': '徐小明'}
        posts = [
            {'id': 'p1', 'title': '文章1', 'published_at': '2024-12-21', 'word_count': 100},
            {'id': 'p2', 'title': '文章2', 'published_at': '2024-12-22', 'word_count': 200},
        ]
        storage.save_index(blogger, posts)
        index_path = storage.output_dir / 'index.json'
        assert index_path.exists()
        index = json.loads(index_path.read_text())
        assert index['total'] == 2
        assert len(index['posts']) == 2
