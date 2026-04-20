/**
 * Sina Blog Spider - Uses articlelist pages (static HTML, 50 articles/page)
 * URL pattern: https://blog.sina.com.cn/s/articlelist_{uid}_0_{page}.html
 */
const { chromium } = require('playwright-core');
const PLAYWRIGHT_BROWSERS_PATH = process.env.PLAYWRIGHT_BROWSERS_PATH || '/home/ht/.cache/ms-playwright';

async function getArticleList(uid, page = 1) {
  const browser = await chromium.launch({
    headless: true,
    executablePath: PLAYWRIGHT_BROWSERS_PATH + '/chromium-1208/chrome-linux64/chrome'
  });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
  });
  const pageObj = await context.newPage();

  try {
    // Articlelist URL: category 0 = all, page numbered
    const url = `https://blog.sina.com.cn/s/articlelist_${uid}_0_${page}.html`;

    await pageObj.goto(url, {
      waitUntil: 'domcontentloaded',
      timeout: 30000
    });
    await pageObj.waitForTimeout(2000); // Wait for any JS

    const articles = await pageObj.evaluate(() => {
      const seen = new Set();
      const results = [];

      // Find all article links in the page
      document.querySelectorAll('a').forEach(a => {
        const href = a.href;
        // Match blog article URLs: /s/blog_ARTICLEID.html or full URL
        if (href.includes('/s/blog_') && !href.includes('comment') && !href.includes('#')) {
          const match = href.match(/blog_([0-9a-z]+)\.html/);
          if (match && !seen.has(match[1])) {
            seen.add(match[1]);
            const title = a.textContent.trim();
            // Skip navigation links and empty titles
            if (title && title.length > 3 && !title.includes('...')) {
              results.push({ title, url: href });
            }
          }
        }
      });

      return results;
    });

    console.log(`Page ${page}: ${articles.length} articles`);
    return articles;
  } catch (error) {
    console.error(`Error page ${page}:`, error.message);
    return [];
  } finally {
    await browser.close();
  }
}

module.exports = { getArticleList };

// CLI
if (require.main === module) {
  const args = process.argv.slice(2);
  const uid = args.find(a => a.startsWith('--uid='))?.split('=')[1] || '1300871220';
  const pageArg = args.find(a => a.startsWith('--page='));
  const page = pageArg ? parseInt(pageArg.split('=')[1]) : null;
  const listOnly = args.includes('--list-only');
  const testPages = parseInt(args.find(a => a.startsWith('--test-pages='))?.split('=')[1] || '0');

  (async () => {
    // Single page mode (for programmatic use via spider.py)
    if (page !== null) {
      const articles = await getArticleList(uid, page);
      if (listOnly) {
        console.log(JSON.stringify(articles));
      } else {
        console.log(`Page ${page} first URL:`, articles[0]?.url);
      }
    }
    // Test mode (for manual testing)
    else if (testPages > 0) {
      for (let p = 1; p <= testPages; p++) {
        const articles = await getArticleList(uid, p);
        console.log(`Page ${p} first URL:`, articles[0]?.url);
      }
    }
    // Default: test 3 pages
    else {
      for (let p = 1; p <= 3; p++) {
        const articles = await getArticleList(uid, p);
        console.log(`Page ${p} first URL:`, articles[0]?.url);
      }
    }
    process.exit(0);
  })();
}
