/**
 * List all articles for a UID using curl + grep (bypasses 418 with Referer header)
 * Much faster than Playwright for listing
 */
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const BASE = '/home/ht/github/blog-downloader';
const UIDS = {
  '1300871220': { name: '徐小明', dir: 'output/1300871220/posts' },
  '1215172700': { name: '缠中说禅', dir: 'output/1215172700/posts' },
  '1285707277': { name: '股市风云', dir: 'output/1285707277/posts' },
};

function shell(cmd, opts = {}) {
  return execSync(cmd, { encoding: 'utf-8', maxBuffer: 5 * 1024 * 1024, timeout: 20000, ...opts });
}

function fetchPage(uid, page) {
  const url = `https://blog.sina.com.cn/s/articlelist_${uid}_0_${page}.html`;
  // Use grep -oP for reliable extraction (Perl regex)
  const html = shell(`curl -s '${url}' \\
    -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36' \\
    -H 'Referer: https://blog.sina.com.cn/' \\
    -H 'Accept-Language: zh-CN,zh;q=0.9' | \\
    grep -oP 'href="//blog\\.sina\\.com\\.cn/s/blog_[^"]{10,30}"[^>]*>[^<]+' | \\
    sed 's/href="//blog\\.sina\\.com\\.cn/\\nhttps:\\/\\/blog.sina.com.cn/g' | \\
    grep '^https://blog.sina.com.cn' | \\
    while read line; do
      url=\$(echo \$line | sed 's/".*//')
      title=\$(echo \$line | sed 's/.*>//')
      echo "\$url|\$title"
    done`, { maxBuffer: 10 * 1024 * 1024 });
  
  const articles = [];
  for (const line of html.trim().split('\n')) {
    const pipeIdx = line.lastIndexOf('|');
    if (pipeIdx > 0) {
      const url = line.substring(0, pipeIdx);
      const title = line.substring(pipeIdx + 1).trim();
      if (title && title.length > 2) {
        articles.push({ url, title });
      }
    }
  }
  return articles;
}

function findLastPage(html) {
  const matches = html.match(/page=(\d+)["&']/g) || [];
  const pages = matches
    .map(m => parseInt(m.match(/page=(\d+)/)?.[1], 10))
    .filter(n => n > 0 && n < 10000);
  return pages.length > 0 ? Math.max(...pages) : 0;
}

function urlToId(url) {
  const m = url.match(/blog_([0-9a-z]+)\.html/);
  return m ? m[1] : '';
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function listUID(uid, options = {}) {
  const { maxPages = Infinity } = options;
  const info = UIDS[uid];
  if (!info) throw new Error(`Unknown UID: ${uid}`);
  
  const dir = path.join(BASE, info.dir);
  const indexPath = path.join(dir, 'index.json');
  
  // Load existing index
  let existingIndex = { total: 0, updated_at: '', posts: [] };
  if (fs.existsSync(indexPath)) {
    try { existingIndex = JSON.parse(fs.readFileSync(indexPath, 'utf-8')); } catch(e) {}
  }
  const existingUrls = new Set(existingIndex.posts.map(p => p.source_url));
  console.error(`[${info.name}] Existing: ${existingIndex.posts.length} posts`);
  
  let allArticles = [...existingIndex.posts];
  let page = 1;
  let lastPage = 0;
  let pagesFound = 0;
  
  while (page <= maxPages) {
    try {
      const html = fetchPage(uid, page);
      
      if (html.includes('captcha') || html.includes('验证码') || html.length < 1000) {
        console.error(`[${info.name}] Page ${page}: Captcha or small response (${html.length} bytes), retrying...`);
        await sleep(3000);
        continue;
      }
      
      if (lastPage === 0) {
        lastPage = findLastPage(html);
        if (lastPage > 0) console.error(`[${info.name}] Last page: ${lastPage}`);
      }
      
      const articles = parseArticles(html);
      console.error(`[${info.name}] Page ${page}: ${articles.length} articles`);
      
      if (articles.length === 0) {
        console.error(`[${info.name}] Empty page (${html.length} bytes), stopping`);
        break;
      }
      
      let newCount = 0;
      for (const a of articles) {
        if (!existingUrls.has(a.url)) {
          const id = urlToId(a.url);
          allArticles.push({
            id,
            title: a.title,
            published_at: '',
            published_date: '',
            tags: [],
            images_count: 0,
            filename: id + '.md',
            source_url: a.url,
            indexed_at: new Date().toISOString(),
          });
          existingUrls.add(a.url);
          newCount++;
        }
      }
      
      if (newCount > 0) {
        // Save index incrementally every 10 pages
        if (page % 10 === 0) {
          fs.writeFileSync(indexPath, JSON.stringify({
            total: allArticles.length,
            updated_at: new Date().toISOString(),
            posts: allArticles,
          }, null, 2), 'utf-8');
          console.error(`[${info.name}] Index checkpoint: ${allArticles.length} posts`);
        }
      }
      
      if (page >= lastPage && lastPage > 0) {
        console.error(`[${info.name}] Reached last page ${lastPage}`);
        break;
      }
      
      page++;
      pagesFound++;
      
      await sleep(300); // Rate limit
    } catch(e) {
      console.error(`[${info.name}] Page ${page}: ${e.message}`);
      await sleep(2000);
    }
  }
  
  // Final save
  fs.writeFileSync(indexPath, JSON.stringify({
    total: allArticles.length,
    updated_at: new Date().toISOString(),
    posts: allArticles,
  }, null, 2), 'utf-8');
  
  console.error(`[${info.name}] Done: ${allArticles.length} posts from ${pagesFound} pages`);
  return { posts: allArticles.length, pages: pagesFound };
}

const uid = process.argv[2];
const maxPages = parseInt(process.argv[3] || '9999', 10);

if (!uid || !UIDS[uid]) {
  console.error(`Usage: node fetch_list.js <uid> [maxPages]`);
  console.error(`Available: ${Object.keys(UIDS).join(', ')}`);
  process.exit(1);
}

listUID(uid, { maxPages }).catch(err => {
  console.error('Fatal:', err.message);
  process.exit(1);
});
