"""
Markdown storage - minimal frontmatter
Only article_id in frontmatter, all other metadata in index.json
"""
import re
from pathlib import Path
from typing import List, Dict


class BlogStorage:
    """Minimal blog storage - frontmatter only contains article_id"""

    FRONTMATTER_TEMPLATE = """---
article_id: {article_id}
---

{content}
"""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)

    def save_post(self, post: Dict, content: str, **kwargs) -> Path:
        """Save post with minimal frontmatter"""
        filepath = self._generate_filepath(post)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        markdown = self.FRONTMATTER_TEMPLATE.format(
            article_id=post.get('article_id', ''),
            content=content
        )
        filepath.write_text(markdown, encoding='utf-8')
        return filepath

    def _generate_filepath(self, post: Dict) -> Path:
        """Generate filepath: {article_id}.md"""
        article_id = post.get('article_id', 'unknown')
        filename = f"{article_id}.md"

        return self.output_dir / filename