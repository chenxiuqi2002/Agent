import json
import re
from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict

from claude_agent_sdk import tool

from .config import VAULT_PATH, ALLOWED_EXTENSIONS, KNOWLEDGE_GRAPH_DIR
from .audit import audit_logger
from .tools import _resolve_path, _parse_frontmatter, _build_frontmatter


def _read_note_content(path: Path) -> tuple[dict, str]:
    content = path.read_text(encoding="utf-8")
    return _parse_frontmatter(content)


def _find_related_notes(note_path: str, body: str, metadata: dict, all_notes: dict[str, tuple[dict, str]]) -> list[dict]:
    related = []
    tags = metadata.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]

    wiki_links = set(re.findall(r'\[\[([^\]|]+)', body))

    for other_path, (other_meta, other_body) in all_notes.items():
        if other_path == note_path:
            continue

        score = 0
        reasons = []

        other_tags = other_meta.get("tags", [])
        if isinstance(other_tags, str):
            other_tags = [other_tags]
        common_tags = set(str(t) for t in tags) & set(str(t) for t in other_tags)
        if common_tags:
            score += len(common_tags) * 2
            reasons.append(f"共享标签: {', '.join(common_tags)}")

        other_name = Path(other_path).stem
        if other_name in wiki_links:
            score += 5
            reasons.append("直接链接引用")

        title = metadata.get("title", Path(note_path).stem)
        other_title = other_meta.get("title", other_name)
        if title.lower() in other_body.lower() or other_title.lower() in body.lower():
            score += 1
            reasons.append("内容提及")

        if score > 0:
            related.append({
                "path": other_path,
                "score": score,
                "reasons": reasons,
                "title": other_title,
            })

    related.sort(key=lambda x: x["score"], reverse=True)
    return related[:10]


class SummarizeNotesInput(TypedDict):
    paths: NotRequired[str]
    folder: NotRequired[str]
    output_path: NotRequired[str]
    max_notes: NotRequired[int]


@tool("summarize_notes", "Summarize multiple notes in batch. Provide a folder path or list of note paths. Generates a summary note with key points from each note.", SummarizeNotesInput)
async def summarize_notes(args):
    import time
    start = time.time()

    paths_str = args.get("paths", "")
    folder = args.get("folder", "")
    output_path = args.get("output_path", "summaries/batch_summary.md")
    max_notes = args.get("max_notes", 20)

    note_paths = []
    if paths_str:
        for p in paths_str.split(","):
            p = p.strip()
            resolved = _resolve_path(p)
            if resolved.exists() and resolved.is_file():
                note_paths.append(resolved)
    elif folder:
        target = _resolve_path(folder)
        if target.exists():
            for f in sorted(target.rglob("*")):
                if f.is_file() and f.suffix in ALLOWED_EXTENSIONS:
                    note_paths.append(f)
    else:
        for f in sorted(VAULT_PATH.rglob("*")):
            if f.is_file() and f.suffix in ALLOWED_EXTENSIONS:
                note_paths.append(f)

    note_paths = note_paths[:max_notes]

    if not note_paths:
        result = "No notes found to summarize"
        audit_logger.log_operation("summarize_notes", "summarize_notes", args, result, duration_ms=(time.time() - start) * 1000)
        return {"content": [{"type": "text", "text": result}]}

    all_notes = {}
    for f in VAULT_PATH.rglob("*"):
        if f.is_file() and f.suffix in ALLOWED_EXTENSIONS:
            try:
                meta, body = _read_note_content(f)
                rel = str(f.relative_to(VAULT_PATH))
                all_notes[rel] = (meta, body)
            except Exception:
                continue

    summaries = []
    for f in note_paths:
        try:
            meta, body = _read_note_content(f)
            rel = str(f.relative_to(VAULT_PATH))
            title = meta.get("title", f.stem)

            first_heading = ""
            for line in body.split("\n"):
                line = line.strip()
                if line.startswith("#"):
                    first_heading = line.lstrip("#").strip()
                    break

            paragraphs = [p.strip() for p in body.split("\n\n") if p.strip() and not p.strip().startswith("#")]
            content_preview = " ".join(paragraphs[:3])
            if len(content_preview) > 500:
                content_preview = content_preview[:500] + "..."

            related = _find_related_notes(rel, body, meta, all_notes)

            summaries.append({
                "path": rel,
                "title": title,
                "heading": first_heading,
                "preview": content_preview,
                "tags": meta.get("tags", []),
                "related_notes": [{"path": r["path"], "title": r["title"], "reason": r["reasons"][0]} for r in related[:3]],
            })
        except Exception as e:
            summaries.append({"path": str(f.relative_to(VAULT_PATH)), "error": str(e)})

    md_lines = [
        f"# 批量笔记总结",
        f"",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 总结笔记数: {len(summaries)}",
        f"",
        f"## 目录",
        f"",
    ]
    for i, s in enumerate(summaries):
        if "error" in s:
            continue
        md_lines.append(f"{i+1}. [[{Path(s['path']).stem}|{s['title']}]]")

    md_lines.append("")
    md_lines.append("---")
    md_lines.append("")

    for s in summaries:
        if "error" in s:
            md_lines.append(f"## {s['path']}")
            md_lines.append(f"读取失败: {s['error']}")
            md_lines.append("")
            continue

        md_lines.append(f"## {s['title']}")
        md_lines.append(f"**路径**: `{s['path']}`")
        if s.get("tags"):
            tag_str = " ".join(f"#{t}" if not str(t).startswith("#") else str(t) for t in s["tags"])
            md_lines.append(f"**标签**: {tag_str}")
        md_lines.append("")
        if s.get("heading"):
            md_lines.append(f"### {s['heading']}")
        md_lines.append(s.get("preview", "（空笔记）"))
        md_lines.append("")
        if s.get("related_notes"):
            md_lines.append("**相关笔记**:")
            for r in s["related_notes"]:
                md_lines.append(f"- [[{Path(r['path']).stem}|{r['title']}]] — {r['reason']}")
            md_lines.append("")
        md_lines.append("---")
        md_lines.append("")

    output = _resolve_path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    metadata = {
        "title": "批量笔记总结",
        "tags": ["总结", "自动生成"],
        "created": datetime.now().isoformat(),
        "updated": datetime.now().isoformat(),
        "source_count": str(len(summaries)),
    }
    full_content = _build_frontmatter(metadata) + "\n".join(md_lines)
    output.write_text(full_content, encoding="utf-8")

    result_msg = f"Batch summary created: {output_path}\nSummarized {len(summaries)} notes"
    audit_logger.log_operation("summarize_notes", "summarize_notes", args, result_msg, duration_ms=(time.time() - start) * 1000)
    return {"content": [{"type": "text", "text": result_msg}]}


class GenerateKnowledgeGraphInput(TypedDict):
    format: NotRequired[str]
    folder: NotRequired[str]
    min_connections: NotRequired[int]


@tool("generate_knowledge_graph", "Generate a knowledge graph of note relationships as a Mermaid diagram or JSON. Analyzes tags, links, and content similarity.", GenerateKnowledgeGraphInput)
async def generate_knowledge_graph(args):
    import time
    start = time.time()

    fmt = args.get("format", "mermaid")
    folder = args.get("folder", "")
    min_connections = args.get("min_connections", 1)

    target = _resolve_path(folder) if folder else VAULT_PATH

    notes_data = {}
    for f in target.rglob("*"):
        if not f.is_file() or f.suffix not in ALLOWED_EXTENSIONS:
            continue
        try:
            meta, body = _read_note_content(f)
            rel = str(f.relative_to(VAULT_PATH))
            notes_data[rel] = {
                "metadata": meta,
                "body": body,
                "title": meta.get("title", f.stem),
                "tags": meta.get("tags", []),
                "links": re.findall(r'\[\[([^\]|]+)', body),
            }
        except Exception:
            continue

    nodes = []
    edges = []

    for rel, data in notes_data.items():
        title = data["title"]
        tags = data["tags"]
        if isinstance(tags, str):
            tags = [tags]
        nodes.append({
            "id": rel,
            "title": title,
            "tags": [str(t) for t in tags],
            "connections": 0,
        })

    node_map = {n["id"]: n for n in nodes}
    title_to_id = {}
    for rel, data in notes_data.items():
        title_to_id[data["title"].lower()] = rel
        title_to_id[Path(rel).stem.lower()] = rel

    edge_set = set()
    for rel, data in notes_data.items():
        for link in data["links"]:
            link_lower = link.strip().lower()
            target_id = title_to_id.get(link_lower)
            if target_id and target_id != rel:
                edge_key = tuple(sorted([rel, target_id]))
                if edge_key not in edge_set:
                    edge_set.add(edge_key)
                    edges.append({"source": rel, "target": target_id, "type": "link"})
                    if rel in node_map:
                        node_map[rel]["connections"] += 1
                    if target_id in node_map:
                        node_map[target_id]["connections"] += 1

    tag_groups: dict[str, list[str]] = {}
    for rel, data in notes_data.items():
        tags = data["tags"]
        if isinstance(tags, str):
            tags = [tags]
        for tag in tags:
            tag_groups.setdefault(str(tag), []).append(rel)

    for tag, note_ids in tag_groups.items():
        for i in range(len(note_ids)):
            for j in range(i + 1, len(note_ids)):
                edge_key = tuple(sorted([note_ids[i], note_ids[j]]))
                if edge_key not in edge_set:
                    edge_set.add(edge_key)
                    edges.append({"source": note_ids[i], "target": note_ids[j], "type": "tag", "tag": tag})

    filtered_nodes = [n for n in nodes if n["connections"] >= min_connections] if min_connections > 0 else nodes
    filtered_node_ids = {n["id"] for n in filtered_nodes}
    filtered_edges = [e for e in edges if e["source"] in filtered_node_ids and e["target"] in filtered_node_ids]

    KNOWLEDGE_GRAPH_DIR.mkdir(parents=True, exist_ok=True)

    if fmt == "mermaid":
        lines = ["graph LR"]
        id_map = {}
        for i, node in enumerate(filtered_nodes):
            safe_id = f"N{i}"
            id_map[node["id"]] = safe_id
            label = node["title"].replace('"', "'")[:30]
            lines.append(f"    {safe_id}[\"{label}\"]")

        lines.append("")
        for edge in filtered_edges:
            src = id_map.get(edge["source"])
            tgt = id_map.get(edge["target"])
            if src and tgt:
                if edge["type"] == "tag":
                    lines.append(f"    {src} -.->|{edge.get('tag', '')}| {tgt}")
                else:
                    lines.append(f"    {src} --> {tgt}")

        mermaid_content = "\n".join(lines)
        graph_file = KNOWLEDGE_GRAPH_DIR / "knowledge_graph.md"
        metadata = {
            "title": "知识图谱",
            "tags": ["知识图谱", "自动生成"],
            "created": datetime.now().isoformat(),
        }
        full = _build_frontmatter(metadata) + f"# 知识图谱\n\n```mermaid\n{mermaid_content}\n```\n"
        graph_file.write_text(full, encoding="utf-8")

        result_msg = f"Knowledge graph (Mermaid) generated with {len(filtered_nodes)} nodes and {len(filtered_edges)} edges.\nSaved to .knowledge_graph/knowledge_graph.md"
    else:
        graph_data = {
            "nodes": filtered_nodes,
            "edges": filtered_edges,
            "tag_groups": {k: v for k, v in tag_groups.items()},
            "generated_at": datetime.now().isoformat(),
        }
        graph_file = KNOWLEDGE_GRAPH_DIR / "knowledge_graph.json"
        graph_file.write_text(json.dumps(graph_data, ensure_ascii=False, indent=2), encoding="utf-8")

        result_msg = f"Knowledge graph (JSON) generated with {len(filtered_nodes)} nodes and {len(filtered_edges)} edges.\nSaved to .knowledge_graph/knowledge_graph.json"

    audit_logger.log_operation("generate_knowledge_graph", "generate_knowledge_graph", args, result_msg, duration_ms=(time.time() - start) * 1000)
    return {"content": [{"type": "text", "text": result_msg}]}


class FindRelatedNotesInput(TypedDict):
    path: str
    max_results: NotRequired[int]


@tool("find_related_notes", "Find notes related to a specific note based on tags, links, and content similarity.", FindRelatedNotesInput)
async def find_related_notes(args):
    import time
    start = time.time()

    path = args["path"]
    max_results = args.get("max_results", 10)

    note_path = _resolve_path(path)
    if not note_path.exists():
        return {"content": [{"type": "text", "text": f"Note not found: {path}"}]}

    meta, body = _read_note_content(note_path)

    all_notes = {}
    for f in VAULT_PATH.rglob("*"):
        if f.is_file() and f.suffix in ALLOWED_EXTENSIONS:
            try:
                other_meta, other_body = _read_note_content(f)
                rel = str(f.relative_to(VAULT_PATH))
                all_notes[rel] = (other_meta, other_body)
            except Exception:
                continue

    related = _find_related_notes(path, body, meta, all_notes)

    result = {
        "note": path,
        "title": meta.get("title", note_path.stem),
        "related_notes": related[:max_results],
    }

    audit_logger.log_operation("find_related_notes", "find_related_notes", args, result, duration_ms=(time.time() - start) * 1000)
    return {"content": [{"type": "text", "text": str(result)}]}


SUMMARY_TOOLS = [summarize_notes, generate_knowledge_graph, find_related_notes]
SUMMARY_TOOL_NAMES = [f"mcp__obsidian__{t.name}" for t in SUMMARY_TOOLS]
