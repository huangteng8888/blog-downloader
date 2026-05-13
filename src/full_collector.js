/**
 * Full collection pipeline: list pages → build index → fetch content
 * All-in-one script that handles both spider.js listing and Playwright content fetching
 */
const { chromium } = require('playwright-core');
const fs = require('fs');
const path = require('path');
const https = require('https');
const http = require('http');

const PLAYWRIGHT_BROWSERS_PATH = process.env.PLAYWRIGHT_BROWSERS_PATH || '/home/ht/.cache/ms-playwright';
const BASE = '/home/ht/github/blog-downloader';
const DELAY_MS = 250; // delay between requests
const PAGES_BATCH = 10; // pages per listing batch
const ARTICLES_BATCH = 10; // articles per content fetch batch

const UIDS = {
  '1300871220': { name: '徐小明', dir: 'output/1300871220/posts' },
  '1215172700': { name: '缠中说禅', dir: 'output/1215172700/posts' },
  '1285707277': { name: '股市风云', dir: 'output/1285707277/posts' },
};

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// Use native https/http to avoid 418 - needs Referer header via Agent
function httpGet(url) {
  return new Promise((resolve, reject) => {
    const isHttps = url.startsWith('https://');
    const mod = isHttps ? https : http;
    const parsedUrl = new URL(url);
    
    const headers = {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36',
      'Referer': 'https://blog.sina.com.cn/',
      'Accept-Language': 'zh-CN,zh;q=0.9',
      'Accept': 'text/html,application/xhtml+xml',
    };
    
    const req = mod.get(url, { headers, timeout: 20000 }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        // Handle redirect
        httpGet(res.headers.location).then(resolve).catch(reject);
        return;
      }
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve({ status: res.statusCode, body: data }));
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
  });
}

// Extract article links from a blog list page HTML
// Pattern: <a href=".../s/blog_ID.html">Title</a> or <a href="https://blog.sina.com.cn/s/blog_ID.html">
function parseListPage(html) {
  const articles = [];
  const re = /<a\s[^>]*href=["'](?:https?:\/\/blog\.sina\.com\.cn)?(\/s\/blog_[0-9a-z]+)\.html["'][^>]*>([^<]+)<\/a>/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    const url = 'https://blog.sina.com.cn' + m[1] + '.html';
    const title = m[2].trim();
    if (title && !title.includes('javascript') && title.length > 3) {
      articles.push({ url, title });
    }
  }
  return articles;
}

// Find last page number from blog list page
function findLastPage(html) {
  // Look for pagination: <a href="...page=248">... or similar
  const matches = html.match(/page=(\d+)"/g) || [];
  const pages = matches.map(m => parseInt(m.match(/page=(\d+)"/)?.[1], 10)).filter(n => n > 0);
  return pages.length > 0 ? Math.max(...pages) : 0;
}

// Get article content via Playwright (bypasses 418)
async function fetchArticleContent(page, url) {
  await page.setExtraHTTPHeaders({
    'Referer': 'https://blog.sina.com.cn/',
    'Accept-Language': 'zh-CN,zh;q=0.9',
  });
  await page.goto(url, { timeout: 25000, waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  
  return await page.evaluate(() => {
    const getText = (el) => el ? el.textContent.replace(/\s+/g, ' ').trim() : '';
    
    const title = getText(document.querySelector('h2.titName') || 
                   document.querySelector('.article-title') ||
                   document.querySelector('h2[class*="titName"]') ||
                   document.querySelector('h2'));
    
    let date = '';
    const timeEl = document.querySelector('span[class*="time"]') || document.querySelector('.time');
    if (timeEl) {
      const m = timeEl.textContent.match(/(\d{4}-\d{2}-\d{2}[^\d]*\d{2}:\d{2}:\d{2})/);
      date = m ? m[1].replace(/\s+/g, ' ') : timeEl.textContent.replace(/[()（）]/g, '').trim();
    }
    
    let content = '';
    const contentEl = document.querySelector('div.articalContent') ||
                      document.querySelector('#sina_keyword_ad_area') ||
                      document.querySelector('[class*="articalContent"]');
    if (contentEl) {
      content = contentEl.innerText || contentEl.textContent;
    }
    
    let tags = [];
    const tagMatch = document.body.textContent.match(/var\s+\$tag\s*=\s*"([^"]+)"/) ||
                     document.body.textContent.match(/var\s+\$tag\s*=\s*'([^']+)'/);
    if (tagMatch) {
      tags = tagMatch[1].split(/[,，]/).map(t => t.trim()).filter(Boolean);
    }
    
    return { title: title || '', date: date || '', content: content || '', tags };
  });
}

// Generate a deterministic filename from URL
function urlToFilename(url) {
  const m = url.match(/blog_([0-9a-z]+)\.html/);
  if (m) return m[1] + '.md';
  // Fallback: hash the URL
  let hash = 0;
  for (let i = 0; i < url.length; i++) {
    hash = ((hash << 5) - hash) + url.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash).toString(16) + '.md';
}

// Main pipeline for one UID
async function collectUID(uid, options = {}) {
  const { maxPages = Infinity, maxArticles = Infinity, startPage = 1 } = options;
  const info = UIDS[uid];
  if (!info) { throw new Error(`Unknown UID: ${uid}`); }
  
  const dir = path.join(BASE, info.dir);
  const indexPath = path.join(dir, 'index.json');
  
  // Load existing index if present
  let existingIndex = { total: 0, updated_at: '', posts: [] };
  if (fs.existsSync(indexPath)) {
    try {
      existingIndex = JSON.parse(fs.readFileSync(indexPath, 'utf-8'));
    } catch(e) {}
  }
  
  const existingUrls = new Set(existingIndex.posts.map(p => p.source_url));
  
  console.error(`[${info.name}] Existing index: ${existingIndex.posts.length} posts`);
  
  // Step 1: List articles from blog pages
  console.error(`[${info.name}] Step 1: Listing articles...`);
  let allArticles = [...existingIndex.posts];
  let currentPage = startPage;
  let lastPage = 0;
  let pagesFetched = 0;
  
  while (pagesFetched < maxPages) {
    const pageUrl = `https://blog.sina.com.cn/u/${uid}?page=${currentPage}`;
    
    try {
      const res = await httpGet(pageUrl);
      
      if (res.status === 200) {
        if (lastPage === 0) {
          lastPage = findLastPage(res.body);
          console.error(`[${info.name}] Last page: ${lastPage}`);
        }
        
        const articles = parseListPage(res.body);
        console.error(`[${info.name}] Page ${currentPage}: ${articles.length} articles`);
        
        if (articles.length === 0) break;
        
        for (const a of articles) {
          if (!existingUrls.has(a.url)) {
            allArticles.push({
              id: urlToFilename(a.url).replace('.md', ''),
              title: a.title,
              published_at: '',
              published_date: '',
              tags: [],
              images_count: 0,
              filename: urlToFilename(a.url),
              source_url: a.url,
              indexed_at: new Date().toISOString(),
            });
          }
        }
        
        if (currentPage >= lastPage) break;
        currentPage++;
        pagesFetched++;
      } else if (res.status === 418) {
        console.error(`[${info.name}] Page ${currentPage}: 418 blocked, retrying...`);
        await sleep(3000);
        continue;
      } else {
        console.error(`[${info.name}] Page ${currentPage}: HTTP ${res.status}`);
        break;
      }
    } catch(e) {
      console.error(`[${info.name}] Page ${currentPage}: ${e.message}`);
      await sleep(2000);
      continue;
    }
    
    await sleep(DELAY_MS * 2);
  }
  
  // Save updated index with listing info
  const updatedIndex = {
    total: allArticles.length,
    updated_at: new Date().toISOString(),
    posts: allArticles,
  };
  fs.writeFileSync(indexPath, JSON.stringify(updatedIndex, null, 2), 'utf-8');
  console.error(`[${info.name}] Index saved: ${allArticles.length} posts`);
  
  // Step 2: Fetch article content via Playwright
  console.error(`[${info.name}] Step 2: Fetching content...`);
  
  const browser = await chromium.launch({
    headless: true,
    executablePath: PLAYWRIGHT_BROWSERS_PATH + '/chromium-1208/chrome-linux64/chrome',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  });
  
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36',
  });
  
  // Find articles missing content files
  const existingFiles = new Set(
    fs.readdirSync(dir).filter(f => f.endsWith('.md')).map(f => f.replace(/\.md$/, ''))
  );
  
  const toFetch = allArticles
    .filter(a => !existingFiles.has((a.filename || '').replace('.md', '') || a.id))
    .slice(0, maxArticles);
  
  console.error(`[${info.name}] ${existingFiles.size} files exist, ${toFetch.length} need content`);
  
  let success = 0, failed = 0;
  for (let i = 0; i < toFetch.length; i++) {
    const article = toFetch[i];
    const page = await context.newPage();
    try {
      const data = await fetchArticleContent(page, article.source_url);
      
      const frontmatter = [
        '---',
        `title: "${(data.title || article.title || '').replace(/"/g, '\\"')}"`,
        `date: "${article.published_at || article.published_date || data.date || ''}"`,
        `source: "${article.source_url}"`,
        `tags: [${(article.tags || data.tags || []).map(t => `"${t.replace(/"/g, '\\"')}"`).join(', ')}]`,
        '---',
        '',
      ].join('\n');
      
      const filename = article.filename || urlToFilename(article.source_url);
      const filepath = path.join(dir, filename);
      fs.writeFileSync(filepath, `${frontmatter}\n${data.content || ''}\n`, 'utf-8');
      success++;
    } catch(e) {
      failed++;
    } finally {
      await page.close();
    }
    
    if ((i + 1) % 20 === 0) {
      console.error(`[${info.name}] Content: ${i + 1}/${toFetch.length}, success: ${success}, failed: ${failed}`);
    }
    
    await sleep(DELAY_MS);
  }
  
  await browser.close();
  console.error(`[${info.name}] Done: ${success} saved, ${failed} failed`);
  
  return { success, failed };
}

// CLI
const uid = process.argv[2];
const maxPages = parseInt(process.argv[3] || '9999', 10);
const maxArticles = parseInt(process.argv[4] || '99999', 10);

if (!uid || !UIDS[uid]) {
  console.error(`Usage: node full_collector.js <uid> [maxPages] [maxArticles]`);
  console.error(`Available UIDs: ${Object.keys(UIDS).join(', ')}`);
  process.exit(1);
}

collectUID(uid, { maxPages, maxArticles }).catch(err => {
  console.error('Fatal:', err.message);
  process.exit(1);
});
