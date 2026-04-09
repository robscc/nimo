from pathlib import Path

from casebank.storage.fs_store import FileStore


def test_fs_store_bootstrap_and_json_roundtrip(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    store.bootstrap()

    raw_path = tmp_path / "raw" / "task_events" / "2026-04-05" / "events.jsonl"
    store.append_jsonl(raw_path, {"a": 1})
    store.append_jsonl(raw_path, {"b": 2})
    rows = store.read_jsonl(raw_path)
    assert rows == [{"a": 1}, {"b": 2}]

    json_path = tmp_path / "checkpoints" / "x.json"
    store.write_json(json_path, {"ok": True})
    assert store.read_json(json_path) == {"ok": True}
