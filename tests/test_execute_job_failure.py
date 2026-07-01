import tempfile
import unittest
from pathlib import Path

import app.main as automaycad
from app.codex_runner import CodexRunError, CodexRunResult


class ExecuteJobFailureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.old_run_codex = automaycad.run_codex
        self.old_get_job_from_db = automaycad.get_job_from_db
        self.old_save_job = automaycad.save_job
        self.old_save_job_token_usage = automaycad.save_job_token_usage
        self.jobs: dict[str, automaycad.Job] = {}

        def fake_get_job_from_db(job_id: str) -> automaycad.Job | None:
            return self.jobs.get(job_id)

        def fake_save_job(job: automaycad.Job) -> None:
            self.jobs[job.id] = job

        def fake_save_job_token_usage(*args, **kwargs) -> None:
            return None

        automaycad.get_job_from_db = fake_get_job_from_db
        automaycad.save_job = fake_save_job
        automaycad.save_job_token_usage = fake_save_job_token_usage

    async def asyncTearDown(self) -> None:
        automaycad.run_codex = self.old_run_codex
        automaycad.get_job_from_db = self.old_get_job_from_db
        automaycad.save_job = self.old_save_job
        automaycad.save_job_token_usage = self.old_save_job_token_usage

    def insert_job(self, job_id: str, task_dir: Path) -> automaycad.Job:
        scene_path = task_dir / "expected.scene"
        job = automaycad.Job(
            id=job_id,
            display_name="test",
            prompt_preview="test",
            task_dir=str(task_dir),
            requirement_path=str(task_dir / "shelf_requirements.md"),
            scene_path=str(scene_path),
            owner="admin",
            status=automaycad.JobStatus.QUEUED,
            created_at=automaycad.utc_now(),
            generated_files=[],
        )
        self.jobs[job.id] = job
        return job

    async def test_codex_error_fails_even_when_scene_exists(self) -> None:
        task_dir = self.root / "task-error-with-scene"
        task_dir.mkdir()
        (task_dir / "expected.scene").write_text("<scene />", encoding="utf-8")
        self.insert_job("job-error-with-scene", task_dir)

        async def fake_run_codex(*args, **kwargs):
            raise CodexRunError("codex boom", output="partial output")

        automaycad.run_codex = fake_run_codex

        await automaycad.execute_job("job-error-with-scene", "prompt", "codex prompt")

        job = self.jobs.get("job-error-with-scene")
        self.assertIsNotNone(job)
        self.assertEqual(job.status, automaycad.JobStatus.FAILED)
        self.assertEqual(job.error, "codex boom")
        self.assertEqual(job.result, "partial output")

    async def test_codex_success_without_scene_fails_without_fallback_files(self) -> None:
        task_dir = self.root / "task-success-no-scene"
        task_dir.mkdir()
        self.insert_job("job-success-no-scene", task_dir)

        async def fake_run_codex(*args, **kwargs):
            return CodexRunResult(output="done")

        automaycad.run_codex = fake_run_codex

        await automaycad.execute_job("job-success-no-scene", "prompt", "codex prompt")

        job = self.jobs.get("job-success-no-scene")
        self.assertIsNotNone(job)
        self.assertEqual(job.status, automaycad.JobStatus.FAILED)
        expected_error = (
            "Codex \u5df2\u5b8c\u6210\uff0c\u4f46\u4efb\u52a1"
            "\u6587\u4ef6\u5939\u4e2d\u672a\u627e\u5230 .scene "
            "\u6587\u4ef6\u3002"
        )
        self.assertEqual(job.error, expected_error)
        self.assertEqual(automaycad.scene_files(task_dir), [])
        self.assertFalse((task_dir / "maycad_skill_spec.json").exists())
        self.assertFalse((task_dir / "job-success-no-scene.scene").exists())
        self.assertFalse((task_dir / "job-success-no-scene_summary.json").exists())


if __name__ == "__main__":
    unittest.main()
