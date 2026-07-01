import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.main import (
    TASK_COMPLETE_FILE,
    Job,
    JobStatus,
    build_job_archive,
    final_scene_file,
    job_to_response,
    refresh_job_outputs,
    visible_generated_files,
)


def write_file(path: Path, text: str = "content", mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class SceneVisibilityTests(unittest.TestCase):
    def test_completion_marker_scene_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir)
            write_file(task_dir / "draft.scene")
            write_file(task_dir / "final.scene")
            write_file(
                task_dir / TASK_COMPLETE_FILE,
                json.dumps({"status": "complete", "scene": "final.scene"}),
            )

            self.assertEqual(final_scene_file(task_dir, task_dir / "draft.scene"), "final.scene")
            self.assertEqual(visible_generated_files(task_dir, task_dir / "draft.scene"), ["final.scene"])

    def test_preferred_scene_wins_without_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir)
            write_file(task_dir / "draft.scene")
            write_file(task_dir / "expected.scene")

            self.assertEqual(final_scene_file(task_dir, task_dir / "expected.scene"), "expected.scene")

    def test_latest_root_scene_wins_before_subdirectory_scene(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir)
            write_file(task_dir / "old.scene", mtime=100)
            write_file(task_dir / "new.scene", mtime=200)
            write_file(task_dir / "nested" / "newer.scene", mtime=300)

            self.assertEqual(final_scene_file(task_dir, task_dir / "missing.scene"), "new.scene")

    def test_latest_subdirectory_scene_is_used_when_no_root_scene_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir)
            write_file(task_dir / "nested" / "old.scene", mtime=100)
            write_file(task_dir / "nested" / "new.scene", mtime=200)

            self.assertEqual(final_scene_file(task_dir, task_dir / "missing.scene"), "nested/new.scene")

    def test_refresh_job_outputs_exposes_one_scene_and_keeps_support_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir)
            write_file(task_dir / "draft.scene")
            write_file(task_dir / "final.scene")
            write_file(task_dir / "summary.json", "{}")
            write_file(
                task_dir / TASK_COMPLETE_FILE,
                json.dumps({"status": "complete", "scene": "final.scene"}),
            )
            job = Job(
                id=task_dir.name,
                display_name="test",
                prompt_preview="test",
                task_dir=str(task_dir),
                requirement_path=str(task_dir / "shelf_requirements.md"),
                scene_path=str(task_dir / "draft.scene"),
                owner="admin",
                status=JobStatus.SUCCEEDED,
                created_at="2026-07-01T00:00:00+00:00",
                generated_files=[],
            )

            self.assertTrue(refresh_job_outputs(job))
            self.assertEqual(job.generated_files, ["final.scene", "summary.json"])
            self.assertTrue(job.scene_path.endswith("final.scene"))

            response = job_to_response(job)
            scene_files = [file for file in response.generated_files or [] if file.endswith(".scene")]
            self.assertEqual(scene_files, ["final.scene"])

    def test_archive_contains_only_visible_scene_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir)
            write_file(task_dir / "draft.scene")
            write_file(task_dir / "final.scene")
            write_file(task_dir / "summary.json", "{}")
            write_file(
                task_dir / TASK_COMPLETE_FILE,
                json.dumps({"status": "complete", "scene": "final.scene"}),
            )

            archive = build_job_archive(task_dir, task_dir / "draft.scene")
            with zipfile.ZipFile(archive) as zip_file:
                self.assertEqual(sorted(zip_file.namelist()), ["final.scene", "summary.json"])


if __name__ == "__main__":
    unittest.main()
