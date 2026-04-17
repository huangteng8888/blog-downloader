/**
 * Sina Blog Spider - Optimized with checkpoint/resume
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

class SinaSpider {
  constructor(uid, outputDir = '/tmp/sina_download') {
    this.uid = uid;
    this.blogUrl = `https://blog.sina.com.cn/s/articlelist_${uid}`;
    this.outputDir = outputDir;
    this.checkpointFile = path.join(outputDir, `.checkpoint_${uid}.json`);
    this.browser = null;
    this.context = null;
    this.page = null;
  }

  async initBrowser() {
    if (!this.browser) {
      this.browser = await chromium.launch({ headless: true });
      this.context = await this.browser.newContext({
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
      });
      this.page = await this.context.newPage();
      await this.page.goto(`https://blog.sina.com.cn/u/${this.uid}`, {
        waitUntil: 'domcontentloaded',
        timeout: 15000
      });
      await this.page.waitForTimeout(2000);
    }
  }

  loadCheckpoint() {
    try {
      if (fs.existsSync(this.checkpointFile)) {
        const data = JSON.parse(fs.readFileSync(this.checkpointFile, 'utf8'));
        console.log(`Checkpoint loaded: page ${data.lastPage}, downloaded ${data.downloaded} articles`);
        return data;
      }
    } catch (e) {
      console.error('Failed to load checkpoint:', e.message);
    }
    return { lastPage: 1, lastArticleIndex: -1, downloaded: 0, failedUrls: [] };
  }

  saveCheckpoint(data) {
    try {
      fs.writeFileSync(this.checkpointFile, JSON.stringify(data, null, 2));
    } catch (e) {
      console.error('Failed to save checkpoint:', e.message);
    }
  }

  markFailed(url, error) {
    try {
      const checkpoint = this.loadCheckpoint();
      if (!checkpoint.failedUrls.includes(url)) {
        checkpoint.failedUrls.push({ url, error: error.message, time: new Date().toISOString() });
        this.saveCheckpoint(checkpoint);
      }
    } catch (e) {}
  }

  async getArticleList(page = 1) {
    await this.initBrowser();

    try {
      let articles;

      if (page === 1) {
        articles = await this.page.evaluate(() => {
          const seen = new Set();
          return Array.from(document.querySelectorAll('a'))
            .filter(a => a.href.includes('/s/blog_') && !a.href.includes('comment'))
            .filter(a => {
              if (seen.has(a.href)) return false;
              seen.add(a.href);
              return true;
            })
            .map(a => ({ title: a.textContent.trim(), url: a.href }));
        });
      } else {
        articles = await this.page.evaluate(async (params) => {
          const { pageNum, uid } = params;
          try {
            const resp = await fetch(`/s/article_sort_${uid}_10001_${pageNum}.html`, {
              headers: { 'Accept': 'text/html', 'X-Requested-With': 'XMLHttpRequest' }
            });
            const text = await resp.text();
            const seen = new Set();
            const matches = text.match(/href="(\/\/blog\.sina\.com\.cn\/s\/blog_[^"]+)"/g) || [];
            return matches
              .map(m => {
                const url = m.match(/href="([^"]+)"/)[1].replace('//blog.', 'https://blog.');
                return url;
              })
              .filter(url => {
                if (seen.has(url)) return false;
                seen.add(url);
                return true;
              })
              .map(url => ({ title: '', url: url }));
          } catch (e) {
            console.error('Fetch error:', e.message);
            return [];
          }
        }, { pageNum: page, uid: this.uid });
      }

      console.log(`Page ${page}: ${articles.length} articles`);
      return articles;
    } catch (error) {
      console.error(`Error getting page ${page}:`, error.message);
      return [];
    }
  }

  async close() {
    if (this.browser) {
      await this.browser.close();
      this.browser = null;
      this.context = null;
      this.page = null;
    }
  }

  async getArticleContent(url) {
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    });
    const page = await context.newPage();

    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
      await page.waitForTimeout(2000);

      const data = await page.evaluate(() => {
        const titleEl = document.querySelector('h2.titName') || document.querySelector('h1');
        const title = titleEl ? titleEl.textContent.trim() : '';

        const timeEl = document.querySelector('.time');
        const date = timeEl ? timeEl.textContent.trim() : '';

        const contentEl = document.querySelector('.articalContent') || document.querySelector('.article-content');
        const content = contentEl ? contentEl.textContent.trim() : '';

        const tagEls = document.querySelectorAll('.blog_tag h3, .articalTag h3');
        const tags = Array.from(tagEls).map(t => t.textContent.trim()).filter(t => t);

        const imgs = Array.from(document.querySelectorAll('.articalContent img'))
          .map(img => img.src);

        return { title, content, date, tags, images: imgs };
      });

      await browser.close();
      return { ...data, url };
    } catch (error) {
      await browser.close();
      throw error;
    }
  }

  async *download(maxPages = 100, delayMs = 1500, resume = true) {
    const checkpoint = resume ? this.loadCheckpoint() : { lastPage: 1, lastArticleIndex: -1, downloaded: 0, failedUrls: [] };

    if (resume && checkpoint.downloaded > 0) {
      console.log(`Resuming from page ${checkpoint.lastPage}, article ${checkpoint.lastArticleIndex + 1}`);
    }

    try {
      for (let page = checkpoint.lastPage; page <= maxPages; page++) {
        const articles = await this.getArticleList(page);
        if (!articles || articles.length === 0) {
          console.log(`No more articles at page ${page}`);
          break;
        }

        const startIndex = page === checkpoint.lastPage ? checkpoint.lastArticleIndex + 1 : 0;

        for (let i = startIndex; i < articles.length; i++) {
          const article = articles[i];

          // Skip if already in failed URLs list
          if (checkpoint.failedUrls.some(f => f.url === article.url)) {
            console.log(`Skipping failed URL: ${article.url}`);
            continue;
          }

          try {
            const detail = await this.getArticleContent(article.url);
            const result = { ...article, ...detail };

            // Save checkpoint after each successful download
            this.saveCheckpoint({
              lastPage: page,
              lastArticleIndex: i,
              downloaded: checkpoint.downloaded + 1,
              failedUrls: checkpoint.failedUrls
            });

            yield result;
            await new Promise(r => setTimeout(r, delayMs));
          } catch (e) {
            console.error(`Error fetching ${article.url}: ${e.message}`);
            this.markFailed(article.url, e);
          }
        }
      }
    } finally {
      await this.close();
    }
  }
}

module.exports = { SinaSpider };

// CLI
if (require.main === module) {
  const args = process.argv.slice(2);
  const uid = args.find(a => a.startsWith('--uid='))?.split('=')[1] || '1300871220';
  const outputDir = args.find(a => a.startsWith('--output='))?.split('=')[1] || '/tmp/sina_download';
  const maxPages = parseInt(args.find(a => a.startsWith('--max-pages='))?.split('=')[1] || '10');
  const delay = parseInt(args.find(a => a.startsWith('--delay='))?.split('=')[1] || '1500');
  const resume = !args.includes('--no-resume');
  const listOnly = args.includes('--list-only');
  const testPages = args.find(a => a.startsWith('--test-pages='))?.split('=')[1] || '1';

  (async () => {
    const spider = new SinaSpider(uid, outputDir);

    if (listOnly) {
      const articles = await spider.getArticleList(parseInt(testPages));
      console.log(JSON.stringify(articles));
    } else {
      // Test pagination
      for (let p = 1; p <= parseInt(testPages); p++) {
        const articles = await spider.getArticleList(p);
        console.log(`Page ${p}: ${articles.length} articles`);
      }
    }

    await spider.close();
    process.exit(0);
  })();
}
