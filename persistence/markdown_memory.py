"""Markdown-backed long-term memory living inside an Obsidian vault.

Karpathy "LLM Wiki" layout. BatPuter writes only into an AI-owned subfolder
(``BatPuter/`` by default); the rest of the vault is read-only "raw sources"
that recall may search.

    <vault>/BatPuter/
    ├── SCHEMA.md     librarian contract (how the compiler maintains the wiki)
    ├── log.md        append-only raw facts ("source code" for the compiler)
    ├── index.md      catalog of compiled pages
    ├── profile.md    compiled core facts, always injected into the system prompt
    └── topics/       compiled topic/entity pages with [[wiki-links]]

``log.md`` is the only file ``remember`` touches (instant append). The
``MemoryCompilerTask`` later folds new log entries into ``topics/``, ``profile.md``
and ``index.md``.
"""
import datetime
import logging
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_OBSIDIAN_CLI = "obsidian"  # the official Obsidian CLI
_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

_STOPWORDS = {
    "about", "after", "again", "against", "before", "could", "doing",
    "during", "have", "into", "should", "their", "them", "there", "these",
    "they", "this", "those", "what", "when", "where", "which", "while",
    "with", "would", "your",
}

_ENTRY_RE = re.compile(r"^- \[(?P<ts>[^\]]+)\]\s*(?P<tags>(?:#\w+\s+)*)(?P<content>.+)$")

_SCHEMA_TEXT = """\
# BatPuter memory — librarian contract

This folder is maintained by BatPuter (an assistant). Humans may read it; the
assistant compiles it. Everything OUTSIDE this folder is read-only source material.

## Files
- `log.md` — append-only raw facts. Never rewritten. The compiler reads new lines and files them.
- `profile.md` — stable core facts about the user and family (names, ages, relationships,
  long-term preferences). Always loaded into context. Keep it tight.
- `topics/<slug>.md` — one page per topic or person (e.g. `topics/auri.md`). Cross-link with [[wiki-links]].
- `index.md` — one line per page: `- [[topics/<slug>]] — one-line summary`.

## Compile rules
- Route each new raw fact to the most relevant existing topic page, or create a new one.
- Promote stable, identity-level facts into `profile.md`; situational notes stay in topics.
- Merge duplicates; when a new fact contradicts an old one, prefer the newer and note the change.
- Keep pages concise and skimmable. Use `[[links]]` between related people/topics.
"""


def _keywords(query: str) -> list[str]:
    """Significant search terms: drop short tokens and stopwords."""
    return [kw for kw in query.split() if len(kw) > 3 and kw.lower() not in _STOPWORDS]


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "untitled"


class MarkdownMemory:
    def __init__(self, vault_path: str, ai_subdir: str = "BatPuter"):
        self.vault = Path(vault_path)
        self.ai_dir = self.vault / ai_subdir
        self.topics_dir = self.ai_dir / "topics"
        self.log_path = self.ai_dir / "log.md"
        self.index_path = self.ai_dir / "index.md"
        self.profile_path = self.ai_dir / "profile.md"
        self.schema_path = self.ai_dir / "SCHEMA.md"
        self._marker_path = self.ai_dir / ".compile_state"
        self._seed()

    def _seed(self) -> None:
        self.topics_dir.mkdir(parents=True, exist_ok=True)
        if not self.schema_path.exists():
            self.schema_path.write_text(_SCHEMA_TEXT)
        for path, header in (
            (self.log_path, "# Memory log (append-only)\n"),
            (self.index_path, "# Index\n"),
            (self.profile_path, "# Profile\n"),
        ):
            if not path.exists():
                path.write_text(header)

    # --- writes (remember) -------------------------------------------------

    def append_raw(self, content: str, profile: bool = False) -> None:
        """Append one timestamped fact to log.md. Cheap; no model involved."""
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        tag = "#profile " if profile else ""
        line = f"- [{ts}] {tag}{' '.join(content.split())}\n"
        with self.log_path.open("a") as f:
            f.write(line)

    # --- reads (recall / system prompt) ------------------------------------

    def get_profile(self) -> str:
        """The compiled profile body (without the leading '# Profile' heading)."""
        text = self.profile_path.read_text() if self.profile_path.exists() else ""
        return re.sub(r"^#\s+Profile\s*\n", "", text, count=1).strip()

    def search(self, query: str, limit: int = 5) -> list[str]:
        """Search every markdown note in the vault (the AI folder plus the user's
        own notes), including each note's title/path. Notes are ranked by how many
        distinct query terms they match, so the most relevant notes win. Each result
        is cited with an Obsidian [[wiki-link]] back to its source note."""
        stems = [self._stem(kw) for kw in _keywords(query)]
        if not stems:
            return []
        scored = []
        for path in sorted(self.vault.rglob("*.md")):
            if ".obsidian" in path.parts or path == self.log_path:
                continue
            try:
                text = path.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            rel = path.relative_to(self.vault).with_suffix("").as_posix()
            title = rel.replace("/", " ").lower()
            body_lines = list(dict.fromkeys(
                ln.strip() for ln in text.splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ))
            body_low = "\n".join(body_lines).lower()
            title_matched = {s for s in stems if s in title}
            matched = title_matched | {s for s in stems if s in body_low}
            if not matched:
                continue
            # A note whose *title* matches is a far stronger hit than a stray
            # body mention, so weight title matches heavily.
            score = 10 * len(title_matched) + len(matched)
            # Notes are short, so a match normally returns the whole note. Only
            # if a note exceeds the cap do we return a window of lines centred on
            # the matches (rather than just the first lines).
            snippet = " / ".join(self._window(body_lines, matched))
            scored.append((score, rel, snippet))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [f"[[{rel}]]: {snippet}" for _, rel, snippet in scored[:limit]]

    _MAX_SNIPPET_LINES = 500

    @classmethod
    def _window(cls, body_lines: list[str], matched: set[str]) -> list[str]:
        """The whole note when short; otherwise a cap-sized window centred on the
        matching lines (falling back to the start for a title-only match)."""
        if len(body_lines) <= cls._MAX_SNIPPET_LINES:
            return body_lines
        hits = [i for i, ln in enumerate(body_lines) if any(s in ln.lower() for s in matched)]
        if not hits:
            return body_lines[: cls._MAX_SNIPPET_LINES]
        center = (hits[0] + hits[-1]) // 2
        start = max(0, center - cls._MAX_SNIPPET_LINES // 2)
        return body_lines[start : start + cls._MAX_SNIPPET_LINES]

    @staticmethod
    def _stem(word: str) -> str:
        """Crude singular stem so 'uniforms' matches 'uniform' and 'sizes' 'size'."""
        w = word.lower()
        if w.endswith("s") and not w.endswith("ss") and len(w) > 4:
            return w[:-1]
        return w

    # --- compiler support --------------------------------------------------

    def _all_entries(self) -> list[str]:
        if not self.log_path.exists():
            return []
        entries = []
        for line in self.log_path.read_text().splitlines():
            m = _ENTRY_RE.match(line.strip())
            if m:
                tags = m.group("tags").split()
                entries.append({"content": m.group("content"), "profile": "#profile" in tags})
        return entries

    def uncompiled_entries(self) -> list[dict]:
        """Raw log entries not yet folded into the wiki by the compiler."""
        return self._all_entries()[self._marker():]

    def _marker(self) -> int:
        try:
            return int(self._marker_path.read_text().strip())
        except (OSError, ValueError):
            return 0

    def advance_marker(self, compiled_count: int) -> None:
        self._marker_path.write_text(str(self._marker() + compiled_count))

    def read_index(self) -> str:
        return self.index_path.read_text() if self.index_path.exists() else ""

    def write_index(self, text: str) -> None:
        self.index_path.write_text(text.rstrip() + "\n")

    def write_profile(self, body: str) -> None:
        self.profile_path.write_text("# Profile\n\n" + body.strip() + "\n")

    def move_page(self, old_rel: str, new_rel: str) -> bool:
        """Rename/move a note, keeping inbound [[links]] across the vault correct.

        Uses the official Obsidian CLI when present (it rewrites links via the
        running app, provided "Automatically update internal links" is enabled);
        otherwise falls back to a plain rename. Returns True only if the CLI
        handled the move (and thus the links). Best-effort: never raises.
        """
        old_path = self.vault / f"{old_rel}.md"
        new_path = self.vault / f"{new_rel}.md"
        if not old_path.exists() or new_path.exists():
            return False
        if shutil.which(_OBSIDIAN_CLI):
            try:
                subprocess.run(
                    [_OBSIDIAN_CLI, "move", f"path={old_rel}.md", f"to={new_rel}.md",
                     f"vault={self.vault.name}"],
                    capture_output=True, text=True, timeout=30, check=True,
                )
                return True
            except (subprocess.SubprocessError, OSError) as e:
                logger.warning("obsidian move failed (%s); falling back to rename", e)
        try:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            old_path.rename(new_path)
        except OSError as e:
            logger.warning("Page rename %s -> %s failed: %s", old_rel, new_rel, e)
        return False

    def move_topic(self, old_slug: str, new_slug: str) -> bool:
        old_rel = (self.topics_dir / f"{_slugify(old_slug)}").relative_to(self.vault).as_posix()
        new_rel = (self.topics_dir / f"{_slugify(new_slug)}").relative_to(self.vault).as_posix()
        return self.move_page(old_rel, new_rel)

    def _note_names(self) -> set[str]:
        names = set()
        for p in self.vault.rglob("*.md"):
            if ".obsidian" in p.parts:
                continue
            rel = p.relative_to(self.vault).with_suffix("")
            names.add(rel.as_posix().lower())
            names.add(rel.name.lower())
        return names

    def unresolved_links(self) -> list[str]:
        """[[wiki-links]] in BatPuter's own pages that don't resolve to a note.

        A pure-Python lint pass (no Obsidian required): the compiler is fed these
        so it can create the missing pages or fix the links on its next run.
        """
        names = self._note_names()
        unresolved, seen = [], set()
        skip = {self.schema_path, self.log_path}
        for p in sorted(self.ai_dir.rglob("*.md")):
            if p in skip:
                continue
            try:
                text = p.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            for m in _LINK_RE.finditer(text):
                target = m.group(1).split("|")[0].split("#")[0].strip()
                if not target or target in seen:
                    continue
                candidates = {target.lower(), target.lower().split("/")[-1]}
                if not (candidates & names):
                    seen.add(target)
                    unresolved.append(target)
        return unresolved

    def read_topic(self, slug: str) -> str:
        path = self.topics_dir / f"{_slugify(slug)}.md"
        return path.read_text() if path.exists() else ""

    def write_topic(self, slug: str, body: str) -> str:
        slug = _slugify(slug)
        (self.topics_dir / f"{slug}.md").write_text(body.strip() + "\n")
        return slug
