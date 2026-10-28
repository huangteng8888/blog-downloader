"""
Tests for blog storage and metadata layers

对齐当前实现：
  - BlogStorage  : 扁平 {article_id}.md，frontmatter 仅含 article_id
  - BlogMetadata : 两层元数据（metadata.json + posts/index.json）
"""
from pathlib import Path
import json
import tempfile

from src.storage import BlogStorage
from src.metadata import BlogMetadata


def test_generate_filepath():
    """Test flat filepath generation: {article_id}.md"""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = BlogStorage(Path(tmpdir))
        post = {'article_id': '4d89b8340103028m'}
        filepath = storage._generate_filepath(post)
        assert filepath.parent == Path(tmpdir)
        assert filepath.name == '4d89b8340103028m.md'
        assert filepath.suffix == '.md'


def test_save_post():
    """Test post saving with minimal frontmatter"""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = BlogStorage(Path(tmpdir))
        post = {'article_id': '4d89b8340103028m', 'title': '测试文章'}
        filepath = storage.save_post(post, '正文内容')
        assert filepath.exists()
        content = filepath.read_text(encoding='utf-8')
        assert content.startswith('---')
        assert 'article_id: 4d89b8340103028m' in content
        assert '正文内容' in content
        # 极简 frontmatter：不应把 title 等字段写进 frontmatter
        assert 'title:' not in content.split('---')[1]


def test_metadata_blogger_info():
    """Test layer 1: metadata.json keeps blogger info"""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir)
        meta = BlogMetadata(out)
        meta.init_blogger('1300871220', '徐小明')

        data = json.loads((out / 'metadata.json').read_text(encoding='utf-8'))
        assert data['version'] == '2.0'
        assert data['bloggers']['1300871220']['name'] == '徐小明'


def test_metadata_posts_index():
    """Test layer 2: posts/index.json generation + dedup + date_index"""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir)
        meta = BlogMetadata(out)
        meta.init_posts_index()

        post = {
            'article_id': '4d89b8340103028m',
            'title': '徐小明：4月17日盘中即时直播',
            'pub_time': '2026-04-16 15:01:20',
            'author_uid': '1300871220',
            'author_name': '徐小明',
            'image_urls': ['a.jpg', 'b.jpg'],
            'filename': '4d89b8340103028m.md',
            'source_url': 'https://blog.sina.com.cn/s/blog_4d89b8340103028m.html',
        }
        meta.add_post(post)
        meta.add_post(post)  # 幂等：重复 ID 不应重复入库
        meta.save()

        index = json.loads((out / 'posts' / 'index.json').read_text(encoding='utf-8'))
        assert index['total'] == 1
        assert len(index['posts']) == 1
        assert index['uid'] == '1300871220'
        assert index['posts'][0]['pub_date'] == '2026-04-16'
        assert index['posts'][0]['images_count'] == 2
        assert index['posts'][0]['timestamp'] is not None
        assert index['date_index'] == {'2026-04': 1}


def test_rebuild_from_files():
    """Test recovery mode: rebuild index from .md files on disk"""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir)
        posts_dir = out / 'posts'
        posts_dir.mkdir(parents=True)

        # 新版格式（仅 article_id frontmatter）
        (posts_dir / 'aaaabbbbccccdddd.md').write_text(
            '---\narticle_id: aaaabbbbccccdddd\n---\n\n这是一篇测试正文\n',
            encoding='utf-8',
        )
        # 遗留格式（正文里带 source_url）
        (posts_dir / 'eeeeffffgggghhhh.md').write_text(
            '正文第一行标题\n\nhttps://blog.sina.com.cn/s/blog_eeeeffffgggghhhh.html\n',
            encoding='utf-8',
        )

        meta = BlogMetadata(out)
        total = meta.rebuild_from_files()
        assert total == 2

        index = json.loads((posts_dir / 'index.json').read_text(encoding='utf-8'))
        ids = {p['article_id'] for p in index['posts']}
        assert ids == {'aaaabbbbccccdddd', 'eeeeffffgggghhhh'}
        # 遗留格式应能从正文回填标题
        legacy = next(p for p in index['posts'] if p['article_id'] == 'eeeeffffgggghhhh')
        assert legacy['title'] == '正文第一行标题'


def test_rebuild_preserves_metadata():
    """回归测试：rebuild 不得静默丢失 pub_time / author_name / tags

    场景：v2 的单篇 md 只含 article_id，正文无时间与作者信息。
    若直接重建，这些字段会全部丢失（历史事故根因）。
    修复后应从现有 index.json 回填，并自动备份为 index.json.bak。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir)
        posts_dir = out / 'posts'

        meta = BlogMetadata(out)
        meta.init_posts_index()
        meta.add_post({
            'article_id': 'aaaabbbbccccdddd',
            'title': '带完整元数据的文章',
            'pub_time': '2026-04-16 15:01:20',
            'author_uid': '1300871220',
            'author_name': '徐小明',
            'image_urls': ['a.jpg'],
            'filename': 'aaaabbbbccccdddd.md',
            'source_url': 'https://blog.sina.com.cn/s/blog_aaaabbbbccccdddd.html',
        })
        meta.save()

        # md 文件只有 article_id（真实 v2 格式）
        posts_dir.mkdir(parents=True, exist_ok=True)
        (posts_dir / 'aaaabbbbccccdddd.md').write_text(
            '---\narticle_id: aaaabbbbccccdddd\n---\n\n正文内容\n',
            encoding='utf-8',
        )

        # 执行重建
        total = meta.rebuild_from_files()
        assert total == 1

        index = json.loads((posts_dir / 'index.json').read_text(encoding='utf-8'))
        entry = index['posts'][0]
        # 关键断言：元数据必须被保留，而不是被清空
        assert entry['pub_time'] == '2026-04-16 15:01:20'
        assert entry['pub_date'] == '2026-04-16'
        assert entry['author_name'] == '徐小明'
        assert entry['author_uid'] == '1300871220'
        assert entry['timestamp'] is not None

        # 必须留下备份
        assert (posts_dir / 'index.json.bak').exists()