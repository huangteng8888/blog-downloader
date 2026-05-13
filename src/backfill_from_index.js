/**
 * Backfill article content from existing index.json using Playwright
 * Reads URLs from index.json, fetches content, writes markdown files
 */
const { chromium } = require('playwright-core');
const fs = require('fs');
const path = require('path');
const readline = require('readline');

const PLAYWRIGHT_BROWSERS_PATH = process.env.PLAYWRIGHT_BROWSERS_PATH || '/home/ht/.cache/ms-playwright';
const UIDS = {
  '1300871220': { name: '徐小明', dir: 'output/1300871220/posts' },
  '1215172700': { name: '缠中说禅', dir: 'output/1215172700/posts' },
  '1285707277': { name: '股市风云', dir: 'output/1285707277/posts' },
};

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function fetchArticleContent(page, url) {
  await page.setExtraHTTPHeaders({
    'Referer': 'https://blog.sina.com.cn/',
    'Accept-Language': 'zh-CN,zh;q=0.9',
  });
  await page.goto(url, { timeout: 25000, waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);
  
  return await page.evaluate(() => {
    // Try multiple selectors for different blog layouts
    const getText = (el) => el ? el.textContent.replace(/\s+/g, ' ').trim() : '';
    
    const title = getText(document.querySelector('h2.titName') || 
                   document.querySelector('h2[class*="titName"]') ||
                   document.querySelector('.article-title') ||
                   document.querySelector('h2') || null);
    
    let date = '';
    const timeEl = document.querySelector('span[class*="time"]') || 
                   document.querySelector('.time') ||
                   document.querySelector('[class*="time"]');
    if (timeEl) {
      const m = timeEl.textContent.match(/(\d{4}-\d{2}-\d{2}[^\d]*\d{2}:\d{2}:\d{2})/);
      date = m ? m[1] : timeEl.textContent.replace(/[()（）]/g, '').trim();
    }
    
    let content = '';
    const contentEl = document.querySelector('div.articalContent') ||
                      document.querySelector('.articalContent') ||
                      document.querySelector('#sina_keyword_ad_area') ||
                      document.querySelector('[class*="articalContent"]') ||
                      document.querySelector('[class*="articleContent"]');
    if (contentEl) {
      content = contentEl.innerText || contentEl.textContent;
    }
    
    // Extract tags
    let tags = [];
    const tagScript = document.body.textContent.match(/var\s+\$tag\s*=\s*"([^"]+)"/) ||
                     document.body.textContent.match(/var\s+\$tag\s*=\s*'([^']+)'/);
    if (tagScript) {
      tags = tagScript[1].split(/[,，]/).map(t => t.trim()).filter(Boolean);
    }
    
    return { title, date, content, tags };
  });
}

async function backfillUID(uid, maxArticles = Infinity) {
  const info = UIDS[uid];
  if (!info) { console.error(`Unknown UID: ${uid}`); return; }
  
  const indexPath = path.join('/home/ht/github/blog-downloader', info.dir, 'index.json');
  if (!fs.existsSync(indexPath)) {
    console.error(`index.json not found: ${indexPath}`);
    return;
  }
  
  const index = JSON.parse(fs.readFileSync(indexPath, 'utf-8'));
  const articles = index.posts || index.articles || [];
  
  console.error(`[${info.name}] ${articles.length} articles in index.json`);
  
  // Check actual files on disk
  const existingOnDisk = new Set(
    fs.readdirSync(path.join('/home/ht/github/blog-downloader', info.dir))
      .filter(f => f.endsWith('.md'))
      .map(f => f.replace(/\.md$/, ''))
  );
  
  const toFetch = articles
    .filter(a => {
      const base = (a.filename || `${a.id}.md`).replace(/\.md$/, '');
      return !existingOnDisk.has(base);
    })
    .slice(0, maxArticles);
  
  console.error(`[${info.name}] ${existingOnDisk.size} existing, ${toFetch.length} to fetch`);
  
  if (toFetch.length === 0) {
    console.error(`[${info.name}] Nothing to fetch`);
    return;
  }
  
  const browser = await chromium.launch({
    headless: true,
    executablePath: PLAYWRIGHT_BROWSERS_PATH + '/chromium-1208/chrome-linux64/chrome',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  });
  
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36',
  });
  
  let processed = 0;
  let success = 0;
  let failed = 0;
  
  for (const article of toFetch) {
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
      ].filter(Boolean).join('\n');
      
      const markdown = `${frontmatter}\n${data.content || ''}\n`;
      
      // Use filename from index to avoid duplicates
      const filename = article.filename || `${article.id}.md`;
      const filepath = path.join('/home/ht/github/blog-downloader', info.dir, filename);
      
      fs.writeFileSync(filepath, markdown, 'utf-8');
      success++;
      
      processed++;
      if (processed % 20 === 0) {
        console.error(`[${info.name}] Progress: ${processed}/${toFetch.length}, success: ${success}`);
      }
    } catch (err) {
      failed++;
      // Try again without waiting on error
    } finally {
      await page.close();
    }
    
    // Rate limit
    await sleep(300);
  }
  
  await browser.close();
  console.error(`[${info.name}] Done: ${success} saved, ${failed} failed`);
}

const uid = process.argv[2];
const max = parseInt(process.argv[3] || '50', 10);

if (!uid) {
  console.error('Usage: node backfill_from_index.js <uid> [max_articles]');
  process.exit(1);
}

backfillUID(uid, max).catch(err => {
  console.error('Fatal error:', err.message);
  process.exit(1);
});
