import asyncio
import io
import json
import os
import sys
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    HookMatcher,
)

from .tools import ALL_TOOLS, TOOL_NAMES
from .summary_tools import SUMMARY_TOOLS, SUMMARY_TOOL_NAMES
from .import_tools import IMPORT_TOOLS, IMPORT_TOOL_NAMES
from .search_tools import SEARCH_TOOLS, SEARCH_TOOL_NAMES
from .link_tools import LINK_TOOLS, LINK_TOOL_NAMES
from .config import (
    VAULT_PATH, ATTACHMENTS_PATH, LOG_DIR, AUDIT_DIR,
    MODEL_OUTPUT_DIR, KNOWLEDGE_GRAPH_DIR,
    SANDBOX_ENABLED, SANDBOX_BLOCKED_PATTERNS,
    ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, MODEL,
)
from .audit import audit_logger
from .skills_loader import build_skills_prompt, deploy_skills_to_vault


def _safe_print(text: str, **kwargs):
    try:
        print(text, **kwargs)
    except UnicodeEncodeError:
        safe_text = text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
            sys.stdout.encoding or "utf-8", errors="replace"
        )
        print(safe_text, **kwargs)

ALL_REGISTERED_TOOLS = ALL_TOOLS + SUMMARY_TOOLS + IMPORT_TOOLS + SEARCH_TOOLS + LINK_TOOLS
ALL_REGISTERED_TOOL_NAMES = TOOL_NAMES + SUMMARY_TOOL_NAMES + IMPORT_TOOL_NAMES + SEARCH_TOOL_NAMES + LINK_TOOL_NAMES

SYSTEM_PROMPT = """You are an Obsidian note management agent running in a local sandbox. You help users manage their Obsidian vault efficiently and intelligently.

## Core Capabilities
- **Note CRUD**: Create, read, update, delete, move, and search notes
- **Note Summary**: Batch summarize notes, find related notes, generate knowledge graphs
- **Multi-format Import**: Import PDF, DOCX, TXT, RTF files as Markdown notes with image extraction
- **Web Search**: Search the web to enrich note content, fetch URL content
- **Link Analysis**: Analyze bidirectional links, find orphans, generate link graphs
- **Attachment Handling**: Extract images from PDF/DOCX, save to attachments with Obsidian references
- **Tag Management**: Get all tags, find notes by tag
- **Vault Structure**: Browse folder hierarchy, create folders

## Web Search Tools
When the user asks to search the web or needs online information:
1. **Search**: Use `mcp__obsidian__web_search` (powered by DDGS library, returns titles, URLs, and snippets)
2. **Deep read**: For detailed content from a specific URL, use `mcp__obsidian__web_fetch_content`
3. **Combined**: For creating a note with search results, use `mcp__obsidian__create_note_with_search`

## Guidelines
1. Always use relative paths from vault root (e.g., "journal/2024-01.md")
2. When creating notes, add frontmatter with title, tags, and timestamps
3. Use wiki-link syntax [[note-name]] for inter-note links
4. For Chinese content, ensure proper UTF-8 encoding
5. Before deleting, always confirm with the user
6. When importing files, extract images to attachments/ and reference with ![[image.png]]
7. All operations are logged for audit trail
8. Respect the existing folder structure and naming conventions

## Vault Info
- Vault path: {vault_path}
- Attachments: {attachments_path}
- Knowledge graphs: {knowledge_graph_path}

{skills_prompt}
""".format(
    vault_path=VAULT_PATH,
    attachments_path=ATTACHMENTS_PATH,
    knowledge_graph_path=KNOWLEDGE_GRAPH_DIR,
    skills_prompt=build_skills_prompt(),
)


async def sandbox_hook(input_data, tool_use_id, context):
    if not SANDBOX_ENABLED:
        return {}

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        for pattern in SANDBOX_BLOCKED_PATTERNS:
            if pattern.lower() in command.lower():
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"Sandbox: blocked dangerous pattern '{pattern}'",
                    }
                }

        for env_var in ["ANTHROPIC_API_KEY", "AWS_", "SECRET", "PASSWORD", "TOKEN"]:
            if env_var in command.upper():
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"Sandbox: blocked access to sensitive environment variable",
                    }
                }

    if tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        if file_path and not file_path.startswith(str(VAULT_PATH)):
            pass

    return {}


def _ensure_dirs():
    for d in [VAULT_PATH, ATTACHMENTS_PATH, LOG_DIR, AUDIT_DIR, MODEL_OUTPUT_DIR, KNOWLEDGE_GRAPH_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    deploy_skills_to_vault(VAULT_PATH)


def create_agent() -> ClaudeSDKClient:
    mcp_server = create_sdk_mcp_server(
        name="obsidian",
        version="1.0.0",
        tools=ALL_REGISTERED_TOOLS,
    )

    sdk_env = {}
    if ANTHROPIC_BASE_URL:
        sdk_env["ANTHROPIC_BASE_URL"] = ANTHROPIC_BASE_URL
    if ANTHROPIC_AUTH_TOKEN:
        sdk_env["ANTHROPIC_AUTH_TOKEN"] = ANTHROPIC_AUTH_TOKEN

    options_kwargs = dict(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"obsidian": mcp_server},
        allowed_tools=ALL_REGISTERED_TOOL_NAMES + ["Read", "Glob"],
        disallowed_tools=["Bash"],
        permission_mode="acceptEdits",
        cwd=str(VAULT_PATH),
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[sandbox_hook]),
                HookMatcher(matcher="Write", hooks=[sandbox_hook]),
                HookMatcher(matcher="Edit", hooks=[sandbox_hook]),
            ],
        },
    )

    if MODEL:
        options_kwargs["model"] = MODEL
    if sdk_env:
        options_kwargs["env"] = sdk_env

    options = ClaudeAgentOptions(**options_kwargs)

    return ClaudeSDKClient(options=options)


async def run_interactive():
    _safe_print("=" * 60)
    _safe_print("  Obsidian Note Management Agent")
    _safe_print("  Powered by Claude Agent SDK")
    _safe_print(f"  Vault: {VAULT_PATH}")
    _safe_print(f"  Sandbox: {'ON' if SANDBOX_ENABLED else 'OFF'}")
    _safe_print(f"  Web Search: Enabled")
    _safe_print(f"  Tools: {len(ALL_REGISTERED_TOOLS)}")
    _safe_print(f"  Session: {audit_logger.session_id}")
    _safe_print("=" * 60)
    _safe_print("")
    _safe_print("Commands:")
    _safe_print("  Type your request in natural language")
    _safe_print("  'quit' or 'exit' to stop")
    _safe_print("  'audit' to view session audit log")
    _safe_print("")

    async with create_agent() as client:
        while True:
            try:
                user_input = input("\n[You]: ").strip()
            except (EOFError, KeyboardInterrupt):
                _safe_print("\nGoodbye!")
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                _safe_print("Goodbye!")
                break
            if user_input.lower() == "audit":
                entries = audit_logger.get_session_log()
                _safe_print(f"\n[Audit Log] ({len(entries)} entries):")
                for entry in entries[-10:]:
                    ts = entry.get("timestamp", "")
                    op = entry.get("operation", entry.get("type", ""))
                    status = entry.get("status", "")
                    _safe_print(f"  [{ts}] {op} {status}")
                continue

            import time
            start = time.time()

            try:
                await client.query(user_input)
            except Exception as e:
                _safe_print(f"\n[Error] Failed to send query: {e}")
                audit_logger.log_operation(
                    operation="query_error",
                    tool_name="query",
                    tool_input={"prompt": user_input},
                    tool_output=str(e),
                    user_prompt=user_input,
                    duration_ms=(time.time() - start) * 1000,
                    status="error",
                    error=str(e),
                )
                continue

            response_parts = []
            _safe_print("\n[Agent]: ", end="")
            try:
                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                _safe_print(block.text, end="", flush=True)
                                response_parts.append(block.text)
                            elif isinstance(block, ToolUseBlock):
                                tool_name = block.name
                                tool_input = block.input
                                short_input = str(tool_input)
                                if len(short_input) > 100:
                                    short_input = short_input[:100] + "..."
                                _safe_print(f"\n  [Tool] {tool_name}: {short_input}", end="", flush=True)

                                audit_logger.log_operation(
                                    operation="tool_call",
                                    tool_name=tool_name,
                                    tool_input=tool_input if isinstance(tool_input, dict) else {"raw": str(tool_input)},
                                    tool_output="",
                                    user_prompt=user_input,
                                    duration_ms=(time.time() - start) * 1000,
                                )
            except Exception as e:
                _safe_print(f"\n[Error] Response interrupted: {e}")
                audit_logger.log_operation(
                    operation="response_error",
                    tool_name="receive_response",
                    tool_input={},
                    tool_output=str(e),
                    user_prompt=user_input,
                    duration_ms=(time.time() - start) * 1000,
                    status="error",
                    error=str(e),
                )
            _safe_print("")

            if response_parts:
                audit_logger.log_query(user_input, " ".join(response_parts)[:500])
                audit_logger.log_model_output(
                    prompt=user_input,
                    response=" ".join(response_parts)[:2000],
                )


async def run_single(prompt: str):
    async with create_agent() as client:
        try:
            await client.query(prompt)
        except Exception as e:
            _safe_print(f"[Error] Failed to send query: {e}")
            return

        try:
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            _safe_print(block.text)
                        elif isinstance(block, ToolUseBlock):
                            _safe_print(f"  [Tool] {block.name}")
        except Exception as e:
            _safe_print(f"[Error] Response interrupted: {e}")


def main():
    if sys.platform == "win32":
        os.system("")  # enable ANSI escape sequences
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    _ensure_dirs()

    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
        asyncio.run(run_single(prompt))
    else:
        asyncio.run(run_interactive())


if __name__ == "__main__":
    main()
