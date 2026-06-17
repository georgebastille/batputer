"""Karpathy-style memory compiler.

Periodically folds new raw facts from the memory log into structured, cross-linked
wiki pages. The model proposes full page bodies in a strict, marker-delimited
format; this task does all the file writing so the filesystem stays deterministic
and the (local) model only has to summarise/merge prose.
"""
import asyncio
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from persistence.markdown_memory import MarkdownMemory

logger = logging.getLogger(__name__)

# Cap how much existing wiki we inline into the prompt. The wiki is small for a
# long time; beyond this we fall back to the index only (a later, smarter
# selection step can replace this).
_MAX_WIKI_CHARS = 12_000

_COMPILE_SYSTEM = """\
You are the librarian for a personal-assistant memory wiki. You fold NEW FACTS into
the existing wiki, following the contract below.

{schema}

Output ONLY page blocks in this exact format, nothing else:

<<<PROFILE>>>
...full updated profile body — stable core facts only; omit this block if unchanged...
<<<TOPIC: the-page-slug>>>
...full updated body of that topic page, using [[wiki-links]] to related pages...
<<<INDEX>>>
...full index: one line per page as "- [[topics/slug]] — one-line summary"...
<<<END>>>

To rename or merge a topic page (this keeps inbound [[links]] correct), emit a line:
<<<RENAME: old-slug || new-slug>>>
before the TOPIC block for the new page.

Rules:
- The text in ... above is a description of what to write, NOT literal content — never
  copy it, and never emit a page named "the-page-slug". Use a real slug derived from the fact.
- Emit a TOPIC block for every page you create or change (full body, not a diff).
- If UNRESOLVED LINKS are listed below, create those pages or fix the links.
- Merge duplicates, prefer newer facts on contradiction, keep pages concise.\
"""

_PLACEHOLDER_SLUGS = {"slug-name", "the-page-slug", "slug"}

_BLOCK_RE = re.compile(
    r"<<<(?P<kind>PROFILE|TOPIC|INDEX)(?::\s*(?P<slug>[^>]+?))?\s*>>>\s*\n(?P<body>.*?)"
    r"(?=<<<(?:PROFILE|TOPIC|INDEX|RENAME|END)|\Z)",
    re.DOTALL,
)

_RENAME_RE = re.compile(r"<<<RENAME:\s*(?P<old>[^|>]+?)\s*\|\|\s*(?P<new>[^>]+?)\s*>>>")


class MemoryCompilerTask:
    def __init__(self, memory: "MarkdownMemory", client, model: str, chat_id: int):
        self._memory = memory
        self._client = client
        self._model = model
        self._chat_id = chat_id

    async def run(self, context) -> None:
        try:
            await self._compile()
        except Exception:
            logger.exception("Memory compile failed")

    async def _compile(self) -> None:
        entries = self._memory.uncompiled_entries()
        if not entries:
            return
        logger.info("Compiling %d new memory fact(s)", len(entries))

        messages = [
            {"role": "system", "content": _COMPILE_SYSTEM.format(schema=self._schema())},
            {"role": "user", "content": self._build_context(entries)},
        ]
        result = await asyncio.to_thread(
            self._client.generate, messages, thinking=True, max_tokens=4096
        )
        text = result.content or ""
        self._apply_renames(text)
        written = self._apply(text)
        if written:
            self._memory.advance_marker(len(entries))
            unresolved = self._memory.unresolved_links()
            logger.info(
                "Memory compile wrote %d page(s); %d unresolved link(s)",
                written, len(unresolved),
            )
        else:
            logger.warning("Memory compile produced no parseable pages; will retry next run")

    def _apply_renames(self, text: str) -> None:
        for m in _RENAME_RE.finditer(text):
            old, new = m.group("old").strip(), m.group("new").strip()
            if old and new and old.lower() not in _PLACEHOLDER_SLUGS:
                if self._memory.move_topic(old, new):
                    logger.info("Renamed memory page %s -> %s (links updated)", old, new)

    def _schema(self) -> str:
        try:
            return self._memory.schema_path.read_text()
        except OSError:
            return ""

    def _build_context(self, entries: list[dict]) -> str:
        parts = ["# CURRENT WIKI\n", "## profile.md\n" + self._memory.get_profile()]
        wiki = ""
        for path in sorted(self._memory.topics_dir.glob("*.md")):
            wiki += f"\n## topics/{path.stem}.md\n{path.read_text()}"
        parts.append(wiki if len(wiki) <= _MAX_WIKI_CHARS else self._memory.read_index())
        facts = "\n".join(
            f"- {'[PROFILE] ' if e['profile'] else ''}{e['content']}" for e in entries
        )
        parts.append("\n# NEW FACTS\n" + facts)
        unresolved = self._memory.unresolved_links()
        if unresolved:
            parts.append("\n# UNRESOLVED LINKS\n" + "\n".join(f"- [[{u}]]" for u in unresolved))
        return "\n".join(parts)

    def _apply(self, text: str) -> int:
        # Collect distinct page targets, last occurrence wins (the model often
        # re-emits the same page several times in one response).
        pages: dict = {}
        for m in _BLOCK_RE.finditer(text):
            kind, slug, body = m.group("kind"), m.group("slug"), m.group("body").strip()
            if self._is_placeholder(body):
                continue
            if kind == "PROFILE":
                pages[("profile", None)] = body
            elif kind == "INDEX":
                pages[("index", None)] = body
            elif kind == "TOPIC" and slug and slug.strip().lower() not in _PLACEHOLDER_SLUGS:
                pages[("topic", slug.strip())] = body

        for (kind, slug), body in pages.items():
            if kind == "profile":
                self._memory.write_profile(body)
            elif kind == "index":
                self._memory.write_index(body)
            else:
                self._memory.write_topic(slug, body)
        return len(pages)

    @staticmethod
    def _is_placeholder(body: str) -> bool:
        """Guard against the model echoing the format template's example text."""
        return not body or body == "..." or (body.startswith("...") and body.endswith("..."))
