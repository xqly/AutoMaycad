import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.codex_runner import (
    CodexTokenUsage,
    extract_token_usage_from_session,
    find_codex_token_usage,
)
from app.main import TOKEN_USAGE_COLUMNS, ensure_jobs_schema


def token_event(
    *,
    input_tokens: int | None = 10,
    cached_input_tokens: int | None = 2,
    output_tokens: int | None = 3,
    reasoning_output_tokens: int | None = 1,
    total_tokens: int | None = 13,
) -> str:
    usage = {
        key: value
        for key, value in {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "reasoning_output_tokens": reasoning_output_tokens,
            "total_tokens": total_tokens,
        }.items()
        if value is not None
    }
    return json.dumps(
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": usage},
            },
        }
    )


def write_session(path: Path, lines: list[str], mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class CodexTokenUsageParserTests(unittest.TestCase):
    def test_extracts_final_cumulative_usage_for_matching_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "session.jsonl"
            write_session(
                session_path,
                [
                    json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "job-123"}}),
                    token_event(input_tokens=10, cached_input_tokens=1, output_tokens=2, reasoning_output_tokens=0, total_tokens=12),
                    token_event(input_tokens=20, cached_input_tokens=4, output_tokens=6, reasoning_output_tokens=3, total_tokens=26),
                ],
            )

            self.assertEqual(
                extract_token_usage_from_session(session_path, "job-123"),
                CodexTokenUsage(
                    input_tokens=20,
                    cached_input_tokens=4,
                    output_tokens=6,
                    reasoning_output_tokens=3,
                    total_tokens=26,
                ),
            )

    def test_ignores_unrelated_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "session.jsonl"
            write_session(
                session_path,
                [
                    json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "other-job"}}),
                    token_event(),
                ],
            )

            self.assertIsNone(extract_token_usage_from_session(session_path, "job-123"))

    def test_tolerates_malformed_jsonl_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "session.jsonl"
            write_session(
                session_path,
                [
                    "{not-json",
                    json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "job-123"}}),
                    token_event(input_tokens=5, cached_input_tokens=0, output_tokens=7, reasoning_output_tokens=2, total_tokens=12),
                ],
            )

            usage = extract_token_usage_from_session(session_path, "job-123")
            self.assertEqual(usage.total_tokens if usage else None, 12)

    def test_missing_token_fields_return_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "session.jsonl"
            write_session(
                session_path,
                [
                    json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "job-123"}}),
                    token_event(total_tokens=None),
                ],
            )

            self.assertIsNone(extract_token_usage_from_session(session_path, "job-123"))

    def test_find_uses_updated_matching_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            sessions_dir = Path(temp_dir)
            write_session(
                sessions_dir / "old.jsonl",
                [
                    json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "job-123"}}),
                    token_event(total_tokens=9),
                ],
                mtime=100,
            )
            write_session(
                sessions_dir / "new.jsonl",
                [
                    json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "job-123"}}),
                    token_event(input_tokens=30, cached_input_tokens=5, output_tokens=8, reasoning_output_tokens=4, total_tokens=38),
                ],
                mtime=200,
            )

            usage = find_codex_token_usage(sessions_dir, "job-123", updated_since=150)
            self.assertEqual(usage.total_tokens if usage else None, 38)


class JobTokenUsageSchemaTests(unittest.TestCase):
    def test_schema_migration_adds_nullable_token_columns(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                prompt_preview TEXT NOT NULL,
                task_dir TEXT NOT NULL,
                requirement_path TEXT NOT NULL,
                scene_path TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                generated_files TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO jobs (
                id,
                prompt_preview,
                task_dir,
                requirement_path,
                scene_path,
                status,
                created_at,
                generated_files,
                updated_at
            )
            VALUES ('job-123', 'prompt', 'task', 'req', 'scene', 'queued', 'now', '[]', 'now')
            """
        )

        ensure_jobs_schema(connection)

        columns = {
            row["name"]: row
            for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
        }
        for column in TOKEN_USAGE_COLUMNS:
            self.assertIn(column, columns)
            self.assertEqual(columns[column]["type"], "INTEGER")
            self.assertFalse(columns[column]["notnull"])

        row = connection.execute("SELECT * FROM jobs WHERE id = 'job-123'").fetchone()
        self.assertEqual(row["display_name"], "未命名任务")
        self.assertEqual(row["owner"], "admin")
        for column in TOKEN_USAGE_COLUMNS:
            self.assertIsNone(row[column])


if __name__ == "__main__":
    unittest.main()
