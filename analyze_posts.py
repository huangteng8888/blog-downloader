#!/usr/bin/env python3
"""
分析博客文章并生成知识图谱
使用 Claude API 提取实体和关系
"""
import os
import json
import hashlib
from pathlib import Path
from typing import Optional

# 配置
POSTS_DIR = Path("/mnt/data/blog-downloader/1300871220/posts")
OUTPUT_DIR = Path("/mnt/data/blog-downloader/1300871220/blog-graph")
CLAUDE_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 1024

def get_article_preview(path: Path, max_chars=2000) -> str:
    """提取文章前 N 个字符用于分析"""
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    # 跳过 frontmatter
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            content = parts[2]
    return content[:max_chars]

def analyze_with_claude(title: str, content: str, author: str) -> Optional[dict]:
    """使用 Claude API 分析单篇文章"""
    import anthropic

    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    prompt = f"""分析以下博客文章，提取实体和关系。

文章标题: {title}
作者: {author}

内容预览:
{content[:1500]}

请以 JSON 格式返回分析结果:
{{
    "topics": ["主题1", "主题2", ...],
    "entities": ["提到的实体1", "实体2", ...],
    "sentiment": "positive/negative/neutral",
    "key_points": ["要点1", "要点2", ...],
    "related_articles": ["相关文章标题或主题"]
}}

只返回 JSON，不要有其他文字。"""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}]
        )
        result_text = response.content[0].text.strip()
        # 尝试解析 JSON
        if result_text.startswith('```json'):
            result_text = result_text[7:]
        if result_text.startswith('```'):
            result_text = result_text[3:]
        if result_text.endswith('```'):
            result_text = result_text[:-3]
        return json.loads(result_text.strip())
    except Exception as e:
        print(f"Error analyzing {title}: {e}")
        return None

def main():
    if not CLAUDE_API_KEY:
        print("请设置 ANTHROPIC_API_KEY 环境变量")
        print("export ANTHROPIC_API_KEY='your-api-key'")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 获取所有文章
    post_files = list(POSTS_DIR.glob("*.md"))
    print(f"找到 {len(post_files)} 篇文章")

    all_results = []
    for i, post_file in enumerate(post_files[:50]):  # 先处理前50篇测试
        if i % 10 == 0:
            print(f"进度: {i}/{min(50, len(post_files))}")

        # 解析 frontmatter
        with open(post_file, 'r', encoding='utf-8') as f:
            content = f.read()

        frontmatter = {}
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                for line in parts[1].strip().split('\n'):
                    if ':' in line:
                        key, val = line.split(':', 1)
                        frontmatter[key.strip()] = val.strip().strip('"').strip("'")

        title = frontmatter.get('title', post_file.stem)
        author = frontmatter.get('author_name', 'Unknown')

        # 分析文章
        article_content = get_article_preview(post_file)
        result = analyze_with_claude(title, article_content, author)

        if result:
            result['_source_file'] = str(post_file)
            result['_title'] = title
            all_results.append(result)

    # 保存结果
    output_file = OUTPUT_DIR / "analysis_results.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"\n分析完成！结果保存到: {output_file}")
    print(f"共分析了 {len(all_results)} 篇文章")

if __name__ == "__main__":
    main()
