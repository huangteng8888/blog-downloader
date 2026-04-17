# Graph Report - /tmp/blog-downloader  (2026-04-15)

## Corpus Check
- 5 files · ~1,197 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 52 nodes · 68 edges · 13 communities detected
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 7 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]

## God Nodes (most connected - your core abstractions)
1. `BlogGraphBuilder` - 13 edges
2. `main()` - 9 edges
3. `SinaSpider` - 9 edges
4. `BlogStorage` - 7 edges
5. `load_bloggers()` - 2 edges
6. `Blog Downloader - AI Knowledge Base Optimized Graphify-compatible blog scraping` - 1 edges
7. `Markdown storage with Graphify-compatible frontmatter` - 1 edges
8. `Graphify-compatible blog storage` - 1 edges
9. `Save post as Graphify-compatible Markdown` - 1 edges
10. `Generate semantic filepath: {date}/{timestamp}_{id}_{title}.md` - 1 edges

## Surprising Connections (you probably didn't know these)
- `main()` --calls--> `BlogStorage`  [INFERRED]
  /tmp/blog-downloader/src/runner.py → /tmp/blog-downloader/src/storage.py
- `main()` --calls--> `SinaSpider`  [INFERRED]
  /tmp/blog-downloader/src/runner.py → /tmp/blog-downloader/src/spider.py
- `main()` --calls--> `BlogGraphBuilder`  [INFERRED]
  /tmp/blog-downloader/src/runner.py → /tmp/blog-downloader/src/blog_graph.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.22
Nodes (6): BlogStorage, Markdown storage with Graphify-compatible frontmatter, Graphify-compatible blog storage, Save post as Graphify-compatible Markdown, Generate semantic filepath: {date}/{timestamp}_{id}_{title}.md, Save index.json for fast retrieval

### Community 1 - "Community 1"
Cohesion: 0.5
Nodes (3): Export graph to Graphify-compatible JSON, load_bloggers(), main()

### Community 2 - "Community 2"
Cohesion: 0.4
Nodes (2): Add edges between temporally adjacent posts, Load posts from Markdown files with frontmatter

### Community 3 - "Community 3"
Cohesion: 0.4
Nodes (3): BlogGraphBuilder, Blog knowledge graph builder - Graphify extension for blog content, Build knowledge graph from blog Markdown files     Graphify-compatible with addi

### Community 4 - "Community 4"
Cohesion: 0.4
Nodes (3): Sina Blog spider - fetch articles with anti-418 bypass, Sina blog article spider, SinaSpider

### Community 5 - "Community 5"
Cohesion: 0.5
Nodes (2): Parse frontmatter and content from Markdown, Parse tags from YAML-like or JSON array format

### Community 6 - "Community 6"
Cohesion: 0.5
Nodes (2): Iterate all articles with pagination, Fetch single article page

### Community 7 - "Community 7"
Cohesion: 0.67
Nodes (1): Extract keywords from content

### Community 8 - "Community 8"
Cohesion: 1.0
Nodes (1): Blog Downloader - AI Knowledge Base Optimized Graphify-compatible blog scraping

### Community 9 - "Community 9"
Cohesion: 1.0
Nodes (1): Add edges based on shared tags

### Community 10 - "Community 10"
Cohesion: 1.0
Nodes (1): Parse article links from list page

### Community 11 - "Community 11"
Cohesion: 1.0
Nodes (1): Parse article content from page

### Community 12 - "Community 12"
Cohesion: 1.0
Nodes (1): Fetch article list page

## Knowledge Gaps
- **22 isolated node(s):** `Blog Downloader - AI Knowledge Base Optimized Graphify-compatible blog scraping`, `Markdown storage with Graphify-compatible frontmatter`, `Graphify-compatible blog storage`, `Save post as Graphify-compatible Markdown`, `Generate semantic filepath: {date}/{timestamp}_{id}_{title}.md` (+17 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 8`** (2 nodes): `Blog Downloader - AI Knowledge Base Optimized Graphify-compatible blog scraping`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 9`** (2 nodes): `._add_tag_edges()`, `Add edges based on shared tags`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 10`** (2 nodes): `Parse article links from list page`, `.parse_article_list()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 11`** (2 nodes): `Parse article content from page`, `.parse_article()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 12`** (2 nodes): `Fetch article list page`, `.fetch_list_page()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `main()` connect `Community 1` to `Community 0`, `Community 2`, `Community 3`, `Community 4`, `Community 6`?**
  _High betweenness centrality (0.633) - this node is a cross-community bridge._
- **Why does `BlogGraphBuilder` connect `Community 3` to `Community 1`, `Community 2`, `Community 5`, `Community 7`, `Community 9`?**
  _High betweenness centrality (0.384) - this node is a cross-community bridge._
- **Why does `SinaSpider` connect `Community 4` to `Community 1`, `Community 6`, `Community 10`, `Community 11`, `Community 12`?**
  _High betweenness centrality (0.264) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `main()` (e.g. with `SinaSpider` and `BlogStorage`) actually correct?**
  _`main()` has 7 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Blog Downloader - AI Knowledge Base Optimized Graphify-compatible blog scraping`, `Markdown storage with Graphify-compatible frontmatter`, `Graphify-compatible blog storage` to the rest of the system?**
  _22 weakly-connected nodes found - possible documentation gaps or missing edges._