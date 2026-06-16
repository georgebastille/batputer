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
