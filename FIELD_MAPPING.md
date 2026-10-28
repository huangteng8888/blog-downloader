# 字段来源对照表

## 最终数据结构

### metadata.json (博主信息)
```json
{
  "version": "2.0",
  "bloggers": {
    "1300871220": {
      "name": "徐小明"
    }
  }
}
```

### posts/index.json (文章索引)
```json
{
  "version": "2.0",
  "uid": "1300871220",
  "total": 10,
  "updated_at": "2026-04-16T15:24:03Z",
  "posts": [
    {
      "article_id": "4d89b8340103028m",
      "title": "徐小明：4月17日盘中即时直播",
      "pub_time": "2026-04-16 15:01:20",
      "pub_date": "2026-04-16",
      "timestamp": 1776322880,
      "author_uid": "1300871220",
      "author_name": "徐小明",
      "images_count": 10,
      "filename": "4d89b8340103028m.md",
      "source_url": "https://blog.sina.com.cn/s/blog_4d89b8340103028m.html",
      "indexed_at": "2026-04-16T15:24:03Z"
    }
  ],
  "date_index": {"2026-04": 10}
}
```

### {article_id}.md (frontmatter最小化)
```markdown
---
article_id: 4d89b8340103028m
---

正文内容...
```

---

## 字段映射

| 字段 | 新浪HTML来源 | 说明 |
|------|-------------|------|
| article_id | URL `blog_([^.]+).html` | 文章唯一标识 |
| title | `<h2 class="titName">` | 文章标题 |
| pub_time | `<span class="time">` | 发布时间 |
| image_urls | `<div class="articalContent"><img src=...>` | 图片列表 |
| source_url | 文章URL | 原文链接 |
| author_uid | 输入参数 | 博主UID |
| author_name | `<title>` | 博主昵称 |
| images_count | 从image_urls计算 | 图片数量 |

---

## 存储结构

```
output/{uid}/
├── metadata.json              # 博主信息
└── posts/
    ├── index.json           # 文章索引 + date聚合
    └── {article_id}.md     # frontmatter最小化
```

---

## 已移除字段

- `tags` - 来源不稳定，非核心数据
- `category` - 新浪无此字段
- `likes/comments` - 需要登录
- `word_count/keywords` - 非新浪来源
