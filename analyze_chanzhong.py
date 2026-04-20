#!/usr/bin/env python3
"""
分析缠中说禅博客文章
"""
import os
import json
import time
from pathlib import Path
from collections import defaultdict

POSTS_DIR = Path("/mnt/data/blog-downloader/1215172700/posts")
OUTPUT_DIR = Path("/mnt/data/blog-downloader/1215172700/blog-graph")
CLAUDE_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-7-20250514"
BATCH_SIZE = 10
MAX_TOKENS = 4096

def get_articles_batch(paths: list[Path]) -> list[dict]:
    articles = []
    for path in paths:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        frontmatter = {}
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                for line in parts[1].strip().split('\n'):
                    if ':' in line:
                        key, val = line.split(':', 1)
                        frontmatter[key.strip()] = val.strip().strip('"').strip("'")
        body = content
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                body = parts[2]
        articles.append({
            'file': path.name,
            'title': frontmatter.get('title', path.stem),
            'author': frontmatter.get('author_name', 'Unknown'),
            'published_at': frontmatter.get('published_at', ''),
            'tags': frontmatter.get('tags', ''),
            'content': body[:2000]
        })
    return articles

def extract_text(response) -> str:
    for block in response.content:
        if hasattr(block, 'thinking'):
            continue
        if hasattr(block, 'text'):
            return block.text
    return ""

def analyze_batch(articles: list[dict]) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    articles_text = ""
    for i, art in enumerate(articles):
        articles_text += f"\n--- 文章 {i+1} ---\n"
        articles_text += f"标题: {art['title']}\n"
        articles_text += f"作者: {art['author']}\n"
        articles_text += f"内容: {art['content'][:1000]}\n"

    prompt = f"""你是缠中说禅股票技术分析专家。分析以下博客文章，提取核心概念、理论体系和技术分析方法。

{articles_text}

请以 JSON 数组格式返回，每篇文章:
[
    {{
        "idx": 1,
        "theory": ["缠中说禅理论要点1", "理论要点2"],
        "concepts": ["概念1", "概念2"],
        "techniques": ["技术方法1", "技术方法2"],
        "key_points": ["核心观点1", "核心观点2"],
        "market_view": "对市场的看法"
    }}
]

idx 必须与输入文章编号对应(1-10)。只返回 JSON。"""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}]
        )
        return extract_text(response)
    except Exception as e:
        print(f"Error: {e}")
        return "[]"

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    post_files = sorted(POSTS_DIR.glob("*.md"))
    total = len(post_files)
    print(f"找到 {total} 篇文章")

    output_file = OUTPUT_DIR / "analysis_results.json"
    all_results = []
    if output_file.exists():
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                all_results = json.load(f)
            seen = {r.get('_source_file') for r in all_results if r.get('_source_file')}
            all_results = [r for r in all_results if r.get('_source_file') in seen]
            print(f"已加载 {len(all_results)} 篇有效结果")
        except:
            all_results = []

    processed_files = {r.get('_source_file') for r in all_results if r.get('_source_file')}
    start_idx = 0
    for i, pf in enumerate(post_files):
        if pf.name not in processed_files:
            start_idx = i
            break

    for i in range(start_idx, total, BATCH_SIZE):
        batch = post_files[i:i+BATCH_SIZE]
        articles = get_articles_batch(batch)
        print(f"批次 {i//BATCH_SIZE + 1}: 文章 {i+1}-{min(i+BATCH_SIZE, total)}", flush=True)

        result_text = analyze_batch(articles)
        try:
            if result_text.startswith('```json'): result_text = result_text[7:]
            if result_text.startswith('```'): result_text = result_text[3:]
            if result_text.endswith('```'): result_text = result_text[:-3]
            results = json.loads(result_text.strip())
            for r in results:
                idx = r.get('idx', 1) - 1
                if 0 <= idx < len(articles):
                    r['_source_file'] = articles[idx]['file']
                    r['_author'] = articles[idx]['author']
                    r['_published_at'] = articles[idx]['published_at']
                all_results.append(r)
            print(f"  成功 {len(results)} 篇")
        except json.JSONDecodeError as e:
            print(f"  JSON错误: {e}")

        time.sleep(1)
        if (i // BATCH_SIZE + 1) % 10 == 0:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(all_results, f, ensure_ascii=False, indent=2)
            print(f"  [保存] {len(all_results)} 篇")

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    theories = defaultdict(int)
    concepts = defaultdict(int)
    techniques = defaultdict(int)
    for r in all_results:
        for t in r.get('theory', []): theories[t] += 1
        for c in r.get('concepts', []): concepts[c] += 1
        for t in r.get('techniques', []): techniques[t] += 1

    print(f"\n=== 完成 ===")
    print(f"分析了 {len(all_results)}/{total} 篇")
    if theories:
        print(f"\n核心理论 (Top 10):")
        for k, v in sorted(theories.items(), key=lambda x: -x[1])[:10]:
            print(f"  {k}: {v}")
    if concepts:
        print(f"\n核心理念 (Top 10):")
        for k, v in sorted(concepts.items(), key=lambda x: -x[1])[:10]:
            print(f"  {k}: {v}")

if __name__ == "__main__":
    main()
