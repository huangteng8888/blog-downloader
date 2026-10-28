"""
Blog knowledge graph builder - reads from index.json
"""
import json
import re
import networkx as nx
from collections import Counter
from pathlib import Path
from typing import Dict, List

# 关键词抽取的停用词（避免把高频虚词/站点噪声当成知识点）
_STOPWORDS = {
    '我们', '他们', '你们', '自己', '什么', '这样', '那样', '因为', '所以',
    '但是', '如果', '现在', '今天', '明天', '昨天', '时间', '问题', '情况',
    '可以', '应该', '已经', '还有', '就是', '不是', '这个', '那个', '一些',
    '进行', '需要', '可能', '非常', '特别', '开始', '继续', '大家', '朋友',
    '博客', '新浪', '文章', '点击', '更多', '转载', '回复', '本id', '本ID',
}


class BlogGraphBuilder:
    """
    Build knowledge graph from posts/index.json
    """

    def __init__(self):
        self.graph = nx.DiGraph()
        self.posts = []
        self._shared_tag_edges = 0

    def load_from_index(self, index_path: Path) -> nx.DiGraph:
        """Load posts from index.json"""
        with open(index_path, encoding='utf-8') as f:
            data = json.load(f)

        posts = data.get('posts', [])
        for post in posts:
            self.posts.append(post)
            self._add_post_node(post)

        self._add_temporal_edges()
        self._add_author_edges()
        # 共享标签边：v2 索引此前丢弃了 tags，该边恒为空；
        # 元数据回填后 tags 已恢复，这里补上调用，否则图谱会缺一整类关系。
        self._add_tag_edges()
        return self.graph

    def load_from_markdown(self, posts_dir: Path) -> nx.DiGraph:
        """Load posts from Markdown files (legacy, reads index.json instead)"""
        index_path = posts_dir / 'index.json'
        if index_path.exists():
            return self.load_from_index(index_path)

        # Fallback: minimal frontmatter parsing
        for md_file in posts_dir.glob("*.md"):
            post = self._parse_minimal_markdown(md_file)
            if post:
                self.posts.append(post)
                self._add_post_node(post)

        self._add_temporal_edges()
        return self.graph

    def _parse_minimal_markdown(self, md_file: Path) -> Dict:
        """Parse minimal frontmatter with only article_id"""
        content = md_file.read_text(encoding='utf-8')
        fm_match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
        if not fm_match:
            return None

        fm = {}
        for line in fm_match.group(1).split('\n'):
            if ':' in line:
                key, val = line.split(':', 1)
                fm[key.strip()] = val.strip()

        body = content[fm_match.end():].strip()
        text = re.sub(r'#+ ', '', body)
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)

        return {
            'article_id': fm.get('article_id', md_file.stem),
            'title': '',
            'content': text
        }

    def _add_post_node(self, post: Dict):
        """Add post as node"""
        article_id = post.get('article_id', '')
        title = post.get('title', '')
        tags = post.get('tags', []) or []
        pub_time = post.get('pub_time', '')
        content = post.get('content', '')

        self.graph.add_node(
            article_id,
            type='post',
            title=title,
            pub_time=pub_time,
            tags=tags
        )

        # Extract keywords from content
        if content:
            keywords = self._extract_keywords(content)
            for kw in keywords:
                if kw not in self.graph:
                    self.graph.add_node(kw, type='keyword')
                self.graph.add_edge(article_id, kw, relation='contains_keyword', confidence='EXTRACTED')

    def _extract_keywords(self, text: str, top_n: int = 10) -> List[str]:
        """Extract keywords from content.

        ⚠️ 原实现用 `re.findall(r'[\\u4e00-\\u9fff]+|[a-zA-Z]+')` 取词，
        中文会被整段连读成一个"词"（如「点前不要怕短调」「上周大盘明显是利用」），
        导致关键词节点变成整句、图谱体积失控（v1 图谱 2.15 GB 的主因之一）。

        改为：优先用 jieba 分词 + 停用词/长度过滤；无 jieba 时回退为正则
        但强制限制词长，避免再产生整句"关键词"。
        """
        try:
            import jieba
            import jieba.analyse
            words = jieba.analyse.extract_tags(text, topK=top_n * 3)
        except Exception:
            words = re.findall(r'[\u4e00-\u9fff]{2,6}|[a-zA-Z]{2,12}', text)

        out = []
        for w in words:
            w = w.strip()
            # 过滤：长度、纯数字、整句噪声
            if not w or len(w) < 2 or len(w) > 12:
                continue
            if w.isdigit():
                continue
            if w in _STOPWORDS:
                continue
            if w not in out:
                out.append(w)
            if len(out) >= top_n:
                break
        return out

    def _add_tag_edges(self, max_tag_clique: int = 20):
        """Add tag structure to the graph.

        ⚠️ 原实现是"共享同一标签的文章两两连边"，对高频标签会退化成完全图：
        「股票」这类标签覆盖 1.2 万篇文章 → C(12241,2) ≈ 7,500 万条边，
        图谱直接爆炸（v1 遗留图谱 21 万条边、2.15 GB 体积即源于此类做法）。

        改为：
          1. 始终为每个标签建 tag 枢纽节点，并连 post --has_tag--> tag（O(n) 边）；
          2. 仅对"低频标签"（出现篇数 <= max_tag_clique）额外保留
             post --shares_tag--> post 的语义边，保证标签确实相似的文章仍直接相连。
        """
        counts = Counter()
        posts_by_tag = {}
        for post in self.posts:
            for tag in post.get('tags', []) or []:
                counts[tag] += 1
                posts_by_tag.setdefault(tag, []).append(post.get('article_id'))

        shared = 0
        for tag, article_ids in posts_by_tag.items():
            # 1. 标签枢纽节点 + has_tag 边
            tag_node = f'tag::{tag}'
            if tag_node not in self.graph:
                self.graph.add_node(tag_node, type='tag', name=tag)
            for aid in article_ids:
                if aid:
                    self.graph.add_edge(aid, tag_node,
                                        relation='has_tag', confidence='EXTRACTED')

            # 2. 低频标签才建两两 shares_tag 边
            if len(article_ids) <= max_tag_clique:
                for i, p1 in enumerate(article_ids):
                    for p2 in article_ids[i + 1:]:
                        if p1 and p2 and not self.graph.has_edge(p1, p2):
                            self.graph.add_edge(p1, p2,
                                                relation='shares_tag', confidence='EXTRACTED')
                            shared += 1
        self._shared_tag_edges = shared

    def _add_temporal_edges(self):
        """Add edges between temporally adjacent posts"""
        sorted_posts = sorted(
            [p for p in self.posts if p.get('pub_time')],
            key=lambda p: p.get('pub_time', '')
        )
        for i, p1 in enumerate(sorted_posts[:-1]):
            p2 = sorted_posts[i + 1]
            self.graph.add_edge(p1.get('article_id'), p2.get('article_id'),
                              relation='next_post', confidence='EXTRACTED')

    def _add_author_edges(self):
        """Add author hub node"""
        authors = {}
        for post in self.posts:
            author = post.get('author_name', '')
            if author:
                if author not in authors:
                    authors[author] = []
                authors[author].append(post.get('article_id'))

        for author, article_ids in authors.items():
            if author and author not in self.graph:
                self.graph.add_node(author, type='author')
            for article_id in article_ids:
                self.graph.add_edge(author, article_id, relation='wrote', confidence='EXTRACTED')

    def export_json(self, output_path: Path):
        """Export graph to Graphify-compatible JSON"""
        data = nx.node_link_data(self.graph, edges='links')
        output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))