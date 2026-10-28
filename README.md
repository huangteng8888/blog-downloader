# Blog Downloader

新浪博客下载器 + 股票知识库构建流水线。抓取博主全部文章 → 结构化 Markdown 知识库 →
知识图谱 → 股票关键词发现与文章打分（为下游「教你炒股票」类知识提取项目供数）。

## 架构

```
blog-downloader/
├── src/
│   ├── spider.js        # Playwright 爬虫：列表页（JS 渲染）解析
│   ├── spider.py        # Python 爬虫：curl 抓文章详情、绕 418、断点续传/增量
│   ├── storage.py       # Markdown 存储（极简 frontmatter，仅 article_id）
│   ├── metadata.py      # 两层元数据：metadata.json + posts/index.json
│   ├── blog_graph.py    # 知识图谱构建（读 index.json）
│   └── runner.py        # 主程序入口
├── config/
│   └── bloggers.yaml    # 博主配置
├── keyword_analyzer.py  # Phase 1：jieba TF-IDF 关键词发现
├── post_scorer.py       # Phase 2：多层词表文章打分 / 筛选
├── keyword_discovery/   # 词表与打分产物（stock_keywords_v1.json、scored/）
├── tools/               # 维护工具（体检 / 元数据回填 / 图谱重建）
├── tests/               # 单元测试
├── output/{uid}/        # 【当前有效数据】index.json + {article_id}.md + knowledge_graph.json
├── legacy/{uid}/        # v1 旧数据（归档保留，含恢复元数据所需字段，勿删）
└── docs/                # 现状说明与诊断记录
```

## 运行环境

- Python 3.10+（Windows / Linux 均可），依赖见 `requirements.txt`
- Node.js + Playwright（列表页抓取）
- 系统 `curl`（文章详情抓取；Windows 10 1803+ 自带）

```bash
python -m pip install -r requirements.txt
npm install playwright
npx playwright install chromium
```

> 路径约定：项目的输出/词表目录默认取**脚本所在目录**，也可用环境变量
> `BLOG_DOWNLOADER_ROOT` 覆盖。不再依赖 `/mnt/data`、`/tmp` 等固定绝对路径。

## 快速开始

```bash
# 全量/增量下载（增量：按 article_id 去重，命中已存在文章即停止翻页）
python src/runner.py --uid 1300871220 --blogger-name 徐小明 --max-pages 1 --delay 0.5

# 灾难恢复：从磁盘上已有的 .md 文件重建 index.json
python src/runner.py --uid 1300871220 --blogger-name 徐小明 --rebuild-index

# 关键词发现（Phase 1）
python keyword_analyzer.py

# 文章打分 / 筛选 top N（Phase 2）
python post_scorer.py --top 500 --blogger 1300871220
```

## 输出格式

存储结构：`output/{uid}/`

```
├── metadata.json          # 博主信息
├── knowledge_graph.json   # 知识图谱
└── posts/
    ├── index.json         # 文章索引（全是真实元数据，含 pub_time/timestamp/date_index）
    └── {article_id}.md    # 正文，frontmatter 仅含 article_id
```

`{article_id}.md`：

```markdown
---
article_id: 4d89b8340103028m
---

# 文章正文...
```

**设计取舍**：所有元数据集中在 `index.json`，单篇 md 只保留 `article_id`，
避免同一字段两处存储、不一致。

## 知识图谱

`BlogGraphBuilder` 读 `posts/index.json` 建图：

- **节点**：`post`（文章）、`author`（作者）、`tag`（标签枢纽）
- **边**：
  - `wrote`（作者 → 文章）
  - `next_post`（时间相邻）
  - `has_tag`（文章 → 标签枢纽，O(n) 边）
  - `shares_tag`（共享低频标签的文章两两相连；**仅对出现 ≤20 篇的标签建边**，
    否则「股票」这类标签会退化成约 7,500 万条边的完全图）
- 可选：`--with-keywords`（`tools/rebuild_graphs.py`）额外生成 `keyword` 节点与
  `contains_keyword` 边，使用 jieba 分词 + 停用词过滤，避免整句被当成关键词。

## 测试

```bash
# 从项目根目录执行（测试以 src 包方式导入）
python -c "from tests.test_storage import *; test_generate_filepath(); test_save_post(); test_metadata_blogger_info(); test_metadata_posts_index(); test_rebuild_from_files(); test_rebuild_preserves_metadata(); print('storage ALL OK')"
python -c "from tests.test_blog_graph import *; test_extract_keywords(); test_load_from_index(); test_load_from_markdown(); test_export_json(); print('graph ALL OK')"
```

## 示例：项目体检

```bash
# 检查索引元数据覆盖率与图谱结构（改动数据后必跑）
python tools/health_check.py
```

## 数据修复工具

```bash
# 从 legacy v1 旧数据回填元数据（并补入 v1 独有文章）
python tools/backfill_from_legacy.py

# 从 v1 图谱流式恢复元数据（支持 GB 级文件）
python tools/recover_from_v1_graph.py --uid 1285707277

# 定向重抓：从详情页补发布时间（带断点续跑，可用 --dry-run 预览）
python tools/backfill_from_sina.py --dry-run

# 重建知识图谱
python tools/rebuild_graphs.py
```

## 已知限制与待办

> 完整诊断、修复过程与数据指标见 `docs/项目现状.md`。

1. **`--rebuild-index` 的历史陷阱（已修复）**：该模式从单篇 md 反推索引，而 v2 的 md
   frontmatter 只有 `article_id`，曾**静默清空** `pub_time` / `author_name` / `timestamp`。
   现已改为：重建前自动备份 `index.json.bak`、用现有索引做存续映射回填、结束后打印覆盖率
   并在低于 50% 时告警。**但缺少 `legacy/` 数据源时仍不建议使用。**
2. **勿删 `legacy/`**：它是元数据恢复的唯一来源，含 23 篇 v2 缺失文章。
3. **16 篇（0.08%）时间不可恢复**：徐小明 13、股市风云 3，经定向重抓确认源站返回
   `404 page not found`（已被新浪删除）。`pub_time` 总覆盖率 99.92%。
4. **图表规模警戒**：`knowledge_graph.json` 超过 100 MB 通常意味着又出现完全图
   （标签两两连边）或整句关键词，参见 `docs/项目现状.md` §3.1 第 11、12 条。
5. **跨博主得分不可比**：`raw_score` 只在同一博主内有序。
6. **徐小明 top500 仍有 22.6% 短帖**：属该博主行文风格，需要纯理论长文请在筛选阶段加
   `text_len` 阈值。
7. **分页 / 反爬**：列表页为 JS 渲染，详情页靠 curl + Cookie 绕 418，策略可能随时失效。
8. **图片**：正文图片仍是新浪 CDN 外链，未本地化。
9. **无 git 仓库**：建议 `git init` 并提交当前基线。