"""I/O-free regression tests for exact-head CI evidence selection."""

from typing import Any

import pytest

from scripts.collect_migration_evidence import (
    REQUIRED_CI_JOBS,
    EvidenceError,
    GitHubEvidenceClient,
)


class _FakeEvidenceClient(GitHubEvidenceClient):
    def __init__(self, repository: str, head: str) -> None:
        self.repository = repository
        self.head = head
        self.current_run = 999

    def get(self, path: str) -> dict[str, Any]:
        full_run_id = 101
        fast_run_id = 202
        if "/actions/runs?" in path:
            return {
                "workflow_runs": [
                    {
                        "id": fast_run_id,
                        "name": "CI",
                        "head_sha": self.head,
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "id": full_run_id,
                        "name": "CI",
                        "head_sha": self.head,
                        "status": "completed",
                        "conclusion": "success",
                    },
                ]
            }
        if path.endswith(f"/runs/{fast_run_id}/jobs?per_page=100"):
            return {
                "jobs": [
                    {
                        "id": 1,
                        "name": "Quality and standards",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]
            }
        if path.endswith(f"/runs/{full_run_id}/jobs?per_page=100"):
            return {
                "jobs": [
                    {
                        "id": index,
                        "name": name,
                        "status": "completed",
                        "conclusion": "success",
                    }
                    for index, name in enumerate(sorted(REQUIRED_CI_JOBS), start=10)
                ]
            }
        if path.endswith(f"/runs/{full_run_id}/artifacts?per_page=100"):
            names = {
                f"python-wheel-{self.head}",
                f"container-image-{self.head}-amd64",
                f"container-image-{self.head}-arm64",
            }
            return {
                "artifacts": [
                    {
                        "id": index,
                        "name": name,
                        "digest": "sha256:" + (str(index % 10) * 64),
                    }
                    for index, name in enumerate(sorted(names), start=20)
                ]
            }
        if path.endswith(f"/runs/{fast_run_id}/artifacts?per_page=100"):
            return {"artifacts": []}
        raise AssertionError(f"unexpected GitHub API path: {path}")


class _FakeInconclusiveJobClient(_FakeEvidenceClient):
    def __init__(self, repository: str, head: str, job_name: str) -> None:
        super().__init__(repository, head)
        self.job_name = job_name

    def _jobs_once(self, run_id: int) -> dict[str, dict[str, Any]]:
        del run_id
        return {
            self.job_name: {
                "id": 303,
                "name": self.job_name,
                "status": "completed",
                "conclusion": None,
            }
        }


@pytest.fixture
def fixture_job_name() -> str:
    """Return a synthetic required CI job name for evidence-client tests."""
    return "Fixture required job"


def test_full_ci_selection_skips_newer_fast_manual_run(
    fixture_repository_name: str,
    fixture_git_sha: str,
) -> None:
    client = _FakeEvidenceClient(fixture_repository_name, fixture_git_sha)
    expected_artifacts = {
        f"python-wheel-{fixture_git_sha}",
        f"container-image-{fixture_git_sha}-amd64",
        f"container-image-{fixture_git_sha}-arm64",
    }

    run, jobs, artifacts = client.wait_for_full_ci(expected_artifacts)

    assert run["id"] == 101
    assert set(jobs) == REQUIRED_CI_JOBS
    assert {artifact["name"] for artifact in artifacts} == expected_artifacts


def test_completed_job_without_success_conclusion_fails_closed(
    fixture_repository_name: str,
    fixture_git_sha: str,
    fixture_job_name: str,
) -> None:
    client = _FakeInconclusiveJobClient(
        fixture_repository_name,
        fixture_git_sha,
        fixture_job_name,
    )

    with pytest.raises(EvidenceError, match="non-success required jobs"):
        client.wait_for_jobs(303, {fixture_job_name})
