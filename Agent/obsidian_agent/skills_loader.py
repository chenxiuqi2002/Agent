import re
from pathlib import Path

SKILLS_DIR = Path(__file__).parent.parent / "skills"

MAX_SKILL_PROMPT_CHARS = 12000

SKILL_SUMMARIES = {
    "obsidian-markdown": (
        "Obsidian Flavored Markdown syntax guide. "
        "Covers: wikilinks [[note]], embeds ![[file]], callouts > [!type], properties (YAML frontmatter), "
        "aliases, tags, footnotes, comments. "
        "Full reference: .skills/obsidian-markdown/SKILL.md (and references/ subfolder)"
    ),
    "obsidian-bases": (
        "Obsidian Bases (.base files) for structured data views. "
        "Covers: views (table/list/card), filters, formulas, summaries, sort, group. "
        "Full reference: .skills/obsidian-bases/SKILL.md (and references/FUNCTIONS_REFERENCE.md)"
    ),
    "json-canvas": (
        "JSON Canvas (.canvas files) for visual knowledge graphs. "
        "Covers: nodes (text/link/group), edges, colors, dimensions. "
        "Full reference: .skills/json-canvas/SKILL.md (and references/EXAMPLES.md)"
    ),
    "obsidian-cli": (
        "Obsidian CLI for vault operations and plugin/theme development. "
        "Covers: CLI commands for managing vaults, plugins, themes. "
        "Full reference: .skills/obsidian-cli/SKILL.md"
    ),
    "defuddle": (
        "Defuddle: extract clean Markdown from web pages, removing clutter to save tokens. "
        "Use before creating notes from web content. "
        "Full reference: .skills/defuddle/SKILL.md"
    ),
}


def load_skill(skill_name: str) -> str:
    skill_dir = SKILLS_DIR / skill_name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return ""

    parts = [skill_md.read_text(encoding="utf-8")]

    refs_dir = skill_dir / "references"
    if refs_dir.exists():
        for ref_file in sorted(refs_dir.glob("*.md")):
            ref_content = ref_file.read_text(encoding="utf-8")
            parts.append(f"\n\n---\n\n## Reference: {ref_file.stem}\n\n{ref_content}")

    return "\n".join(parts)


def load_all_skills() -> dict[str, str]:
    skills = {}
    if not SKILLS_DIR.exists():
        return skills
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
            name = skill_dir.name
            skills[name] = load_skill(name)
    return skills


def deploy_skills_to_vault(vault_path: Path):
    skills_vault_dir = vault_path / ".skills"
    skills_vault_dir.mkdir(parents=True, exist_ok=True)

    if not SKILLS_DIR.exists():
        return

    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        dest_dir = skills_vault_dir / skill_dir.name
        dest_dir.mkdir(parents=True, exist_ok=True)

        content = skill_md.read_text(encoding="utf-8")
        (dest_dir / "SKILL.md").write_text(content, encoding="utf-8")

        refs_dir = skill_dir / "references"
        if refs_dir.exists():
            dest_refs = dest_dir / "references"
            dest_refs.mkdir(parents=True, exist_ok=True)
            for ref_file in refs_dir.glob("*.md"):
                ref_content = ref_file.read_text(encoding="utf-8")
                (dest_refs / ref_file.name).write_text(ref_content, encoding="utf-8")


def build_skills_prompt() -> str:
    all_skills = load_all_skills()
    if not all_skills:
        return ""

    parts = ["## Obsidian Skills\n"]
    parts.append("You have access to the following Obsidian skills. ")
    parts.append("Read the full reference files in vault/.skills/ when you need detailed syntax.\n")

    for name in all_skills:
        summary = SKILL_SUMMARIES.get(name, f"Obsidian skill: {name}")
        parts.append(f"- **{name}**: {summary}")
        parts.append("")

    total_chars = sum(len(p) for p in parts)
    remaining = MAX_SKILL_PROMPT_CHARS - total_chars

    if remaining > 2000:
        for name, content in all_skills.items():
            trimmed = _trim_skill_content(content, remaining // max(len(all_skills), 1))
            if trimmed:
                parts.append(f"### {name} (key rules)\n")
                parts.append(trimmed)
                parts.append("\n")
                remaining -= len(trimmed) + 50
                if remaining <= 0:
                    break

    return "\n".join(parts)


def _trim_skill_content(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content

    lines = content.split("\n")
    result = []
    current_len = 0

    for line in lines:
        if line.startswith("#") or line.strip() == "":
            if current_len + len(line) + 1 <= max_chars:
                result.append(line)
                current_len += len(line) + 1
            else:
                break
        elif line.startswith("- ") or line.startswith("* ") or line.startswith("> "):
            if current_len + len(line) + 1 <= max_chars:
                result.append(line)
                current_len += len(line) + 1
            else:
                break
        else:
            if current_len + len(line) + 1 <= max_chars:
                result.append(line)
                current_len += len(line) + 1
            else:
                break

    trimmed = "\n".join(result)
    if len(trimmed) < len(content):
        trimmed += "\n\n... (see full reference in .skills/)"
    return trimmed
