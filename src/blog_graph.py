"""
Blog knowledge graph builder - Graphify extension for blog content
"""
import json
import networkx as nx
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Set
import re

class BlogGraphBuilder:
    """
    Build knowledge graph from blog Markdown files
    Graphify-compatible with additional blog-specific relations
    """
    
    def __init__(self):
        self.graph = nx.DiGraph()
        self.posts = []
    
    def load_from_markdown(self, posts_dir: Path) -> nx.DiGraph:
        """Load posts from Markdown files with frontmatter"""
        for md_file in posts_dir.rglob("*.md"):
            post = self._parse_markdown(md_file)
            if post:
                self.posts.append(post)
                self._add_post_node(post)
        
        self._add_tag_edges()
        self._add_temporal_edges()
        self._add_author_edges()
        return self.graph
    
    def _parse_markdown(self, md_file: Path) -> Dict:
        """Parse frontmatter and content from Markdown"""
        content = md_file.read_text(encoding='utf-8')
        
        fm_match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
        if not fm_match:
            return None
        
        fm = {}
        for line in fm_match.group(1).split('\n'):
            if ':' in line:
                key, val = line.split(':', 1)
                fm[key.strip()] = val.strip().strip('"').strip("'")
        
        body = content[fm_match.end():].strip()
        text = re.sub(r'#+ ', '', body)
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        
        return {
            'id': fm.get('id', md_file.stem),
            'title': fm.get('title', ''),
            'author': fm.get('author_name', ''),
            'author_uid': fm.get('author_uid', ''),
            'published_at': fm.get('published_at', ''),
            'category': fm.get('category', ''),
            'tags': self._parse_tags(fm.get('tags', '[]')),
            'word_count': int(fm.get('word_count', 0)),
            'content': text,
            'source_url': fm.get('source_url', ''),
            'filename': str(md_file)
        }
    
    def _parse_tags(self, tags_str: str) -> List[str]:
        """Parse tags from YAML-like or JSON array format"""
        tags_str = tags_str.strip()
        if tags_str.startswith("[") and tags_str.endswith("]"):
            tags_str = tags_str[1:-1]
            return [t.strip() for t in tags_str.split(",")]
        return []
    
    def _add_post_node(self, post: Dict):
        """Add post as node"""
        self.graph.add_node(
            post['id'],
            type='post',
            title=post['title'],
            author=post['author'],
            published_at=post['published_at'],
            category=post['category'],
            tags=post['tags'],
            word_count=post['word_count']
        )
        
        keywords = self._extract_keywords(post['content'])
        for kw in keywords:
            if kw not in self.graph:
                self.graph.add_node(kw, type='keyword')
            self.graph.add_edge(post['id'], kw, relation='contains_keyword', confidence='EXTRACTED')
    
    def _extract_keywords(self, text: str, top_n: int = 10) -> List[str]:
        """Extract keywords from content"""
        words = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', text)
        word_freq = {}
        for w in words:
            if len(w) >= 2:
                word_freq[w] = word_freq.get(w, 0) + 1
        return sorted(word_freq, key=word_freq.get, reverse=True)[:top_n]
    
    def _add_tag_edges(self):
        """Add edges based on shared tags"""
        posts_by_tag = {}
        for post in self.posts:
            for tag in post['tags']:
                if tag not in posts_by_tag:
                    posts_by_tag[tag] = []
                posts_by_tag[tag].append(post['id'])
        
        for tag, post_ids in posts_by_tag.items():
            for i, p1 in enumerate(post_ids):
                for p2 in post_ids[i+1:]:
                    if not self.graph.has_edge(p1, p2):
                        self.graph.add_edge(p1, p2, relation='shares_tag', confidence='EXTRACTED')
    
    def _add_temporal_edges(self):
        """Add edges between temporally adjacent posts"""
        sorted_posts = sorted(self.posts, key=lambda p: p['published_at'])
        for i, p1 in enumerate(sorted_posts[:-1]):
            p2 = sorted_posts[i + 1]
            self.graph.add_edge(p1['id'], p2['id'], relation='next_post', confidence='EXTRACTED')
    
    def _add_author_edges(self):
        """Add author hub node"""
        authors = set(p['author'] for p in self.posts if p['author'])
        for author in authors:
            if author and author not in self.graph:
                self.graph.add_node(author, type='author')
            posts = [p['id'] for p in self.posts if p['author'] == author]
            for post_id in posts:
                self.graph.add_edge(author, post_id, relation='wrote', confidence='EXTRACTED')
    
    def export_json(self, output_path: Path):
        """Export graph to Graphify-compatible JSON"""
        data = nx.node_link_data(self.graph, edges='links')
        output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
