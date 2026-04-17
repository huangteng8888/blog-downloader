"""
Markdown storage with Graphify-compatible frontmatter
"""
import json
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

class BlogStorage:
    """Graphify-compatible blog storage"""
    
    FRONTMATTER_TEMPLATE = """---
id: {id}
author_uid: {author_uid}
author_name: {author_name}
published_at: {published_at}
fetched_at: {fetched_at}
type: {post_type}
category: {category}
tags: {tags}
word_count: {word_count}
source_url: {source_url}
likes: {likes}
comments: {comments}
images_count: {images_count}
images: {images}
---

# {title}

{content}
"""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
    
    def save_post(self, post: Dict, content: str, images: List[str]) -> Path:
        """Save post as Graphify-compatible Markdown"""
        filepath = self._generate_filepath(post)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        markdown = self.FRONTMATTER_TEMPLATE.format(
            id=post['id'],
            author_uid=post['author_uid'],
            author_name=post['author_name'],
            published_at=post['published_at'],
            fetched_at=datetime.now().isoformat(),
            post_type=post.get('type', 'article'),
            category=post.get('category', ''),
            tags=json.dumps(post.get('tags', []), ensure_ascii=False),
            word_count=len(content),
            source_url=post['source_url'],
            likes=post.get('likes', 0),
            comments=post.get('comments', 0),
            images_count=len(images),
            images=json.dumps(images, ensure_ascii=False),
            title=post['title'],
            content=content
        )
        filepath.write_text(markdown, encoding='utf-8')
        return filepath
    
    def _generate_filepath(self, post: Dict) -> Path:
        """Generate semantic filepath: {date}/{timestamp}_{id}_{title}.md"""
        date = post['published_at'][:10]  # YYYY-MM-DD
        timestamp = int(datetime.fromisoformat(post['published_at'].replace('+08:00', '')).timestamp())
        safe_title = re.sub(r'[^\w\s\u4e00-\u9fff]', '', post['title'])[:30]
        filename = f"{timestamp}_{post['id']}_{safe_title}.md"
        return self.output_dir / date / filename
    
    def save_index(self, blogger: Dict, posts: List[Dict]):
        """Save index.json for fast retrieval"""
        posts_index = [{
            'id': p['id'],
            'title': p['title'],
            'published_at': p['published_at'],
            'category': p.get('category', ''),
            'word_count': p.get('word_count', 0),
            'filename': str(self._generate_filepath(p).relative_to(self.output_dir))
        } for p in posts]
        
        index = {'blogger': blogger, 'posts': posts_index, 'total': len(posts)}
        (self.output_dir / 'index.json').write_text(
            json.dumps(index, indent=2, ensure_ascii=False), encoding='utf-8')
