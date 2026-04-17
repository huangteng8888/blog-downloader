# Blog Downloader

新浪博客下载器，支持增量更新、Graphify兼容输出、知识图构建。

## 架构

```
├── src/
│   ├── spider.js      # Playwright爬虫（列表页JS渲染）
│   ├── spider.py     # Python爬虫（curl获取文章详情）
│   ├── storage.py     # Graphify兼容Markdown存储
│   ├── blog_graph.py # 知识图构建器
│   └── runner.py      # 主程序
├── config/
│   └── bloggers.yaml  # 博主配置
└── output/            # 输出目录
```

## 快速开始

```bash
cd /tmp/blog-downloader

# 安装依赖
npm install playwright
npx playwright install chromium

# 运行下载（当前支持获取首页列表约11篇）
python3 src/runner.py --uid 1300871220 --max-pages 1 --delay 0.5

# 查看输出
ls output/1300871220/posts/
cat output/1300871220/index.json

# 查看知识图
cat output/1300871220/knowledge_graph.json
```

## 输出格式

每篇文章：`{timestamp}_{id}_{safe_title}.md`（扁平结构）

```yaml
---
id: 4d89b8340103028
author_uid: 1300871220
author_name: 徐小明
published_at: 2026-04-14 16:38:44
tags: ["徐小明", "交易师", "股票"]
source_url: https://blog.sina.com.cn/s/blog_4d89b8340103028h.html
---

# 文章标题

正文内容...
```

## 知识图

BlogGraphBuilder 构建包含：
- **节点**：post（文章）、author（作者）、keyword（关键词）
- **边**：shares_tag（共享标签）、next_post（时间相邻）、wrote（写作关系）、contains_keyword（包含关键词）

## 元数据层

BlogMetadata 三层架构：
1. **blogger.json** - 博主个人信息
2. **download.json** - 下载会话统计
3. **posts/index.json** - 所有文章索引（快速检索）

## 已知限制

1. **分页**：Sina博客分页使用JS渲染，当前版本仅支持获取首页列表
2. **反爬**：使用curl+Cookie绕过418，新浪可能随时调整策略
3. **图片本地化**：暂未实现，建议后续添加

## 测试

```bash
python3 -c "from tests.test_storage import *; test_generate_filepath(); test_save_post(); test_save_index(); print('All storage tests passed!')"
python3 -c "from tests.test_blog_graph import *; test_parse_tags(); test_extract_keywords(); test_build_graph_from_markdown(); print('All graph tests passed!')"
```
