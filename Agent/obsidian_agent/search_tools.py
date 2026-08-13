import re
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict

from claude_agent_sdk import tool

from .config import VAULT_PATH, WEB_SEARCH_ENABLED
from .audit import audit_logger
from .tools import _normalize_tags, _resolve_path, _build_frontmatter


def _web_search(query: str, max_results: int = 5) -> list[dict]:
    results = []
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results, region="us-en"):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                    "source": "",
                })
    except ImportError:
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results, region="us-en"):
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                        "source": "",
                    })
        except ImportError:
            results = _web_search_fallback(query, max_results)
        except Exception as e:
            fallback = _web_search_fallback(query, max_results)
            if fallback:
                return fallback
            results.append({"error": f"DDGS search failed: {e}"})
    except Exception as e:
        fallback = _web_search_fallback(query, max_results)
        if fallback:
            return fallback
        results.append({"error": f"DDGS search failed: {e}"})
    return results[:max_results]


def _web_search_fallback(query: str, max_results: int = 5) -> list[dict]:
    import json
    import urllib.parse
    results = []
    try:
        encoded = urllib.parse.quote_plus(query)
        url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        abstract = data.get("Abstract", "")
        if abstract:
            results.append({
                "title": data.get("Heading", ""),
                "url": data.get("AbstractURL", ""),
                "snippet": abstract,
                "source": data.get("AbstractSource", ""),
            })
        for topic in (data.get("RelatedTopics") or [])[:max_results]:
            if isinstance(topic, dict) and "Text" in topic:
                results.append({
                    "title": topic.get("Text", "")[:80],
                    "url": topic.get("FirstURL", ""),
                    "snippet": topic.get("Text", ""),
                    "source": "",
                })
    except Exception:
        pass
    return results[:max_results]


def _fetch_url_content(url: str, max_length: int = 5000) -> str:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,text/plain,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text" not in content_type and "html" not in content_type and "xml" not in content_type:
                return f"[Non-text content: {content_type}]"

            raw = resp.read(max_length + 1)
            encoding = "utf-8"

            if "charset=" in content_type:
                charset_match = content_type.split("charset=")[-1].split(";")[0].strip()
                if charset_match:
                    encoding = charset_match

            try:
                text = raw.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                text = raw.decode("utf-8", errors="replace")

            if len(raw) > max_length:
                text = text[:max_length] + "\n...[truncated]"

            text = _strip_html(text)
            return text.strip()
    except Exception as e:
        return f"[Failed to fetch: {e}]"


def _strip_html(text: str) -> str:
    text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</h[1-6]>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</li>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)
    return text


class WebSearchInput(TypedDict):
    query: str
    max_results: NotRequired[int]


@tool("web_search", "Search the web for information to enrich note content. Returns search results with titles, URLs, and snippets.", WebSearchInput)
async def web_search(args):
    import time
    start = time.time()

    if not WEB_SEARCH_ENABLED:
        return {"content": [{"type": "text", "text": "Web search is disabled. Set WEB_SEARCH_ENABLED=true to enable."}]}

    query = args["query"]
    max_results = args.get("max_results", 5)

    results = _web_search(query, max_results)

    if not results:
        result_msg = f"No results found for: {query}"
    else:
        lines = [f"Search results for: **{query}**\n"]
        for i, r in enumerate(results):
            if "error" in r:
                lines.append(f"{i+1}. {r['error']}")
            else:
                lines.append(f"{i+1}. **{r['title']}**")
                lines.append(f"   URL: {r['url']}")
                lines.append(f"   {r['snippet']}")
                if r.get("source"):
                    lines.append(f"   Source: {r['source']}")
                lines.append("")
        result_msg = "\n".join(lines)

    audit_logger.log_operation("web_search", "web_search", args, result_msg[:500], duration_ms=(time.time() - start) * 1000)
    return {"content": [{"type": "text", "text": result_msg}]}


class WebFetchContentInput(TypedDict):
    url: str
    max_length: NotRequired[int]


@tool("web_fetch_content", "Fetch and extract text content from a URL. Useful for reading web pages to enrich notes.", WebFetchContentInput)
async def web_fetch_content(args):
    import time
    start = time.time()

    url = args["url"]
    max_length = args.get("max_length", 5000)

    content = _fetch_url_content(url, max_length)

    audit_logger.log_operation("web_fetch_content", "web_fetch_content", args, f"Fetched {len(content)} chars from {url}", duration_ms=(time.time() - start) * 1000)
    return {"content": [{"type": "text", "text": content}]}


class CreateNoteWithSearchInput(TypedDict):
    path: str
    topic: NotRequired[str]
    tags: NotRequired[str | list]
    title: NotRequired[str]
    max_search_results: NotRequired[int]


@tool("create_note_with_search", "Create a new note enriched with web search results. Searches the web for the topic and combines results into a structured note.", CreateNoteWithSearchInput)
async def create_note_with_search(args):
    import time
    start = time.time()

    path = args["path"]
    topic = args.get("topic", "")
    tags_input = args.get("tags", "")
    title = args.get("title", "")
    max_search_results = args.get("max_search_results", 3)

    if not topic:
        topic = title or Path(path).stem

    search_results = []
    if WEB_SEARCH_ENABLED:
        search_results = _web_search(topic, max_search_results)

    note_path = _resolve_path(path)
    if note_path.exists():
        return {"content": [{"type": "text", "text": f"Note already exists: {path}. Use update_note to modify it."}]}

    note_path.parent.mkdir(parents=True, exist_ok=True)

    metadata = {}
    if title:
        metadata["title"] = title
    else:
        metadata["title"] = topic
    tags_list = _normalize_tags(tags_input)
    if tags_list:
        metadata["tags"] = tags_list
    else:
        metadata["tags"] = ["联网搜索"]
    metadata["created"] = datetime.now().isoformat()
    metadata["updated"] = metadata["created"]
    metadata["sources"] = [r.get("url", "") for r in search_results if r.get("url")]

    md_lines = [f"# {metadata['title']}", ""]

    if search_results:
        md_lines.append("## 搜索来源")
        md_lines.append("")
        for i, r in enumerate(search_results):
            if "error" in r:
                continue
            md_lines.append(f"{i+1}. [{r.get('title', 'Link')}]({r.get('url', '')})")
            if r.get("snippet"):
                md_lines.append(f"   > {r['snippet']}")
        md_lines.append("")
        md_lines.append("---")
        md_lines.append("")

    md_lines.append("## 概述")
    md_lines.append("")
    md_lines.append(f"（关于 **{topic}** 的笔记，请根据搜索结果和你的知识补充内容）")
    md_lines.append("")

    for i, r in enumerate(search_results):
        if "error" in r or not r.get("snippet"):
            continue
        md_lines.append(f"## 参考 {i+1}: {r.get('title', '')}")
        md_lines.append("")
        md_lines.append(r["snippet"])
        if r.get("url"):
            md_lines.append(f"\n来源: {r['url']}")
        md_lines.append("")

    full_content = _build_frontmatter(metadata) + "\n".join(md_lines)
    note_path.write_text(full_content, encoding="utf-8")

    result_msg = f"Note created with web search enrichment: {path}\nSearch results: {len(search_results)}"
    audit_logger.log_operation("create_note_with_search", "create_note_with_search", args, result_msg, duration_ms=(time.time() - start) * 1000)
    return {"content": [{"type": "text", "text": result_msg}]}


SEARCH_TOOLS = [web_search, web_fetch_content, create_note_with_search]
SEARCH_TOOL_NAMES = [f"mcp__obsidian__{t.name}" for t in SEARCH_TOOLS]
