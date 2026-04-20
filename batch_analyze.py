#!/usr/bin/env python3
"""
批量分析博客文章并生成知识图谱
"""
import os
import json
import time
from pathlib import Path
from collections import defaultdict

POSTS_DIR = Path("/mnt/data/blog-downloader/1300871220/posts")
OUTPUT_DIR = Path("/mnt/data/blog-downloader/1300871220/blog-graph")
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
    """从 API 响应中提取文本，跳过 ThinkingBlock"""
    for block in response.content:
        # 跳过 ThinkingBlock
        if hasattr(block, 'thinking'):
            continue
        # 查找 TextBlock
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
        articles_text += f"标签: {art['tags']}\n"
        articles_text += f"内容: {art['content'][:1000]}\n"

    prompt = f"""你是一个金融博客文章分析助手。分析以下博客文章，提取主题、实体和关键信息。

{articles_text}

请以 JSON 数组格式返回分析结果，每篇文章一个对象（必须包含 idx 字段表示文章编号1-10）:
[
    {{
        "idx": 1,
        "title": "文章标题",
        "topics": ["主题1", "主题2"],
        "entities": ["实体1", "实体2"],
        "sentiment": "positive/negative/neutral",
        "key_points": ["要点1", "要点2"],
        "investment_related": true/false
    }},
    ...
]

idx 必须与输入文章的编号对应（1-10）。只返回 JSON 数组，不要有任何其他文字。"""

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
    if not CLAUDE_API_KEY:
        print("请设置 ANTHROPIC_API_KEY 环境变量")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    post_files = sorted(POSTS_DIR.glob("*.md"))
    total = len(post_files)
    print(f"找到 {total} 篇文章")

    # 断点续传：加载已有结果，按文件名去重
    output_file = OUTPUT_DIR / "analysis_results.json"
    all_results = []
    if output_file.exists():
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                all_results = json.load(f)
            # 按 source_file 去重，保留有有效映射的记录
            seen_files = set()
            clean_results = []
            for r in all_results:
                sf = r.get('_source_file', '')
                if sf and sf.endswith('.md') and sf not in seen_files:
                    seen_files.add(sf)
                    clean_results.append(r)
            all_results = clean_results
            print(f"已加载 {len(all_results)} 篇有效分析结果")
        except:
            all_results = []

    # 获取已处理的文件名集合
    processed_files = {r.get('_source_file') for r in all_results if r.get('_source_file')}

    # 计算起始批次（跳过已处理的文件）
    # 按顺序遍历，找到第一个未处理的文件索引
    start_idx = 0
    for i, pf in enumerate(post_files):
        if pf.name not in processed_files:
            start_idx = i
            break

    for i in range(start_idx, total, BATCH_SIZE):
        batch = post_files[i:i+BATCH_SIZE]
        articles = get_articles_batch(batch)

        print(f"处理批次 {i//BATCH_SIZE + 1}: 文章 {i+1}-{min(i+BATCH_SIZE, total)}", flush=True)

        result_text = analyze_batch(articles)

        try:
            if result_text.startswith('```json'):
                result_text = result_text[7:]
            if result_text.startswith('```'):
                result_text = result_text[3:]
            if result_text.endswith('```'):
                result_text = result_text[:-3]

            results = json.loads(result_text.strip())
            # 使用 LLM 返回的 idx 精确对应文章
            for r in results:
                idx = r.get('idx', 1) - 1  # idx 是 1-10，转换为 0-9
                if 0 <= idx < len(articles):
                    r['_source_file'] = articles[idx]['file']
                    r['_author'] = articles[idx]['author']
                    r['_published_at'] = articles[idx]['published_at']
                all_results.append(r)
            print(f"  成功解析 {len(results)} 篇")
        except json.JSONDecodeError as e:
            print(f"  JSON 解析错误: {e}")
            print(f"  响应前200字符: {result_text[:200]}")

        time.sleep(1)

        # 每10批保存一次，防止中断丢失数据
        if (i // BATCH_SIZE + 1) % 10 == 0:
            with open(OUTPUT_DIR / "analysis_results.json", 'w', encoding='utf-8') as f:
                json.dump(all_results, f, ensure_ascii=False, indent=2)
            print(f"  [自动保存] 已保存 {len(all_results)} 篇")

    # 保存结果
    output_file = OUTPUT_DIR / "analysis_results.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    topics = defaultdict(int)
    for r in all_results:
        for t in r.get('topics', []):
            topics[t] += 1

    print(f"\n=== 分析完成 ===")
    print(f"共分析了 {len(all_results)} 篇文章")
    print(f"结果保存到: {output_file}")
    if topics:
        print(f"\n热门主题 (Top 10):")
        for topic, count in sorted(topics.items(), key=lambda x: -x[1])[:10]:
            print(f"  {topic}: {count}")

if __name__ == "__main__":
    main()
