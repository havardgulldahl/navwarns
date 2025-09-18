#!/usr/bin/env python3
import os
import re
import sys
import time
import math
import logging
from typing import List, Tuple
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
import datetime

"""
Downloader for NSR NAVAREA paginated pages.

Assumptions from provided HTML:
- Pages follow filenames like NAVAREA_page1.htm, NAVAREA_page2.htm, NAVAREA_page3.htm.
- There is a pager section listing pages and Next/Last links.
- We can start from page 1 and discover the total number of pages via the pager.

Configure BASE_URL and START_PATH below as needed.
"""

# ---------------- Configuration ----------------
BASE_URL = "https://nsr.rosatom.ru/en/navigational-and-weather-information/navarea/"
START_PATH = ""  # seed page path relative to BASE_URL
OUT_DIR = f"../history/{datetime.datetime.now().strftime('%Y')}/NAVAREAXX"  # output directory for downloaded pages
REQUEST_TIMEOUT = 20  # seconds
MAX_RETRIES = 4
RETRY_BACKOFF = 2.0  # exponential backoff factor
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36"
}
# ------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

session = requests.Session()
session.headers.update(HEADERS)


def fetch(url: str) -> requests.Response:
    """Fetch URL with retries."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            if 200 <= resp.status_code < 300:
                return resp
            else:
                logging.warning("HTTP %s for %s", resp.status_code, url)
        except requests.RequestException as e:
            logging.warning(
                "Request error on %s (attempt %d/%d): %s", url, attempt, MAX_RETRIES, e
            )
        if attempt < MAX_RETRIES:
            sleep_s = (RETRY_BACKOFF ** (attempt - 1)) + (0.1 * attempt)
            time.sleep(sleep_s)
    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts")


def save_content(content: bytes, filename: str):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, filename)
    with open(path, "wb") as f:
        f.write(content)
    logging.info("Saved %s", path)


def extract_page_number_from_filename(name: str) -> int | None:
    """
    Extract page number from filenames like NAVAREA_page3.htm.
    """
    m = re.search(r"NAVAREA_page(\d+)\.htm$", name)
    if m:
        return int(m.group(1))
    return None


def get_pager(soup: BeautifulSoup) -> List[Tuple[int, str]] | None:
    """
    Given a BeautifulSoup of the page, infer the max page number shown in the pager.
    We look for a block containing the pager (e.g., 'First | Prev. | 1 2 3 | Next | Last').
    Strategy:
      - Find all anchors whose text is purely a number, collect integers.
      - Return max of those numbers.
    """
    # Find all links and capture those that look like page numbers
    page_nums = []
    for a in soup.find_all("a"):
        text = (a.get_text() or "").strip()
        if re.fullmatch(r"\d+", text):
            try:
                page_nums.append((int(text), a.get("href")))
            except ValueError:
                pass
    print(f"Discovered page numbers in pager: {page_nums}")
    return page_nums or None


def discover_all_page_urls(seed_url: str, seed_content: bytes) -> list[str]:
    """
    Parse the seed page to discover total number of pages and construct URLs.
    If we cannot determine last page from pager, we will still at least return the seed.
    """
    soup = BeautifulSoup(seed_content, "html.parser")
    pages = get_pager(soup)
    if not pages:
        logging.warning("Could not determine last page from pager; defaulting to 1.")
        sys.exit(1)

    # Construct URLs NAVAREA_page{n}.htm for n=1..last_page
    urls = []
    for pageno, url in pages:
        urls.append(urljoin(BASE_URL, url))
    return urls


def filename_from_url(url: str) -> str:
    stub = urlparse(url).query or "PAGEN_1=1"
    return (
        datetime.datetime.now().strftime("ROSATOM_%Y-%m-%d_")
        + stub.replace("=", "_").replace("&", "_")
        + ".html"
    )


def extract_navwarns_from_html(html: bytes) -> List[str]:
    """
    Extract individual navwarns from the HTML content.
    """
    soup = BeautifulSoup(html, "html.parser")
    navwarns = []
    # Example: assuming navwarns are in <p class="otherclass generic-class news-item">...</p>
    for div in soup.find_all("p", class_=re.compile(r"\bnews-item\b")):
        text = div.get_text(strip=True)
        # Only include non-empty navwarns; skip empty strings
        if text:
            navwarns.append(text)
    return navwarns


def main():
    seed_url = urljoin(BASE_URL, START_PATH)
    logging.info("Seed URL: %s", seed_url)

    # Fetch seed page
    resp = fetch(seed_url)
    seed_html = resp.content
    # Save seed page as-is
    save_content(seed_html, filename_from_url(seed_url))

    # Discover all page URLs via pager numbers
    page_urls = discover_all_page_urls(seed_url, seed_html)
    logging.info("Discovered %d page(s): %s", len(page_urls), ", ".join(page_urls))

    # Ensure we include the seed in the list (avoid duplicates)
    page_urls = list(dict.fromkeys(page_urls))  # de-duplicate preserving order

    navwarns = []
    # Download each page
    for url in page_urls:
        # Already saved seed; skip refetch if the same URL
        if url == seed_url:
            continue
        try:
            resp = fetch(url)
            save_content(resp.content, filename_from_url(url))
            navwarns.extend(extract_navwarns_from_html(resp.content))
        except Exception as e:
            logging.error("Failed to download %s: %s", url, e)

    logging.info("Done. Files saved in: %s", os.path.abspath(OUT_DIR))

    # Save navwarns to a file
    if navwarns:
        navwarns_file = os.path.join(
            OUT_DIR, datetime.datetime.now().date().isoformat(), "navwarns.txt"
        )
        os.makedirs(os.path.dirname(navwarns_file), exist_ok=True)
        with open(navwarns_file, "w", encoding="utf-8") as f:
            for nw in navwarns:
                f.write(nw + "\n\n")
        logging.info("Extracted %d navwarns to %s", len(navwarns), navwarns_file)
    else:
        logging.info("No navwarns extracted.")


if __name__ == "__main__":
    # Allow optional overrides from command line:
    # python download_navarea_pages.py https://.../navarea/ NAVAREA_page1.htm
    if len(sys.argv) >= 2:
        BASE_URL = sys.argv[1]
        if not BASE_URL.endswith("/"):
            BASE_URL += "/"
    if len(sys.argv) >= 3:
        START_PATH = sys.argv[2]
    main()
