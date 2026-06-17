from persistence.markdown_memory import MarkdownMemory


def _memory(tmp_path) -> MarkdownMemory:
    return MarkdownMemory(str(tmp_path))


def test_seed_creates_files(tmp_path):
    m = _memory(tmp_path)
    assert m.schema_path.exists()
    assert m.profile_path.exists()
    assert m.index_path.exists()
    assert m.log_path.exists()
    assert m.topics_dir.is_dir()


def test_append_raw_records_entries_and_profile_flag(tmp_path):
    m = _memory(tmp_path)
    m.append_raw("User is planning a trip to Italy in September")
    m.append_raw("User has a daughter Auri (9)", profile=True)

    entries = m.uncompiled_entries()
    assert len(entries) == 2
    assert entries[0]["content"] == "User is planning a trip to Italy in September"
    assert entries[0]["profile"] is False
    assert entries[1]["profile"] is True
    # multi-line content collapses to a single log line
    m.append_raw("line one\nline two")
    assert m.uncompiled_entries()[2]["content"] == "line one line two"


def test_marker_advances_so_compiled_entries_are_skipped(tmp_path):
    m = _memory(tmp_path)
    m.append_raw("fact one")
    m.append_raw("fact two")
    m.advance_marker(2)
    assert m.uncompiled_entries() == []
    m.append_raw("fact three")
    assert [e["content"] for e in m.uncompiled_entries()] == ["fact three"]


def test_get_profile_strips_heading(tmp_path):
    m = _memory(tmp_path)
    m.write_profile("Daughter Auri is 9.")
    assert m.get_profile() == "Daughter Auri is 9."


def test_search_greps_whole_vault_with_citation(tmp_path):
    m = _memory(tmp_path)
    note = tmp_path / "Home" / "Willow Uniform.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Uniform\nWillow uniform jumper is age 9-10\n")

    results = m.search("Willow uniform")
    assert len(results) == 1
    assert results[0] == "[[Home/Willow Uniform]]: Willow uniform jumper is age 9-10"


def test_search_returns_full_body_of_short_note(tmp_path):
    # A short note must come back whole, not just the line containing the query
    # word — otherwise the model thinks the note is empty.
    m = _memory(tmp_path)
    (tmp_path / "Psychology.md").write_text(
        "Trans personal psychology\nNeurotheology\nAndrew Newberg research\n"
    )
    results = m.search("psychology")
    assert results == [
        "[[Psychology]]: Trans personal psychology / Neurotheology / Andrew Newberg research"
    ]


def test_search_windows_long_note_around_the_hit(tmp_path):
    # A note longer than the cap returns lines centred on the match, not line 1.
    m = _memory(tmp_path)
    lines = [f"filler line {i}" for i in range(1200)]
    lines[800] = "the rare keyword zorblax lives here"
    (tmp_path / "Big.md").write_text("\n".join(lines) + "\n")

    result = m.search("zorblax")[0]
    assert "zorblax" in result
    assert "filler line 0" not in result  # not just the first 500 lines


def test_search_ranks_title_matches_above_body_mentions(tmp_path):
    m = _memory(tmp_path)
    (tmp_path / "Willow Uniform.md").write_text("size 5/6 purple shirt\n")
    (tmp_path / "Diary.md").write_text("today I bought a uniform for the dog\n")
    results = m.search("uniform")
    assert results[0].startswith("[[Willow Uniform]]")


def test_unresolved_links_flags_only_dangling(tmp_path):
    m = _memory(tmp_path)
    (tmp_path / "Existing.md").write_text("hi\n")
    m.write_topic("auri", "Sees [[Existing]] and [[Missing Page]].")
    assert m.unresolved_links() == ["Missing Page"]


def test_move_page_falls_back_to_rename_without_cli(tmp_path, monkeypatch):
    import persistence.markdown_memory as mm
    monkeypatch.setattr(mm.shutil, "which", lambda _: None)
    m = _memory(tmp_path)
    m.write_topic("auri", "body")

    updated = m.move_page("BatPuter/topics/auri", "BatPuter/topics/aurora")
    assert updated is False  # no CLI, so links weren't rewritten
    assert (m.topics_dir / "aurora.md").exists()
    assert not (m.topics_dir / "auri.md").exists()


def test_move_page_invokes_obsidian_cli_when_present(tmp_path, monkeypatch):
    import subprocess
    import persistence.markdown_memory as mm
    calls = {}
    monkeypatch.setattr(mm.shutil, "which", lambda _: "/usr/bin/obsidian-cli")
    monkeypatch.setattr(
        mm.subprocess, "run",
        lambda cmd, **kw: calls.setdefault("cmd", cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    m = _memory(tmp_path)
    m.write_topic("auri", "body")

    updated = m.move_page("BatPuter/topics/auri", "BatPuter/topics/aurora")
    assert updated is True
    assert calls["cmd"][:2] == ["obsidian", "move"]
    assert "path=BatPuter/topics/auri.md" in calls["cmd"]
    assert "to=BatPuter/topics/aurora.md" in calls["cmd"]
    assert any(c.startswith("vault=") for c in calls["cmd"])


def test_search_ignores_obsidian_dir_and_short_queries(tmp_path):
    m = _memory(tmp_path)
    cfg = tmp_path / ".obsidian" / "app.md"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("workspace pasta settings\n")
    (tmp_path / "Recipes.md").write_text("Pasta with garlic\n")

    # .obsidian is skipped; only the real note matches
    assert m.search("pasta") == ["[[Recipes]]: Pasta with garlic"]
    # all-short / stopword queries yield nothing
    assert m.search("the a an") == []
