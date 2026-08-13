import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict

from claude_agent_sdk import tool

from .config import VAULT_PATH, ALLOWED_EXTENSIONS, KNOWLEDGE_GRAPH_DIR
from .audit import audit_logger
from .tools import _resolve_path, _parse_frontmatter, _build_frontmatter


def _build_link_graph() -> dict:
    notes = {}
    for f in VAULT_PATH.rglob("*"):
        if not f.is_file() or f.suffix not in ALLOWED_EXTENSIONS:
            continue
        try:
            content = f.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(content)
            rel = str(f.relative_to(VAULT_PATH))
            stem = f.stem

            wiki_links = re.findall(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]', body)
            wiki_links = [l.strip() for l in wiki_links]

            md_links = re.findall(r'\[([^\]]*)\]\(([^)]+)\)', body)
            md_link_targets = []
            for text, href in md_links:
                if not href.startswith(("http://", "https://", "mailto:", "#")):
                    md_link_targets.append(href.strip())

            inline_tags = re.findall(r'(?:^|\s)#([a-zA-Z\u4e00-\u9fff][\w\u4e00-\u9fff/]*)', body)
            frontmatter_tags = meta.get("tags", [])
            if isinstance(frontmatter_tags, str):
                frontmatter_tags = [frontmatter_tags]
            all_tags = list(set(str(t) for t in frontmatter_tags) | set(inline_tags))

            embeds = re.findall(r'!\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]', body)
            embeds = [e.strip() for e in embeds]

            notes[rel] = {
                "title": meta.get("title", stem),
                "stem": stem,
                "wiki_links": wiki_links,
                "md_links": md_link_targets,
                "tags": all_tags,
                "embeds": embeds,
                "metadata": meta,
            }
        except Exception:
            continue

    stem_to_path = {}
    title_to_path = {}
    for rel, data in notes.items():
        stem_to_path[data["stem"].lower()] = rel
        title_to_path[data["title"].lower()] = rel

    outgoing = defaultdict(list)
    incoming = defaultdict(list)
    unresolved = defaultdict(list)

    for rel, data in notes.items():
        for link in data["wiki_links"]:
            link_lower = link.lower()
            target = stem_to_path.get(link_lower) or title_to_path.get(link_lower)
            if target:
                outgoing[rel].append({"target": target, "type": "wiki", "raw": f"[[{link}]]"})
                incoming[target].append({"source": rel, "type": "wiki", "raw": f"[[{link}]]"})
            else:
                unresolved[link].append(rel)

        for link in data["md_links"]:
            link_lower = Path(link).stem.lower()
            target = stem_to_path.get(link_lower)
            if target:
                outgoing[rel].append({"target": target, "type": "markdown", "raw": link})
                incoming[target].append({"source": rel, "type": "markdown", "raw": link})

        for embed in data["embeds"]:
            embed_lower = embed.lower()
            target = stem_to_path.get(embed_lower) or title_to_path.get(embed_lower)
            if target:
                outgoing[rel].append({"target": target, "type": "embed", "raw": f"![[{embed}]]"})
                incoming[target].append({"source": rel, "type": "embed", "raw": f"![[{embed}]]"})

    return {
        "notes": notes,
        "outgoing": dict(outgoing),
        "incoming": dict(incoming),
        "unresolved": dict(unresolved),
        "stem_to_path": stem_to_path,
    }


class AnalyzeLinksInput(TypedDict):
    path: NotRequired[str]
    include_unresolved: NotRequired[bool]


@tool("analyze_links", "Analyze bidirectional links between all notes. Returns outgoing links, incoming links (backlinks), and unresolved references.", AnalyzeLinksInput)
async def analyze_links(args):
    import time
    start = time.time()

    path = args.get("path", "")
    include_unresolved = args.get("include_unresolved", True)

    graph = _build_link_graph()

    if path:
        note_path = _resolve_path(path)
        if not note_path.exists():
            return {"content": [{"type": "text", "text": f"Note not found: {path}"}]}
        rel = str(note_path.relative_to(VAULT_PATH))

        out_links = graph["outgoing"].get(rel, [])
        in_links = graph["incoming"].get(rel, [])

        result = {
            "note": rel,
            "title": graph["notes"].get(rel, {}).get("title", ""),
            "outgoing_links": out_links,
            "incoming_links": in_links,
            "outgoing_count": len(out_links),
            "incoming_count": len(in_links),
        }

        if include_unresolved:
            note_data = graph["notes"].get(rel, {})
            unresolved_for_note = []
            for link in note_data.get("wiki_links", []):
                if link.lower() not in graph["stem_to_path"]:
                    unresolved_for_note.append(link)
            result["unresolved_links"] = unresolved_for_note

        audit_logger.log_operation("analyze_links", "analyze_links", args, f"Analyzed links for {rel}", duration_ms=(time.time() - start) * 1000)
        return {"content": [{"type": "text", "text": str(result)}]}

    stats = {
        "total_notes": len(graph["notes"]),
        "total_outgoing": sum(len(v) for v in graph["outgoing"].values()),
        "total_incoming": sum(len(v) for v in graph["incoming"].values()),
        "unresolved_count": len(graph["unresolved"]),
        "orphan_notes": [],
        "hub_notes": [],
    }

    for rel in graph["notes"]:
        if not graph["outgoing"].get(rel) and not graph["incoming"].get(rel):
            stats["orphan_notes"].append(rel)

    sorted_by_incoming = sorted(graph["incoming"].items(), key=lambda x: len(x[1]), reverse=True)
    stats["hub_notes"] = [{"path": p, "backlinks": len(links)} for p, links in sorted_by_incoming[:10]]

    if include_unresolved:
        stats["unresolved_references"] = [
            {"target": target, "referenced_by": sources}
            for target, sources in sorted(graph["unresolved"].items(), key=lambda x: len(x[1]), reverse=True)[:20]
        ]

    audit_logger.log_operation("analyze_links", "analyze_links", args, f"Full link analysis: {stats['total_notes']} notes", duration_ms=(time.time() - start) * 1000)
    return {"content": [{"type": "text", "text": str(stats)}]}


class GenerateLinkGraphInput(TypedDict):
    format: NotRequired[str]
    folder: NotRequired[str]
    link_type: NotRequired[str]
    max_nodes: NotRequired[int]


@tool("generate_link_graph", "Generate a visualizable knowledge graph of bidirectional links in Mermaid or JSON format.", GenerateLinkGraphInput)
async def generate_link_graph(args):
    import time
    start = time.time()

    fmt = args.get("format", "mermaid")
    folder = args.get("folder", "")
    link_type = args.get("link_type", "all")
    max_nodes = args.get("max_nodes", 50)

    graph = _build_link_graph()

    target_notes = {}
    if folder:
        target = _resolve_path(folder)
        for rel, data in graph["notes"].items():
            note_path = VAULT_PATH / rel
            if str(note_path).startswith(str(target)):
                target_notes[rel] = data
    else:
        target_notes = graph["notes"]

    sorted_notes = sorted(
        target_notes.items(),
        key=lambda x: len(graph["incoming"].get(x[0], [])) + len(graph["outgoing"].get(x[0], [])),
        reverse=True,
    )
    sorted_notes = sorted_notes[:max_nodes]
    included_paths = {rel for rel, _ in sorted_notes}

    nodes = []
    for rel, data in sorted_notes:
        in_count = len(graph["incoming"].get(rel, []))
        out_count = len(graph["outgoing"].get(rel, []))
        nodes.append({
            "id": rel,
            "title": data["title"],
            "stem": data["stem"],
            "tags": data["tags"],
            "in_links": in_count,
            "out_links": out_count,
            "total_connections": in_count + out_count,
        })

    edges = []
    edge_set = set()
    for rel in included_paths:
        for link in graph["outgoing"].get(rel, []):
            target = link["target"]
            if target in included_paths:
                lt = link["type"]
                if link_type != "all" and lt != link_type:
                    continue
                edge_key = (rel, target, lt)
                if edge_key not in edge_set:
                    edge_set.add(edge_key)
                    edges.append({
                        "source": rel,
                        "target": target,
                        "type": lt,
                    })

    KNOWLEDGE_GRAPH_DIR.mkdir(parents=True, exist_ok=True)

    if fmt == "mermaid":
        lines = ["graph LR"]
        id_map = {}
        for i, node in enumerate(nodes):
            safe_id = f"N{i}"
            id_map[node["id"]] = safe_id
            label = node["title"].replace('"', "'")[:25]
            conn = node["total_connections"]
            lines.append(f"    {safe_id}[\"{label}<br/><small>{conn} links</small>\"]")

        lines.append("")
        for edge in edges:
            src = id_map.get(edge["source"])
            tgt = id_map.get(edge["target"])
            if src and tgt:
                if edge["type"] == "wiki":
                    lines.append(f"    {src} --> {tgt}")
                elif edge["type"] == "embed":
                    lines.append(f"    {src} ==>|embed| {tgt}")
                else:
                    lines.append(f"    {src} -.-> {tgt}")

        mermaid_content = "\n".join(lines)
        graph_file = KNOWLEDGE_GRAPH_DIR / "link_graph.md"
        metadata = {
            "title": "双链关系图谱",
            "tags": ["知识图谱", "双链分析", "自动生成"],
            "created": datetime.now().isoformat(),
        }
        full = _build_frontmatter(metadata) + f"# 双链关系图谱\n\n```mermaid\n{mermaid_content}\n```\n"
        graph_file.write_text(full, encoding="utf-8")

        result_msg = f"Link graph (Mermaid) generated: {len(nodes)} nodes, {len(edges)} edges\nSaved to .knowledge_graph/link_graph.md"
    else:
        graph_data = {
            "nodes": nodes,
            "edges": edges,
            "generated_at": datetime.now().isoformat(),
            "stats": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "orphan_count": sum(1 for n in nodes if n["total_connections"] == 0),
            },
        }
        graph_file = KNOWLEDGE_GRAPH_DIR / "link_graph.json"
        graph_file.write_text(json.dumps(graph_data, ensure_ascii=False, indent=2), encoding="utf-8")

        result_msg = f"Link graph (JSON) generated: {len(nodes)} nodes, {len(edges)} edges\nSaved to .knowledge_graph/link_graph.json"

    audit_logger.log_operation("generate_link_graph", "generate_link_graph", args, result_msg, duration_ms=(time.time() - start) * 1000)
    return {"content": [{"type": "text", "text": result_msg}]}


class FindOrphanNotesInput(TypedDict):
    folder: NotRequired[str]


@tool("find_orphan_notes", "Find notes that have no incoming or outgoing links (orphan notes).", FindOrphanNotesInput)
async def find_orphan_notes(args):
    import time
    start = time.time()

    folder = args.get("folder", "")
    graph = _build_link_graph()

    orphans = []
    for rel, data in graph["notes"].items():
        if folder and not rel.startswith(folder):
            continue
        has_out = bool(graph["outgoing"].get(rel))
        has_in = bool(graph["incoming"].get(rel))
        if not has_out and not has_in:
            orphans.append({
                "path": rel,
                "title": data["title"],
                "tags": data["tags"],
            })

    result_msg = f"Found {len(orphans)} orphan notes"
    audit_logger.log_operation("find_orphan_notes", "find_orphan_notes", args, result_msg, duration_ms=(time.time() - start) * 1000)
    return {"content": [{"type": "text", "text": str(orphans)}]}


LINK_TOOLS = [analyze_links, generate_link_graph, find_orphan_notes]
LINK_TOOL_NAMES = [f"mcp__obsidian__{t.name}" for t in LINK_TOOLS]
