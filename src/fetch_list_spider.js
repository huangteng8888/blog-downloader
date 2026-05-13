/**
 * List articles for a UID using spider.js
 * Saves to index.json incrementally
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

function urlToId(url) {
  const m = url.match(/blog_([0-9a-z]+)\.html/);
  return m ? m[1] : '';
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function callSpider(uid, page) {
  const output = execSync(`node src/spider.js --uid=${uid} --page=${page} --list-only`, {
    cwd: BASE,
    encoding: 'utf-8',
    timeout: 90000,
    maxBuffer: 50 * 1024 * 1024,
  });
  try {
    return JSON.parse(output.trim());
  } catch {
    return [];
  }
}

async function listUID(uid, options = {}) {
  const { maxPages = Infinity, delay = 2 } = options;
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
  let consecutiveEmpty = 0;

  while (page <= maxPages) {
    try {
      const articles = await callSpider(uid, page);
      console.error(`[${info.name}] Page ${page}: ${articles.length} articles`);

      if (articles.length === 0) {
        consecutiveEmpty++;
        if (consecutiveEmpty >= 3) {
          console.error(`[${info.name}] 3 consecutive empty pages, stopping`);
          break;
        }
      } else {
        consecutiveEmpty = 0;
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
        if (newCount > 0 && page % 10 === 0) {
          fs.writeFileSync(indexPath, JSON.stringify({
            total: allArticles.length,
            updated_at: new Date().toISOString(),
            posts: allArticles,
          }, null, 2), 'utf-8');
          console.error(`[${info.name}] Checkpoint: ${allArticles.length} posts`);
        }
        if (articles.length < 50) break; // Last page usually has fewer
      }

      page++;
      await sleep(delay * 1000);
    } catch(e) {
      console.error(`[${info.name}] Page ${page}: ${e.message}`);
      await sleep(3000);
    }
  }

  fs.writeFileSync(indexPath, JSON.stringify({
    total: allArticles.length,
    updated_at: new Date().toISOString(),
    posts: allArticles,
  }, null, 2), 'utf-8');

  console.error(`[${info.name}] Done: ${allArticles.length} posts, ${page - 1} pages`);
}

const uid = process.argv[2];
const maxPages = parseInt(process.argv[3] || '9999', 10);
const delay = parseFloat(process.argv[4] || '2');

if (!uid || !UIDS[uid]) {
  console.error(`Usage: node fetch_list_spider.js <uid> [maxPages] [delaySec]`);
  console.error(`Available: ${Object.keys(UIDS).join(', ')}`);
  process.exit(1);
}

listUID(uid, { maxPages, delay }).catch(err => {
  console.error('Fatal:', err.message);
  process.exit(1);
});
