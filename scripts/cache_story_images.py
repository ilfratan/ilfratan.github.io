#!/usr/bin/env python3
"""Cache external article thumbnails as local WebP images for GitHub Pages."""
import hashlib
import html
import io
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps, UnidentifiedImageError

PAGE = Path("index.html")
OUT = Path("assets/covers")
OUT.mkdir(parents=True, exist_ok=True)
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
})
card_re = re.compile(r'<article class="story"[\s\S]*?<\/article>')
img_re = re.compile(r'<img\b[^>]*\bclass="story-cover"[^>]*>', re.I)
src_re = re.compile(r'\bsrc="([^"]+)"')

def fetch_image(url, referer):
    if not url.startswith("https://"):
        return None
    try:
        response = session.get(
            url, timeout=(10, 25),
            headers={"Referer": referer, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"},
        )
        response.raise_for_status()
        if len(response.content) > 12_000_000:
            return None
        with Image.open(io.BytesIO(response.content)) as original:
            original.load()
            image = ImageOps.exif_transpose(original).convert("RGB")
            if image.width < 250 or image.height < 150:
                return None
            image.thumbnail((1150, 900), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=83, method=5)
            return output.getvalue()
    except (requests.RequestException, UnidentifiedImageError, OSError, ValueError) as e:
        print("  - No disponible:", url[:140], type(e).__name__)
        return None

def article_candidates(url):
    try:
        response = session.get(url, timeout=(10, 25), headers={"Accept": "text/html"})
        response.raise_for_status()
    except requests.RequestException:
        return []
    soup = BeautifulSoup(response.text, "html.parser")
    candidates = []
    for attrs in (
        {"property": "og:image"},
        {"name": "twitter:image"},
        {"name": "twitter:image:src"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            candidates.append(urljoin(url, tag["content"]))
    # Only images from the actual article; skip tiny avatars and logos.
    article = soup.find("article") or soup.find("main")
    if article:
        for img in article.find_all("img", limit=6):
            for prop in ("data-src", "src", "data-lazy-src"):
                src = img.get(prop)
                if src:
                    candidates.append(urljoin(url, src))
    return candidates

def proxy_url(url):
    parsed = urlparse(url)
    if parsed.hostname == "es.rollingstone.com" and parsed.path.startswith("/wp-content/uploads/"):
        return "https://i0.wp.com/" + parsed.hostname + parsed.path
    return ""

def cache(article_url, src):
    article_slug = urlparse(article_url).path.strip("/").split("/")[-1]
    slug = re.sub(r'[^a-zA-Z0-9-]', '-', article_slug)[:62].strip("-") or "nota"
    digest = hashlib.sha256(article_url.encode()).hexdigest()[:9]
    path = OUT / (slug + "-" + digest + ".webp")
    if path.is_file() and path.stat().st_size > 500:
        return "./" + path.as_posix()
    candidates = [src]
    alternate = proxy_url(src)
    if alternate:
        candidates.append(alternate)
    candidates.extend(article_candidates(article_url))
    tested = set()
    for candidate in candidates:
        if candidate in tested:
            continue
        tested.add(candidate)
        data = fetch_image(candidate, article_url)
        if data:
            path.write_bytes(data)
            print("  + Guardada:", str(path), len(data), "bytes")
            return "./" + path.as_posix()
    return None

original = PAGE.read_text(encoding="utf-8")
success = failed = skipped = 0

def process_card(match):
    global success, failed, skipped
    card = match.group(0)
    tag_match = img_re.search(card)
    article_match = re.search(r'<a\b[^>]*\bhref="(https?://[^"]+)"', card)
    if not tag_match or not article_match:
        skipped += 1
        return card
    tag = tag_match.group(0)
    src_match = src_re.search(tag)
    if not src_match:
        skipped += 1
        return card
    src = html.unescape(src_match.group(1))
    if not src.startswith("http"):
        skipped += 1
        return card
    article_url = html.unescape(article_match.group(1))
    print("Procesando:", article_url)
    local = cache(article_url, src)
    if not local:
        failed += 1
        print("  ! Sin imagen descargable; se mantiene original")
        return card
    new_tag = tag[:src_match.start(1)] + local + tag[src_match.end(1):]
    success += 1
    return card.replace(tag, new_tag, 1)

updated = card_re.sub(process_card, original)
if updated != original:
    PAGE.write_text(updated, encoding="utf-8")
print(f"Imágenes guardadas: {success}; fallidas: {failed}; ya locales/omitas: {skipped}")
