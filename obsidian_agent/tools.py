import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict

from claude_agent_sdk import tool

from .config import VAULT_PATH, ALLOWED_EXTENSIONS


def _resolve_path(relative_path: str) -> Path:
    resolved = (VAULT_PATH / relative_path).resolve()
    if not str(resolved).startswith(str(VAULT_PATH.resolve())):
        raise ValueError(f"Path traversal detected: {relative_path}")
    return resolved


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    frontmatter_text = parts[1].strip()
    body = parts[2].lstrip("\n")
    metadata = {}
    for line in frontmatter_text.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            value = value.strip().strip('"').strip("'")
            if value.startswith("[") and value.endswith("]"):
                value = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",") if v.strip()]
            metadata[key.strip()] = value
    return metadata, body


def _build_frontmatter(metadata: dict) -> str:
    if not metadata:
        return ""
    lines = ["---"]
    for key, value in metadata.items():
        if isinstance(value, list):
            items = ", ".join(f'"{v}"' for v in value)
            lines.append(f"{key}: [{items}]")
        else:
            lines.append(f'{key}: "{value}"')
    lines.append("---\n")
    return "\n".join(lines)


def _read_note_file(path: Path) -> dict:
    content = path.read_text(encoding="utf-8")
    metadata, body = _parse_frontmatter(content)
    return {
        "path": str(path.relative_to(VAULT_PATH)),
        "metadata": metadata,
        "content": body,
        "raw_content": content,
        "size": path.stat().st_size,
        "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
    }


def _normalize_tags(tags_input) -> list[str]:
    if isinstance(tags_input, list):
        return [str(t).strip() for t in tags_input if str(t).strip()]
    if isinstance(tags_input, str):
        return [t.strip() for t in tags_input.split(",") if t.strip()]
    return []


def _strip_frontmatter_from_content(content: str) -> str:
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].lstrip("\n")
    return content


class ListNotesInput(TypedDict):
    folder: NotRequired[str]
    recursive: NotRequired[bool]


@tool("list_notes", "List all notes in the Obsidian vault. Returns a list of note paths and metadata.", ListNotesInput)
async def list_notes(args):
    folder = args.get("folder", "")
    recursive = args.get("recursive", True)
    target = _resolve_path(folder) if folder else VAULT_PATH
    if not target.exists():
        return {"content": [{"type": "text", "text": f"Folder not found: {folder}"}]}
    notes = []
    pattern = "**/*" if recursive else "*"
    for f in target.glob(pattern):
        if f.is_file() and f.suffix in ALLOWED_EXTENSIONS:
            try:
                note_info = _read_note_file(f)
                notes.append({
                    "path": note_info["path"],
                    "metadata": note_info["metadata"],
                    "size": note_info["size"],
                    "modified": note_info["modified"],
                })
            except Exception:
                notes.append({"path": str(f.relative_to(VAULT_PATH)), "error": "Failed to read"})
    return {"content": [{"type": "text", "text": str(notes)}]}


class ReadNoteInput(TypedDict):
    path: str


@tool("read_note", "Read a specific note from the vault by its relative path. Returns the note content and metadata.", ReadNoteInput)
async def read_note(args):
    path = args["path"]
    note_path = _resolve_path(path)
    if not note_path.exists():
        return {"content": [{"type": "text", "text": f"Note not found: {path}"}]}
    if not note_path.is_file():
        return {"content": [{"type": "text", "text": f"Path is not a file: {path}"}]}
    try:
        note_info = _read_note_file(note_path)
        return {"content": [{"type": "text", "text": str(note_info)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error reading note: {e}"}]}


class CreateNoteInput(TypedDict):
    path: str
    content: NotRequired[str]
    tags: NotRequired[str | list]
    title: NotRequired[str]


@tool("create_note", "Create a new note in the vault with optional frontmatter metadata. Tags can be a comma-separated string or a list.", CreateNoteInput)
async def create_note(args):
    path = args["path"]
    content = args.get("content", "")
    tags_input = args.get("tags", "")
    title = args.get("title", "")

    note_path = _resolve_path(path)
    if note_path.exists():
        return {"content": [{"type": "text", "text": f"Note already exists: {path}. Use update_note to modify it."}]}

    note_path.parent.mkdir(parents=True, exist_ok=True)

    content = _strip_frontmatter_from_content(content)

    metadata = {}
    if title:
        metadata["title"] = title
    tags_list = _normalize_tags(tags_input)
    if tags_list:
        metadata["tags"] = tags_list
    metadata["created"] = datetime.now().isoformat()
    metadata["updated"] = metadata["created"]

    full_content = _build_frontmatter(metadata) + content
    note_path.write_text(full_content, encoding="utf-8")

    return {"content": [{"type": "text", "text": f"Note created successfully: {path}"}]}


class UpdateNoteInput(TypedDict):
    path: str
    content: NotRequired[str]
    append: NotRequired[bool]
    tags: NotRequired[str | list]
    title: NotRequired[str]


@tool("update_note", "Update an existing note's content and/or metadata. Tags can be a comma-separated string or a list.", UpdateNoteInput)
async def update_note(args):
    path = args["path"]
    new_content = args.get("content", "")
    append = args.get("append", False)
    tags_input = args.get("tags", "")
    title = args.get("title", "")

    note_path = _resolve_path(path)
    if not note_path.exists():
        return {"content": [{"type": "text", "text": f"Note not found: {path}. Use create_note to create it."}]}

    existing = _read_note_file(note_path)
    metadata = existing["metadata"]
    body = existing["content"]

    if new_content:
        new_content = _strip_frontmatter_from_content(new_content)
        if append:
            body = body + "\n" + new_content if body else new_content
        else:
            body = new_content

    if title:
        metadata["title"] = title
    tags_list = _normalize_tags(tags_input)
    if tags_list:
        metadata["tags"] = tags_list
    metadata["updated"] = datetime.now().isoformat()

    full_content = _build_frontmatter(metadata) + body
    note_path.write_text(full_content, encoding="utf-8")

    return {"content": [{"type": "text", "text": f"Note updated successfully: {path}"}]}


class DeleteNoteInput(TypedDict):
    path: str


@tool("delete_note", "Delete a note from the vault.", DeleteNoteInput)
async def delete_note(args):
    path = args["path"]
    note_path = _resolve_path(path)
    if not note_path.exists():
        return {"content": [{"type": "text", "text": f"Note not found: {path}"}]}
    note_path.unlink()
    return {"content": [{"type": "text", "text": f"Note deleted successfully: {path}"}]}


class SearchNotesInput(TypedDict):
    query: str
    case_sensitive: NotRequired[bool]
    search_in_metadata: NotRequired[bool]


@tool("search_notes", "Search notes by content or title using a text query. Supports regex patterns.", SearchNotesInput)
async def search_notes(args):
    query = args["query"]
    case_sensitive = args.get("case_sensitive", False)
    search_in_metadata = args.get("search_in_metadata", True)

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query, flags)
    except re.error:
        pattern = re.compile(re.escape(query), flags)

    results = []
    for f in VAULT_PATH.rglob("*"):
        if not f.is_file() or f.suffix not in ALLOWED_EXTENSIONS:
            continue
        try:
            content = f.read_text(encoding="utf-8")
            metadata, body = _parse_frontmatter(content)
            search_text = body
            if search_in_metadata:
                search_text = content

            matches = pattern.findall(search_text)
            if matches:
                rel_path = str(f.relative_to(VAULT_PATH))
                preview_start = max(0, search_text.lower().find(query.lower()) - 50)
                preview = search_text[preview_start:preview_start + 200]
                results.append({
                    "path": rel_path,
                    "match_count": len(matches),
                    "preview": preview,
                    "metadata": metadata,
                })
        except Exception:
            continue

    return {"content": [{"type": "text", "text": str(results)}]}


class MoveNoteInput(TypedDict):
    source: str
    destination: str


@tool("move_note", "Move or rename a note to a new path.", MoveNoteInput)
async def move_note(args):
    source = args["source"]
    destination = args["destination"]

    src_path = _resolve_path(source)
    dst_path = _resolve_path(destination)

    if not src_path.exists():
        return {"content": [{"type": "text", "text": f"Source note not found: {source}"}]}
    if dst_path.exists():
        return {"content": [{"type": "text", "text": f"Destination already exists: {destination}"}]}

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src_path), str(dst_path))

    return {"content": [{"type": "text", "text": f"Note moved from {source} to {destination}"}]}


class GetTagsInput(TypedDict):
    pass


@tool("get_tags", "Get all tags used across the vault and the notes that use each tag.", GetTagsInput)
async def get_tags(args):
    tag_map: dict[str, list[str]] = {}
    for f in VAULT_PATH.rglob("*"):
        if not f.is_file() or f.suffix not in ALLOWED_EXTENSIONS:
            continue
        try:
            content = f.read_text(encoding="utf-8")
            metadata, body = _parse_frontmatter(content)
            rel_path = str(f.relative_to(VAULT_PATH))

            if "tags" in metadata:
                tags = metadata["tags"]
                if isinstance(tags, list):
                    for tag in tags:
                        tag_map.setdefault(tag, []).append(rel_path)
                else:
                    tag_map.setdefault(str(tags), []).append(rel_path)

            inline_tags = re.findall(r'(?:^|\s)#([a-zA-Z\u4e00-\u9fff][\w\u4e00-\u9fff/]*)', body)
            for tag in inline_tags:
                tag_map.setdefault(tag, []).append(rel_path)
        except Exception:
            continue

    return {"content": [{"type": "text", "text": str(tag_map)}]}


class GetBacklinksInput(TypedDict):
    path: str


@tool("get_backlinks", "Get all notes that link to a specific note (backlinks).", GetBacklinksInput)
async def get_backlinks(args):
    path = args["path"]
    note_name = Path(path).stem
    backlinks = []

    for f in VAULT_PATH.rglob("*"):
        if not f.is_file() or f.suffix not in ALLOWED_EXTENSIONS:
            continue
        try:
            content = f.read_text(encoding="utf-8")
            rel_path = str(f.relative_to(VAULT_PATH))
            if rel_path == path:
                continue

            wiki_link_pattern = rf'\[\[{re.escape(note_name)}'
            md_link_pattern = rf'\[([^\]]*)\]\([^)]*{re.escape(note_name)}[^)]*\)'

            if re.search(wiki_link_pattern, content) or re.search(md_link_pattern, content, re.IGNORECASE):
                metadata, _ = _parse_frontmatter(content)
                backlinks.append({
                    "path": rel_path,
                    "metadata": metadata,
                })
        except Exception:
            continue

    return {"content": [{"type": "text", "text": str(backlinks)}]}


class CreateFolderInput(TypedDict):
    path: str


@tool("create_folder", "Create a new folder in the vault.", CreateFolderInput)
async def create_folder(args):
    path = args["path"]
    folder_path = _resolve_path(path)
    if folder_path.exists():
        return {"content": [{"type": "text", "text": f"Folder already exists: {path}"}]}
    folder_path.mkdir(parents=True, exist_ok=True)
    return {"content": [{"type": "text", "text": f"Folder created: {path}"}]}


class GetNoteStructureInput(TypedDict):
    folder: NotRequired[str]
    depth: NotRequired[int]


@tool("get_note_structure", "Get the folder structure of the vault, showing the hierarchy of notes and folders.", GetNoteStructureInput)
async def get_note_structure(args):
    folder = args.get("folder", "")
    depth = args.get("depth", 3)
    target = _resolve_path(folder) if folder else VAULT_PATH

    if not target.exists():
        return {"content": [{"type": "text", "text": f"Folder not found: {folder}"}]}

    lines = []

    def _walk(current: Path, prefix: str = "", current_depth: int = 0):
        if current_depth >= depth:
            return
        items = sorted(current.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        for i, item in enumerate(items):
            if item.name.startswith("."):
                continue
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            child_prefix = "    " if is_last else "│   "
            if item.is_dir():
                lines.append(f"{prefix}{connector}{item.name}/")
                _walk(item, prefix + child_prefix, current_depth + 1)
            elif item.suffix in ALLOWED_EXTENSIONS:
                lines.append(f"{prefix}{connector}{item.name}")

    _walk(target)
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


ALL_TOOLS = [
    list_notes,
    read_note,
    create_note,
    update_note,
    delete_note,
    search_notes,
    move_note,
    get_tags,
    get_backlinks,
    create_folder,
    get_note_structure,
]

TOOL_NAMES = [f"mcp__obsidian__{t.name}" for t in ALL_TOOLS]
