#!/usr/bin/env python3
"""
Crawl4AI Web App — A Streamlit UI for crawling websites with Crawl4AI.
Supports single-page crawl, deep/site crawl, and HTTP fallback mode.
"""

import asyncio
import os
import re
import json
import time
import zipfile
import io
from datetime import datetime
from urllib.parse import urlparse, urljoin

import streamlit as st
import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md_convert

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Crawl4AI Web App",
    page_icon="🕷️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------
if "crawl_history" not in st.session_state:
    st.session_state.crawl_history = []
if "crawl_results" not in st.session_state:
    st.session_state.crawl_results = {}
if "current_result" not in st.session_state:
    st.session_state.current_result = None

# ---------------------------------------------------------------------------
# Detect Crawl4AI availability
# ---------------------------------------------------------------------------
@st.cache_data
def check_crawl4ai():
    """Check if Crawl4AI browser mode is available."""
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig
        # Try to actually launch a browser briefly
        async def _test():
            config = BrowserConfig(headless=True, verbose=False)
            async with AsyncWebCrawler(config=config) as crawler:
                result = await crawler.arun(
                    url="https://httpbin.org/get",
                    config=__import__("crawl4ai").CrawlerRunConfig(
                        word_count_threshold=1, verbose=False
                    ),
                )
                return result.success
        result = asyncio.run(_test())
        return True, result
    except Exception as e:
        return False, str(e)

# Try crawl4ai availability check (but don't block the app)
try:
    CRAWL4AI_AVAILABLE, CRAWL4AI_MSG = check_crawl4ai()
except Exception:
    CRAWL4AI_AVAILABLE = False
    CRAWL4AI_MSG = "Check failed"

HTTP_FALLBACK_AVAILABLE = True  # httpx + markdownify always available


# ---------------------------------------------------------------------------
# Crawl4AI Browser-based crawling
# ---------------------------------------------------------------------------

async def crawl_single_page_c4ai(url, opts):
    """Crawl a single page using Crawl4AI with full configuration."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    from crawl4ai.content_filter_strategy import PruningContentFilter, BM25ContentFilter

    # Build browser config
    browser_config = BrowserConfig(
        headless=opts.get("headless", True),
        browser_type=opts.get("browser_type", "chromium"),
        verbose=opts.get("verbose", False),
        java_script_enabled=opts.get("java_script_enabled", True),
        text_mode=opts.get("text_mode", False),
        light_mode=opts.get("light_mode", False),
        ignore_https_errors=opts.get("ignore_https_errors", True),
        viewport_width=opts.get("viewport_width", 1080),
        viewport_height=opts.get("viewport_height", 600),
        user_agent=opts.get("user_agent") or None,
        proxy=opts.get("proxy") or None,
        user_agent_mode=opts.get("user_agent_mode") or None,
    )

    # Build content filter for fit_markdown
    content_filter = None
    if opts.get("markdown_format") == "fit_markdown":
        filter_type = opts.get("content_filter_type", "pruning")
        if filter_type == "pruning":
            content_filter = PruningContentFilter(
                user_query=opts.get("user_query") or None,
                min_word_threshold=opts.get("min_word_threshold") or None,
                threshold_type=opts.get("threshold_type", "fixed"),
                threshold=opts.get("pruning_threshold", 0.48),
            )
        elif filter_type == "bm25":
            content_filter = BM25ContentFilter(
                user_query=opts.get("user_query") or None,
                bm25_threshold=opts.get("bm25_threshold", 1.0),
            )

    # Build markdown generator
    md_generator = DefaultMarkdownGenerator(
        content_filter=content_filter,
        content_source=opts.get("content_source", "cleaned_html"),
    )

    # Determine cache mode
    cache_mode_map = {
        "ENABLED": CacheMode.ENABLED,
        "DISABLED": CacheMode.DISABLED,
        "READ_ONLY": CacheMode.READ_ONLY,
        "WRITE_ONLY": CacheMode.WRITE_ONLY,
        "BYPASS": CacheMode.BYPASS,
    }

    # Build crawler run config
    run_config = CrawlerRunConfig(
        word_count_threshold=opts.get("word_count_threshold", 200),
        markdown_generator=md_generator,
        css_selector=opts.get("css_selector") or None,
        excluded_tags=opts.get("excluded_tags") or None,
        excluded_selector=opts.get("excluded_selector") or None,
        cache_mode=cache_mode_map.get(opts.get("cache_mode", "BYPASS"), CacheMode.BYPASS),
        wait_until=opts.get("wait_until", "domcontentloaded"),
        page_timeout=opts.get("page_timeout", 60000),
        wait_for=opts.get("wait_for") or None,
        delay_before_return_html=opts.get("delay_before_return_html", 0.1),
        js_code=opts.get("js_code") or None,
        screenshot=opts.get("screenshot", False),
        pdf=opts.get("pdf", False),
        scan_full_page=opts.get("scan_full_page", False),
        scroll_delay=opts.get("scroll_delay", 0.2),
        process_iframes=opts.get("process_iframes", False),
        remove_overlay_elements=opts.get("remove_overlay_elements", False),
        simulate_user=opts.get("simulate_user", False),
        override_navigator=opts.get("override_navigator", False),
        magic=opts.get("magic", False),
        exclude_all_images=opts.get("exclude_all_images", False),
        exclude_external_images=opts.get("exclude_external_images", False),
        exclude_external_links=opts.get("exclude_external_links", False),
        exclude_social_media_links=opts.get("exclude_social_media_links", False),
        verbose=opts.get("verbose", False),
        log_console=opts.get("log_console", False),
        only_text=opts.get("only_text", False),
        prettiify=opts.get("prettiify", False),
        remove_forms=opts.get("remove_forms", False),
        check_robots_txt=opts.get("check_robots_txt", False),
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=url, config=run_config)
        return result


async def crawl_deep_site_c4ai(url, opts, progress_callback=None):
    """Deep crawl a website using Crawl4AI BFS strategy."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    from crawl4ai.content_filter_strategy import PruningContentFilter, BM25ContentFilter
    from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
    from crawl4ai.deep_crawling.filters import (
        FilterChain, DomainFilter, ContentTypeFilter,
        ContentRelevanceFilter, SEOFilter, URLPatternFilter,
    )

    # Build browser config
    browser_config = BrowserConfig(
        headless=opts.get("headless", True),
        browser_type=opts.get("browser_type", "chromium"),
        verbose=False,
        java_script_enabled=opts.get("java_script_enabled", True),
        text_mode=opts.get("text_mode", False),
        ignore_https_errors=opts.get("ignore_https_errors", True),
    )

    # Build filter chain
    filters = []

    # Domain filter
    allowed_domains = opts.get("allowed_domains")
    blocked_domains = opts.get("blocked_domains")
    if allowed_domains or blocked_domains:
        domains_allowed = [d.strip() for d in allowed_domains.split(",")] if allowed_domains else None
        domains_blocked = [d.strip() for d in blocked_domains.split(",")] if blocked_domains else None
        filters.append(DomainFilter(
            allowed_domains=domains_allowed,
            blocked_domains=domains_blocked,
        ))

    # URL pattern filter
    url_patterns = opts.get("url_patterns")
    if url_patterns:
        patterns = [p.strip() for p in url_patterns.split(",")]
        filters.append(URLPatternFilter(patterns=patterns))

    # Content type filter
    content_types = opts.get("content_types")
    if content_types:
        types = [t.strip() for t in content_types.split(",")]
        filters.append(ContentTypeFilter(allowed_types=types))

    filter_chain = FilterChain(filters) if filters else FilterChain()

    # Build BFS strategy
    bfs_strategy = BFSDeepCrawlStrategy(
        max_depth=opts.get("max_depth", 2),
        max_pages=opts.get("max_pages", 50),
        filter_chain=filter_chain,
        include_external=opts.get("include_external", False),
    )

    # Build content filter for markdown
    content_filter = None
    if opts.get("markdown_format") == "fit_markdown":
        filter_type = opts.get("content_filter_type", "pruning")
        if filter_type == "pruning":
            content_filter = PruningContentFilter(
                user_query=opts.get("user_query") or None,
                threshold=opts.get("pruning_threshold", 0.48),
            )
        elif filter_type == "bm25":
            content_filter = BM25ContentFilter(
                user_query=opts.get("user_query") or None,
                bm25_threshold=opts.get("bm25_threshold", 1.0),
            )

    md_generator = DefaultMarkdownGenerator(content_filter=content_filter)

    cache_mode_map = {
        "ENABLED": CacheMode.ENABLED,
        "DISABLED": CacheMode.DISABLED,
        "READ_ONLY": CacheMode.READ_ONLY,
        "WRITE_ONLY": CacheMode.WRITE_ONLY,
        "BYPASS": CacheMode.BYPASS,
    }

    run_config = CrawlerRunConfig(
        word_count_threshold=opts.get("word_count_threshold", 200),
        markdown_generator=md_generator,
        deep_crawl_strategy=bfs_strategy,
        cache_mode=cache_mode_map.get(opts.get("cache_mode", "BYPASS"), CacheMode.BYPASS),
        wait_until=opts.get("wait_until", "domcontentloaded"),
        page_timeout=opts.get("page_timeout", 60000),
        delay_before_return_html=opts.get("delay_before_return_html", 0.1),
        scan_full_page=opts.get("scan_full_page", False),
        process_iframes=opts.get("process_iframes", False),
        remove_overlay_elements=opts.get("remove_overlay_elements", False),
        exclude_all_images=opts.get("exclude_all_images", True),
        exclude_external_links=opts.get("exclude_external_links", True),
        verbose=False,
        stream=True,  # Stream results for progress
    )

    results = []
    async with AsyncWebCrawler(config=browser_config) as crawler:
        async for result in await crawler.arun(url=url, config=run_config):
            results.append(result)
            if progress_callback:
                progress_callback(len(results), opts.get("max_pages", 50), result.url)
        return results


# ---------------------------------------------------------------------------
# HTTP Fallback crawling (no browser needed)
# ---------------------------------------------------------------------------

async def crawl_single_page_http(url, opts):
    """Crawl a single page using httpx + markdownify (no browser needed)."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(opts.get("page_timeout", 30000) / 1000),
        headers={"User-Agent": opts.get("user_agent") or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        follow_redirects=True,
        verify=not opts.get("ignore_https_errors", True),
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        html = resp.text

    soup = BeautifulSoup(html, "lxml")

    # Extract title
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "Untitled"

    # Remove non-content elements
    for selector in ["nav", "footer", "script", "style", "noscript", "header",
                      ".navbar", ".site-header", ".site-footer", ".sidebar",
                      ".breadcrumb", ".search", "iframe", ".ads", ".google-analytics"]:
        for elem in soup.select(selector):
            elem.decompose()

    # CSS selector support
    css_selector = opts.get("css_selector")
    if css_selector:
        content = soup.select_one(css_selector)
        content = content if content else soup.find("body") or soup
    else:
        # Find main content
        content = None
        for sel in ["main", "article", ".content", ".main-content", "#content", ".page-content"]:
            content = soup.select_one(sel)
            if content:
                break
        if not content:
            content = soup.find("body") or soup

    # Excluded tags
    excluded_tags = opts.get("excluded_tags")
    if excluded_tags:
        for tag_name in [t.strip() for t in excluded_tags.split(",")]:
            for elem in content.find_all(tag_name):
                elem.decompose()

    # Excluded selector
    excluded_selector = opts.get("excluded_selector")
    if excluded_selector:
        for elem in content.select(excluded_selector):
            elem.decompose()

    # Convert to markdown
    if opts.get("exclude_all_images"):
        for img in content.find_all("img"):
            img.decompose()

    markdown_text = md_convert(str(content), heading_style="ATX")

    # Clean up
    markdown_text = re.sub(r"\n{3,}", "\n\n", markdown_text)

    final_markdown = f"# {title}\n\n{markdown_text.strip()}\n"

    # Build a result-like object
    class HTTPResult:
        def __init__(self):
            self.url = url
            self.success = True
            self.html = html
            self.cleaned_html = str(content)
            self.markdown = final_markdown
            self.fit_markdown = final_markdown
            self.title = title
            self.status_code = resp.status_code
            self.media = {"images": [], "videos": []}
            self.links = {"internal": [], "external": []}
            self.screenshot = None
            self.pdf = None
            self.error_message = None
            self.metadata = {"title": title}

    return HTTPResult()


async def crawl_deep_site_http(url, opts, progress_callback=None):
    """Deep crawl using sitemap + httpx (no browser needed)."""
    base_domain = urlparse(url).netloc
    max_pages = opts.get("max_pages", 50)
    max_depth = opts.get("max_depth", 3)

    # Try sitemap first
    sitemap_url = urljoin(url, "/sitemap.xml")
    urls_to_crawl = []

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30),
        headers={"User-Agent": "Mozilla/5.0"},
        follow_redirects=True,
    ) as client:
        try:
            resp = await client.get(sitemap_url)
            if resp.status_code == 200 and "<loc>" in resp.text:
                urls_to_crawl = re.findall(r"<loc>(.*?)</loc>", resp.text)
                urls_to_crawl = [u.strip() for u in urls_to_crawl if base_domain in urlparse(u).netloc]
        except Exception:
            pass

        # If no sitemap, try to discover links from the starting page
        if not urls_to_crawl:
            urls_to_crawl = [url]
            try:
                resp = await client.get(url)
                soup = BeautifulSoup(resp.text, "lxml")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if href.startswith("/"):
                        href = urljoin(url, href)
                    if href.startswith("http") and base_domain in urlparse(href).netloc:
                        if href not in urls_to_crawl:
                            urls_to_crawl.append(href)
            except Exception:
                pass

        # Apply URL pattern filter
        url_patterns = opts.get("url_patterns")
        if url_patterns:
            patterns = [p.strip() for p in url_patterns.split(",")]
            filtered = []
            for u in urls_to_crawl:
                for pat in patterns:
                    import fnmatch
                    if fnmatch.fnmatch(u, pat):
                        filtered.append(u)
                        break
            urls_to_crawl = filtered if filtered else urls_to_crawl

        # Limit pages
        urls_to_crawl = urls_to_crawl[:max_pages]

        # Crawl each page
        results = []
        semaphore = asyncio.Semaphore(opts.get("concurrency", 5))

        for i, page_url in enumerate(urls_to_crawl):
            async with semaphore:
                try:
                    page_opts = dict(opts)
                    page_opts["page_timeout"] = 30000
                    result = await crawl_single_page_http(page_url, page_opts)
                    results.append(result)
                    if progress_callback:
                        progress_callback(len(results), len(urls_to_crawl), page_url)
                except Exception as e:
                    # Add error result
                    class ErrorResult:
                        def __init__(self, u, err):
                            self.url = u
                            self.success = False
                            self.error_message = str(err)
                            self.markdown = f"**Error crawling {u}: {err}**"
                            self.fit_markdown = self.markdown
                            self.html = ""
                            self.cleaned_html = ""
                            self.title = "Error"
                            self.status_code = None
                            self.media = {}
                            self.links = {}
                            self.screenshot = None
                            self.pdf = None
                            self.metadata = {}
                    results.append(ErrorResult(page_url, e))

        return results


# ---------------------------------------------------------------------------
# UI Components
# ---------------------------------------------------------------------------

def render_sidebar():
    """Render the sidebar with mode selection and global options."""
    with st.sidebar:
        st.markdown("## 🕷️ Crawl4AI Web App")
        st.caption("Crawl websites and convert to LLM-optimized markdown")

        # Engine selection
        st.markdown("### Crawl Engine")
        if CRAWL4AI_AVAILABLE:
            engine = st.radio(
                "Engine",
                ["Crawl4AI (Browser)", "HTTP Fallback"],
                help="Crawl4AI uses a headless browser for JS rendering. HTTP Fallback is faster but doesn't execute JS.",
            )
        else:
            engine = "HTTP Fallback"
            st.warning(
                "⚠️ Crawl4AI browser mode unavailable.\n\n"
                f"Reason: {CRAWL4AI_MSG[:100]}\n\n"
                "Using HTTP Fallback mode (httpx + markdownify).\n"
                "Install Playwright browser deps to enable full Crawl4AI."
            )

        st.divider()

        # Crawl mode
        st.markdown("### Crawl Mode")
        mode = st.radio(
            "Mode",
            ["Single Page", "Deep Crawl (Site)"],
            help="Single Page: crawl one URL. Deep Crawl: recursively crawl an entire site."
        )

        st.divider()

        # Output directory
        output_dir = st.text_input(
            "Output Directory",
            value="/workspace/BSL/crawl4ai-output",
            help="Where to save markdown files"
        )
        os.makedirs(output_dir, exist_ok=True)

        st.divider()

        # History
        if st.session_state.crawl_history:
            st.markdown("### Recent Crawls")
            for entry in reversed(st.session_state.crawl_history[-5:]):
                st.caption(f" `{entry['time']}` — {entry['mode']} — {entry['pages']} pages")

        return engine, mode, output_dir


def render_single_page_config(engine):
    """Render configuration panel for single page crawl."""
    st.markdown("### ⚙️ Configuration")

    col1, col2 = st.columns(2)

    with col1:
        url = st.text_input("🌐 URL to Crawl", placeholder="https://example.com")

        markdown_format = st.selectbox(
            "Markdown Format",
            ["fit_markdown", "raw_markdown"],
            help="fit_markdown: Smart extraction, removes noise. raw_markdown: Full page content."
        )

        css_selector = st.text_input(
            "CSS Selector (optional)",
            placeholder="main, article, .content",
            help="Extract only this part of the page"
        )

        excluded_selector = st.text_input(
            "Excluded CSS Selector (optional)",
            placeholder=".sidebar, .ads, nav",
            help="Remove these elements before conversion"
        )

        excluded_tags = st.text_input(
            "Excluded HTML Tags (optional)",
            placeholder="nav, footer, aside",
            help="Comma-separated tag names to remove"
        )

        word_count_threshold = st.number_input(
            "Word Count Threshold", min_value=1, max_value=5000, value=200,
            help="Minimum words to include a section"
        )

    with col2:
        wait_until = st.selectbox(
            "Wait Until",
            ["domcontentloaded", "load", "networkidle"],
            help="When to consider the page loaded"
        )

        page_timeout = st.number_input(
            "Page Timeout (ms)", min_value=5000, max_value=120000, value=60000, step=5000
        )

        delay_before_return = st.number_input(
            "Delay Before Return (s)", min_value=0.0, max_value=30.0, value=0.1, step=0.5
        )

        cache_mode = st.selectbox(
            "Cache Mode",
            ["BYPASS", "ENABLED", "DISABLED", "READ_ONLY", "WRITE_ONLY"]
        )

        user_agent = st.text_input(
            "User Agent (optional)",
            placeholder="Leave empty for default"
        )

    # Advanced options expander
    with st.expander("🔧 Advanced Options"):
        adv_col1, adv_col2, adv_col3 = st.columns(3)

        with adv_col1:
            js_code = st.text_area(
                "JavaScript Code (optional)",
                placeholder="// Execute custom JS before scraping",
                height=100,
            )
            wait_for = st.text_input(
                "Wait For Selector (optional)",
                placeholder=".dynamic-content"
            )

        with adv_col2:
            st.caption("**Content Filtering**")
            exclude_all_images = st.checkbox("Exclude All Images", value=False)
            exclude_external_images = st.checkbox("Exclude External Images", value=False)
            exclude_external_links = st.checkbox("Exclude External Links", value=False)
            exclude_social_media_links = st.checkbox("Exclude Social Media Links", value=False)

        with adv_col3:
            st.caption("**Page Behavior**")
            scan_full_page = st.checkbox("Scan Full Page (scroll)", value=False)
            scroll_delay = st.number_input("Scroll Delay (s)", 0.0, 10.0, 0.2, 0.1)
            process_iframes = st.checkbox("Process iFrames", value=False)
            remove_overlay_elements = st.checkbox("Remove Overlays/Popups", value=False)
            simulate_user = st.checkbox("Simulate User Behavior", value=False)
            magic = st.checkbox("Magic Mode", value=False,
                                help="Auto-apply anti-bot measures")

    # Fit markdown options
    content_filter_type = None
    user_query = None
    pruning_threshold = 0.48
    bm25_threshold = 1.0
    min_word_threshold = None
    threshold_type = "fixed"

    if markdown_format == "fit_markdown":
        with st.expander("🎯 Fit Markdown Options", expanded=True):
            cf_col1, cf_col2 = st.columns(2)
            with cf_col1:
                content_filter_type = st.selectbox(
                    "Content Filter",
                    ["pruning", "bm25"],
                    help="Pruning: score-based content filtering. BM25: keyword-relevance filtering."
                )
                user_query = st.text_input(
                    "User Query (optional)",
                    placeholder="What content are you looking for?",
                    help="Helps the filter prioritize relevant content"
                )
            with cf_col2:
                if content_filter_type == "pruning":
                    pruning_threshold = st.slider(
                        "Pruning Threshold", 0.0, 1.0, 0.48, 0.01,
                        help="Lower = more content, Higher = more filtered"
                    )
                    threshold_type = st.selectbox("Threshold Type", ["fixed", "dynamic", "percentage"])
                    min_word_threshold = st.number_input("Min Words per Section", 0, 500, 0)
                elif content_filter_type == "bm25":
                    bm25_threshold = st.slider(
                        "BM25 Threshold", 0.0, 5.0, 1.0, 0.1,
                        help="Lower = more content, Higher = more filtered"
                    )

    # Build options dict
    opts = {
        "markdown_format": markdown_format,
        "css_selector": css_selector or None,
        "excluded_selector": excluded_selector or None,
        "excluded_tags": excluded_tags or None,
        "word_count_threshold": word_count_threshold,
        "wait_until": wait_until,
        "page_timeout": page_timeout,
        "delay_before_return_html": delay_before_return,
        "cache_mode": cache_mode,
        "user_agent": user_agent or None,
        "js_code": js_code or None,
        "wait_for": wait_for or None,
        "exclude_all_images": exclude_all_images,
        "exclude_external_images": exclude_external_images,
        "exclude_external_links": exclude_external_links,
        "exclude_social_media_links": exclude_social_media_links,
        "scan_full_page": scan_full_page,
        "scroll_delay": scroll_delay,
        "process_iframes": process_iframes,
        "remove_overlay_elements": remove_overlay_elements,
        "simulate_user": simulate_user,
        "magic": magic,
        "content_filter_type": content_filter_type,
        "user_query": user_query or None,
        "pruning_threshold": pruning_threshold,
        "bm25_threshold": bm25_threshold,
        "min_word_threshold": min_word_threshold if min_word_threshold and min_word_threshold > 0 else None,
        "threshold_type": threshold_type,
        "content_source": "cleaned_html",
        "headless": True,
        "verbose": False,
        "browser_type": "chromium",
        "java_script_enabled": True,
        "text_mode": False,
        "light_mode": False,
        "ignore_https_errors": True,
        "viewport_width": 1080,
        "viewport_height": 600,
    }

    return url, opts


def render_deep_crawl_config(engine):
    """Render configuration panel for deep/site crawl."""
    st.markdown("### ⚙️ Deep Crawl Configuration")

    col1, col2 = st.columns(2)

    with col1:
        url = st.text_input("🌐 Starting URL", placeholder="https://docs.example.com")

        max_depth = st.number_input(
            "Max Crawl Depth", min_value=1, max_value=10, value=2,
            help="How deep to follow links from the starting page"
        )

        max_pages = st.number_input(
            "Max Pages", min_value=1, max_value=1000, value=50,
            help="Maximum number of pages to crawl"
        )

        markdown_format = st.selectbox(
            "Markdown Format",
            ["fit_markdown", "raw_markdown"],
            help="fit_markdown: Smart extraction. raw_markdown: Full content."
        )

    with col2:
        cache_mode = st.selectbox(
            "Cache Mode",
            ["BYPASS", "ENABLED", "DISABLED"]
        )

        include_external = st.checkbox(
            "Include External Links",
            value=False,
            help="Follow links to other domains"
        )

        concurrency = st.number_input(
            "Concurrency", min_value=1, max_value=20, value=5,
            help="Number of parallel requests (HTTP mode)"
        )

        word_count_threshold = st.number_input(
            "Word Count Threshold", min_value=1, max_value=5000, value=200
        )

    # Filters
    with st.expander("🔍 URL Filters & Discovery"):
        f_col1, f_col2 = st.columns(2)

        with f_col1:
            allowed_domains = st.text_input(
                "Allowed Domains (comma-separated)",
                placeholder="docs.example.com, api.example.com"
            )
            blocked_domains = st.text_input(
                "Blocked Domains (comma-separated)",
                placeholder="ads.example.com, old.example.com"
            )

        with f_col2:
            url_patterns = st.text_input(
                "URL Patterns (glob, comma-separated)",
                placeholder="*/docs/*, */api/*",
                help="Only crawl URLs matching these patterns"
            )
            content_types = st.text_input(
                "Allowed Content Types (comma-separated)",
                placeholder="text/html, application/json",
                help="Only crawl these content types"
            )

    # Content options
    with st.expander("📄 Content Options"):
        c_col1, c_col2 = st.columns(2)
        with c_col1:
            css_selector = st.text_input("CSS Selector (optional)", placeholder="main, article")
            excluded_tags = st.text_input("Excluded Tags (optional)", placeholder="nav, footer, aside")
            exclude_all_images = st.checkbox("Exclude All Images", value=True)
        with c_col2:
            excluded_selector = st.text_input("Excluded CSS Selector", placeholder=".sidebar, .ads")
            exclude_external_links = st.checkbox("Exclude External Links", value=True)
            exclude_social_media_links = st.checkbox("Exclude Social Media Links", value=True)

    # Fit markdown options
    content_filter_type = "pruning"
    user_query = None
    pruning_threshold = 0.48
    bm25_threshold = 1.0

    if markdown_format == "fit_markdown":
        with st.expander("🎯 Fit Markdown Options"):
            content_filter_type = st.selectbox("Content Filter", ["pruning", "bm25"])
            user_query = st.text_input("User Query (optional)", placeholder="What content are you looking for?")
            if content_filter_type == "pruning":
                pruning_threshold = st.slider("Pruning Threshold", 0.0, 1.0, 0.48, 0.01)
            elif content_filter_type == "bm25":
                bm25_threshold = st.slider("BM25 Threshold", 0.0, 5.0, 1.0, 0.1)

    opts = {
        "max_depth": max_depth,
        "max_pages": max_pages,
        "markdown_format": markdown_format,
        "cache_mode": cache_mode,
        "include_external": include_external,
        "concurrency": concurrency,
        "word_count_threshold": word_count_threshold,
        "allowed_domains": allowed_domains or None,
        "blocked_domains": blocked_domains or None,
        "url_patterns": url_patterns or None,
        "content_types": content_types or None,
        "css_selector": css_selector or None,
        "excluded_tags": excluded_tags or None,
        "excluded_selector": excluded_selector or None,
        "exclude_all_images": exclude_all_images,
        "exclude_external_links": exclude_external_links,
        "exclude_social_media_links": exclude_social_media_links,
        "content_filter_type": content_filter_type,
        "user_query": user_query or None,
        "pruning_threshold": pruning_threshold,
        "bm25_threshold": bm25_threshold,
        "headless": True,
        "browser_type": "chromium",
        "java_script_enabled": True,
        "text_mode": False,
        "ignore_https_errors": True,
        "wait_until": "domcontentloaded",
        "page_timeout": 30000,
        "delay_before_return_html": 0.1,
        "scan_full_page": False,
        "scroll_delay": 0.2,
        "process_iframes": False,
        "remove_overlay_elements": True,
        "verbose": False,
    }

    return url, opts


def render_results(results, output_dir, mode, url):
    """Render crawl results with viewer, download, and save options."""
    if not results:
        return

    st.divider()
    st.markdown("## 📊 Results")

    # Handle single result
    if isinstance(results, dict) or (isinstance(results, list) and len(results) == 1 and not isinstance(results[0], dict)):
        # Normalize to list
        if not isinstance(results, list):
            results = [results]

    # Stats
    total = len(results)
    success = sum(1 for r in results if getattr(r, "success", False))
    failed = total - success

    stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)
    stat_col1.metric("Total Pages", total)
    stat_col2.metric("Succeeded", success)
    stat_col3.metric("Failed", failed)
    total_chars = sum(len(getattr(r, "markdown", "") or "") for r in results)
    stat_col4.metric("Total Characters", f"{total_chars:,}")

    # Result selector
    st.markdown("### Page Viewer")
    page_options = []
    for i, r in enumerate(results):
        title = getattr(r, "title", None) or getattr(r, "url", f"Page {i+1}")
        status = "✅" if getattr(r, "success", False) else "❌"
        page_options.append(f"{status} {i+1}. {title[:60]}")

    selected_idx = st.selectbox(
        "Select Page",
        range(len(page_options)),
        format_func=lambda x: page_options[x]
    )

    result = results[selected_idx]

    # Tabs for different views
    tab1, tab2, tab3, tab4 = st.tabs(["📝 Markdown", "🌐 HTML", "🔗 Links & Media", "ℹ️ Metadata"])

    with tab1:
        markdown_content = getattr(result, "markdown", "") or getattr(result, "fit_markdown", "") or ""
        if markdown_content:
            st.markdown(markdown_content)
        else:
            st.warning("No markdown content available")
            if getattr(result, "error_message", None):
                st.error(f"Error: {result.error_message}")

    with tab2:
        html_content = getattr(result, "cleaned_html", "") or getattr(result, "html", "")
        if html_content:
            st.code(html_content[:50000], language="html")
        else:
            st.info("No HTML content available")

    with tab3:
        media = getattr(result, "media", {}) or {}
        links = getattr(result, "links", {}) or {}

        if media:
            st.markdown("**Media:**")
            for media_type, items in media.items():
                st.markdown(f"- {media_type}: {len(items)} items")
                for item in items[:5]:
                    st.markdown(f"  - {item.get('src', item.get('url', ''))[:100]}")

        if links:
            st.markdown("**Links:**")
            for link_type, items in links.items():
                st.markdown(f"- {link_type}: {len(items)} links")
                for item in items[:10]:
                    href = item.get("href", "")
                    text = item.get("text", "")[:50]
                    st.markdown(f"  - [{text}]({href})")

        if not media and not links:
            st.info("No links or media extracted")

    with tab4:
        meta = {
            "url": getattr(result, "url", ""),
            "title": getattr(result, "title", ""),
            "status_code": getattr(result, "status_code", None),
            "success": getattr(result, "success", None),
            "error_message": getattr(result, "error_message", None),
            "screenshot": "Yes" if getattr(result, "screenshot", None) else "No",
            "pdf": "Yes" if getattr(result, "pdf", None) else "No",
        }
        metadata = getattr(result, "metadata", {}) or {}
        if isinstance(metadata, dict):
            meta.update(metadata)
        st.json(meta)

    # Download / Save section
    st.divider()
    st.markdown("### 💾 Save & Download")

    dl_col1, dl_col2, dl_col3 = st.columns(3)

    with dl_col1:
        # Download selected page markdown
        md_content = getattr(result, "markdown", "") or getattr(result, "fit_markdown", "") or ""
        if md_content:
            filename = url_to_filename(getattr(result, "url", f"page_{selected_idx}"))
            st.download_button(
                "📥 Download This Page (MD)",
                data=md_content.encode("utf-8"),
                file_name=filename,
                mime="text/markdown",
            )

    with dl_col2:
        # Save all to files
        if st.button("💾 Save All to Files"):
            saved = save_results_to_files(results, output_dir, url)
            st.success(f"Saved {saved} files to `{output_dir}`")

    with dl_col3:
        # Download all as zip
        all_md = "\n\n---\n\n".join(
            getattr(r, "markdown", "") or getattr(r, "fit_markdown", "") or ""
            for r in results if getattr(r, "success", False)
        )
        if all_md:
            # Create zip in memory
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for i, r in enumerate(results):
                    if not getattr(r, "success", False):
                        continue
                    md = getattr(r, "markdown", "") or getattr(r, "fit_markdown", "") or ""
                    fname = url_to_filename(getattr(r, "url", f"page_{i}"))
                    zf.writestr(fname, md)
            zip_buffer.seek(0)

            site_name = urlparse(url).netloc.replace(".", "_") or "crawl"
            st.download_button(
                "📦 Download All as ZIP",
                data=zip_buffer.getvalue(),
                file_name=f"{site_name}_crawl.zip",
                mime="application/zip",
            )


def url_to_filename(url):
    """Convert a URL to a safe filename."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        return "index.md"
    # Take last 2 path segments
    parts = path.split("/")
    if len(parts) > 2:
        path = "/".join(parts[-2:])
    # Clean
    path = re.sub(r"[^a-zA-Z0-9/_-]", "", path)
    path = path.rstrip("/")
    return f"{path}.md" if path else "index.md"


def save_results_to_files(results, output_dir, base_url):
    """Save all crawl results to markdown files."""
    site_name = urlparse(base_url).netloc.replace(".", "_") or "crawl"
    site_dir = os.path.join(output_dir, site_name)
    os.makedirs(site_dir, exist_ok=True)

    saved = 0
    for i, result in enumerate(results):
        if not getattr(result, "success", False):
            continue
        md = getattr(result, "markdown", "") or getattr(result, "fit_markdown", "") or ""
        if not md:
            continue
        filename = url_to_filename(getattr(result, "url", f"page_{i}"))
        filepath = os.path.join(site_dir, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md)
        saved += 1

    # Save index
    index_path = os.path.join(site_dir, "INDEX.md")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(f"# Crawl Index — {base_url}\n\n")
        f.write(f"Crawled {saved} pages on {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        for i, r in enumerate(results):
            status = "✅" if getattr(r, "success", False) else "❌"
            title = getattr(r, "title", getattr(r, "url", f"Page {i+1}"))[:60]
            f.write(f"- {status} [{title}]({url_to_filename(getattr(r, 'url', f'page_{i}'))})\n")

    return saved


def run_crawl(url, opts, engine, mode, output_dir):
    """Execute the crawl and return results."""
    if not url:
        st.error("Please enter a URL")
        return None

    if not url.startswith("http"):
        url = "https://" + url

    start_time = time.time()

    try:
        if mode == "Single Page":
            with st.spinner(f"Crawling {url}..."):
                if engine == "Crawl4AI (Browser)":
                    result = asyncio.run(crawl_single_page_c4ai(url, opts))
                else:
                    result = asyncio.run(crawl_single_page_http(url, opts))
                results = [result]
        else:
            # Deep crawl
            progress = st.progress(0, "Starting crawl...")
            status_text = st.empty()

            def update_progress(done, total, current_url):
                pct = min(done / total, 1.0)
                progress.progress(pct)
                status_text.text(f"Crawled {done}/{total} pages — Current: {current_url[:80]}")

            with st.spinner(f"Deep crawling {url}..."):
                if engine == "Crawl4AI (Browser)":
                    results = asyncio.run(crawl_deep_site_c4ai(url, opts, update_progress))
                else:
                    results = asyncio.run(crawl_deep_site_http(url, opts, update_progress))

            progress.progress(1.0)
            status_text.text(f"Crawl complete — {len(results)} pages")

        elapsed = time.time() - start_time

        # Add to history
        st.session_state.crawl_history.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "mode": mode,
            "pages": len(results),
            "url": url,
            "elapsed": f"{elapsed:.1f}s",
        })

        return results

    except Exception as e:
        st.error(f"Crawl failed: {str(e)}")
        st.exception(e)
        return None


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

def main():
    st.title("🕷️ Crawl4AI Web App")
    st.caption("Crawl websites and convert to LLM-optimized markdown")

    engine, mode, output_dir = render_sidebar()

    if mode == "Single Page":
        url, opts = render_single_page_config(engine)
    else:
        url, opts = render_deep_crawl_config(engine)

    st.divider()

    # Crawl button
    if st.button("🚀 Start Crawl", type="primary"):
        results = run_crawl(url, opts, engine, mode, output_dir)
        if results:
            st.session_state.current_result = results
            st.session_state.crawl_results = {"results": results, "url": url, "mode": mode}

    # Show results
    if st.session_state.current_result:
        render_results(
            st.session_state.current_result,
            output_dir,
            st.session_state.crawl_results.get("mode", mode),
            st.session_state.crawl_results.get("url", url),
        )

    # Footer
    st.divider()
    with st.expander("ℹ️ About Crawl4AI Web App"):
        st.markdown("""
        **Crawl4AI Web App** provides a browser-based UI for crawling websites and converting them to LLM-optimized markdown.

        ### Features
        - **Single Page Crawl** — Crawl one URL with full configuration
        - **Deep Crawl** — Recursively crawl entire sites using BFS strategy
        - **Fit Markdown** — Smart content extraction with Pruning/BM25 filters
        - **Raw Markdown** — Full page content preservation
        - **CSS Selectors** — Target specific page elements
        - **JavaScript Execution** — Render dynamic pages (Crawl4AI mode)
        - **URL Filters** — Domain, pattern, and content-type filtering
        - **Export** — Download individual pages or entire crawls as ZIP

        ### Crawl Engines
        - **Crawl4AI (Browser)**: Full headless browser with JS rendering, screenshots, PDFs
        - **HTTP Fallback**: Fast HTTP requests + markdownify (no browser needed)

        ### Output
        Results are saved as clean markdown files with YAML frontmatter, ready for use with any LLM.
        """)


if __name__ == "__main__":
    main()