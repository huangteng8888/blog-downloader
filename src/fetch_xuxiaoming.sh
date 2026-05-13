#!/bin/bash
# Fast article lister for 徐小明 using curl (bypasses 418 with Referer header)
UID="1300871220"
BASE="/home/ht/github/blog-downloader"
INDEX="$BASE/output/$UID/posts/index.json"
DIR="$BASE/output/$UID/posts"

mkdir -p "$DIR"

# Load existing URLs
if [ -f "$INDEX" ]; then
  EXISTING=$(python3 -c "import json,sys; d=json.load(open('$INDEX')); print(len(d.get('posts',[])))" 2>/dev/null || echo "0")
else
  EXISTING=0
fi
echo "[徐小明] Existing: $EXISTING posts"

# Fetch one page and count articles
fetch_page() {
  local page=$1
  curl -s "https://blog.sina.com.cn/s/articlelist_${UID}_0_${page}.html" \
    -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36" \
    -H "Referer: https://blog.sina.com.cn/" \
    -H "Accept-Language: zh-CN,zh;q=0.9" \
    --max-time 15
}

# Extract articles from HTML
extract() {
  local html="$1"
  # Use grep -oP for PCRE regex to extract blog URLs and titles
  echo "$html" | grep -oP 'href="//blog\.sina\.com\.cn/s/blog_[0-9a-z]+\.html"[^>]*>\K[^<]+' | \
    while read title; do
      echo "$title"
    done
}

# Get article count on a page
count_page() {
  local html=$(fetch_page $1)
  local count=$(echo "$html" | grep -c 'href="//blog\.sina\.com\.cn/s/blog_' || echo "0")
  echo "$count"
}

# Detect last page
detect_last_page() {
  local html=$(fetch_page 1)
  local last=$(echo "$html" | grep -oP 'page=\K\d+(?=["&'\''])' | sort -n | tail -1)
  echo "${last:-0}"
}

# Check if first page works
FIRST_HTML=$(fetch_page 1)
if echo "$FIRST_HTML" | grep -q 'blog_4d89b8340103029i'; then
  echo "[徐小明] Page 1 accessible, articles found"
else
  echo "[徐小明] Page 1 FAILED - check curl access"
  echo "$FIRST_HTML" | head -c 500
  exit 1
fi

LAST_PAGE=$(detect_last_page <<< "$FIRST_HTML")
echo "[徐小明] Last page detected: $LAST_PAGE"

# Build temp file for new articles
TEMP_URLS=$(mktemp)
TEMP_TITLES=$(mktemp)

# Process pages
START_PAGE=1
PAGE=$START_PAGE
while [ $PAGE -le ${LAST_PAGE:-300} ]; do
  HTML=$(fetch_page $PAGE)
  COUNT=$(echo "$HTML" | grep -c 'href="//blog\.sina\.com\.cn/s/blog_' || echo "0")
  echo "[徐小明] Page $PAGE: $COUNT articles"
  
  if [ "$COUNT" -eq 0 ]; then
    echo "[徐小明] Empty page, stopping at $PAGE"
    break
  fi
  
  # Extract URLs
  echo "$HTML" | grep -oP 'href="//blog\.sina\.com\.cn/s/blog_[0-9a-z]+\.html"' | \
    sed 's|href="//blog.sina.com.cn||' | sed 's|"||g' >> "$TEMP_URLS"
  
  # Extract titles (corresponding order)
  echo "$HTML" | grep -oP 'href="//blog\.sina\.com\.cn/s/blog_[0-9a-z]+\.html"[^>]*>\K[^<]+' >> "$TEMP_TITLES"
  
  # Save checkpoint every 10 pages
  if [ $((PAGE % 10)) -eq 0 ]; then
    echo "[徐小明] Checkpoint at page $PAGE"
    # TODO: merge with existing index
  fi
  
  sleep 0.5
  PAGE=$((PAGE + 1))
done

URL_COUNT=$(wc -l < "$TEMP_URLS")
TITLE_COUNT=$(wc -l < "$TEMP_TITLES")
echo "[徐小明] Total extracted: $URL_COUNT URLs, $TITLE_COUNT titles"

# Merge with existing index.json
python3 << EOF
import json, os
from datetime import datetime

index_path = '$INDEX'
uid = '$UID'
urls_file = '$TEMP_URLS'
titles_file = '$TEMP_TITLES'

# Load existing
existing = {'total': 0, 'updated_at': '', 'posts': []}
if os.path.exists(index_path):
    try:
        existing = json.load(open(index_path))
    except: pass

existing_urls = {p['source_url'] for p in existing['posts']}

# Read new
with open(urls_file) as f:
    urls = [l.strip() for l in f]
with open(titles_file) as f:
    titles = [l.strip() for l in f]

# Merge
new_posts = list(existing['posts'])
for i, url in enumerate(urls):
    full_url = 'https://blog.sina.com.cn' + url
    if full_url not in existing_urls:
        import hashlib
        blog_id = url.replace('/s/blog_', '').replace('.html', '')
        title = titles[i] if i < len(titles) else blog_id
        new_posts.append({
            'id': blog_id,
            'title': title,
            'published_at': '',
            'published_date': '',
            'tags': [],
            'images_count': 0,
            'filename': blog_id + '.md',
            'source_url': full_url,
            'indexed_at': datetime.now().isoformat(),
        })

# Save
result = {'total': len(new_posts), 'updated_at': datetime.now().isoformat(), 'posts': new_posts}
with open(index_path, 'w') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"[徐小明] Saved index: {len(new_posts)} posts ({len(new_posts) - len(existing['posts'])} new)")
EOF

rm -f "$TEMP_URLS" "$TEMP_TITLES"
echo "[徐小明] Done listing"
