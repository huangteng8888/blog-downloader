/**
 * High-performance article fetcher using Playwright
 * Bypasses HTTP 418 blocking that affects Python requests
 */
const { chromium } = require('playwright-core');
const PLAYWRIGHT_BROWSERS_PATH = process.env.PLAYWRIGHT_BROWSERS_PATH || '/home/ht/.cache/ms-playwright';

async function fetchArticles(urls) {
  const browser = await chromium.launch({
    headless: true,
    executablePath: PLAYWRIGHT_BROWSERS_PATH + '/chromium-1208/chrome-linux64/chrome',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=BlockCredentialedDownloads']
  });
  
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
  });
  
  const results = [];
  
  try {
    // Process in batches to avoid overwhelming the browser
    const BATCH_SIZE = 10;
    for (let i = 0; i < urls.length; i += BATCH_SIZE) {
      const batch = urls.slice(i, i + BATCH_SIZE);
      const batchResults = await Promise.all(
        batch.map(url => fetchOneArticle(context, url))
      );
      results.push(...batchResults);
    }
  } finally {
    await browser.close();
  }
  
  return results;
}

async function fetchOneArticle(context, url) {
  const page = await context.newPage();
  await page.setExtraHTTPHeaders({
    'Referer': 'https://blog.sina.com.cn/',
    'Accept-Language': 'zh-CN,zh;q=0.9',
  });
  
  try {
    await page.goto(url, { timeout: 25000, waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);
    
    const data = await page.evaluate(() => {
      // Extract title
      const titleEl = document.querySelector('h2.titName') || 
                      document.querySelector('h2[class*="titName"]') ||
                      document.querySelector('h2');
      const title = titleEl ? titleEl.textContent.trim() : '';
      
      // Extract date
      const timeEl = document.querySelector('span[class*="time"]');
      let date = '';
      if (timeEl) {
        const match = timeEl.textContent.match(/\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\)/);
        date = match ? match[1] : timeEl.textContent.replace(/[()]/g, '').trim();
      }
      
      // Extract content
      const contentEl = document.querySelector('div.articalContent') ||
                        document.querySelector('div[class*="articalContent"]');
      let content = '';
      if (contentEl) {
        content = contentEl.textContent.replace(/\s+/g, ' ').trim();
      }
      
      // Extract images
      const images = [];
      if (contentEl) {
        contentEl.querySelectorAll('img').forEach(img => {
          const src = img.src || img.getAttribute('_src') || img.getAttribute('real_src');
          if (src && !src.includes('sinaimg')) {
            images.push(src);
          }
        });
      }
      
      // Extract tags
      let tags = [];
      const tagMatch = document.body.textContent.match(/var\s+\$tag\s*=\s*'([^']+)'/);
      if (tagMatch) {
        tags = tagMatch[1].split(',').map(t => t.trim()).filter(t => t);
      }
      
      return { title, date, content, images, tags };
    });
    
    // Extract article ID from URL
    const idMatch = url.match(/blog_([0-9a-z]+)\.html/);
    const id = idMatch ? idMatch[1] : '';
    
    return {
      id,
      title: data.title,
      url,
      date: data.date,
      tags: data.tags,
      content: data.content,
      images: data.images,
    };
  } catch (error) {
    return {
      id: (url.match(/blog_([0-9a-z]+)\.html/)?.[1] || ''),
      url,
      title: '',
      date: '',
      tags: [],
      content: '',
      images: [],
      error: error.message,
    };
  } finally {
    await page.close();
  }
}

module.exports = { fetchArticles };

// CLI: fetch specific URLs from stdin
if (require.main === module) {
  const urls = process.argv.slice(2);
  
  if (urls.length === 0) {
    // Read URLs from stdin
    let input = '';
    process.stdin.on('data', chunk => input += chunk);
    process.stdin.on('end', async () => {
      const urlList = input.trim().split('\n').filter(u => u.trim());
      const results = await fetchArticles(urlList);
      console.log(JSON.stringify(results));
    });
  } else {
    (async () => {
      const results = await fetchArticles(urls);
      console.log(JSON.stringify(results));
    })();
  }
}
