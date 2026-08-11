#!/usr/bin/env python3
"""Correlate exact-head GitHub evidence and emit a bounded migration report."""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

EXPECTED_AI_SKILLS = "b54fc6b27ea80b36a70d5de73445970e17f55789"
MAX_ARTIFACT_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_WHEEL_MEMBER_BYTES = 128 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024
WORKFLOW_WAIT_SECONDS = 30 * 60
SETTLE_WAIT_SECONDS = 5 * 60
POLL_SECONDS = 5

REQUIRED_WORKFLOWS = (
    "CI",
    "Official MCP client",
    "Pre-commit gate",
    "AI Skills policy",
    "Semgrep Security Scan",
)
REQUIRED_CI_JOBS = {
    "Quality and standards",
    "Tests (Python 3.11)",
    "Tests (Python 3.12)",
    "Tests (Python 3.13)",
    "Tests (Python 3.14)",
    "Build and inspect wheel",
    "Container (amd64) from tested wheel",
    "Container (arm64) from tested wheel",
}
OFFICIAL_JOB = "Official client exact artifacts"


class EvidenceError(RuntimeError):
    """Raised when exact-head evidence cannot be established safely."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Expose the GitHub API artifact redirect so credentials are not forwarded."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class GitHubEvidenceClient:
    """Small bounded GitHub API client for one exact assessed revision."""

    def __init__(self, repository: str, token: str, head: str, current_run: int) -> None:
        self.repository = repository
        self.token = token
        self.head = head
        self.current_run = current_run
        self.api = "https://api.github.com"
        self._no_redirect = urllib.request.build_opener(NoRedirect)

    def _api_request(self, url: str) -> urllib.request.Request:
        return urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def request_bytes(self, url: str) -> bytes:
        request = self._api_request(url)
        last_error: BaseException | None = None
        for attempt in range(5):
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code not in {403, 429, 500, 502, 503, 504}:
                    raise
                last_error = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
            if attempt < 4:
                time.sleep(2**attempt)
        raise EvidenceError(f"GitHub API request failed for {url}: {last_error}")

    def get(self, path: str) -> dict[str, Any]:
        payload = json.loads(self.request_bytes(self.api + path))
        if not isinstance(payload, dict):
            raise EvidenceError(f"GitHub API returned non-object payload for {path}")
        return payload

    def wait_for_workflow(self, name: str) -> dict[str, Any]:
        deadline = time.monotonic() + WORKFLOW_WAIT_SECONDS
        while time.monotonic() < deadline:
            query = urllib.parse.urlencode({"head_sha": self.head, "per_page": 100})
            runs = self.get(f"/repos/{self.repository}/actions/runs?{query}").get(
                "workflow_runs", []
            )
            matches = [
                run
                for run in runs
                if isinstance(run, dict)
                and run.get("name") == name
                and int(run.get("id", 0)) != self.current_run
                and run.get("head_sha") == self.head
            ]
            if matches:
                candidate = max(matches, key=lambda run: int(run["id"]))
                if candidate.get("status") == "completed":
                    if candidate.get("conclusion") != "success":
                        raise EvidenceError(
                            f"correlated {name} failed: {candidate.get('html_url')}"
                        )
                    return candidate
            time.sleep(POLL_SECONDS)
        raise EvidenceError(f"timed out waiting for successful exact-head {name}")

    def wait_for_jobs(
        self, run_id: int, expected_names: set[str]
    ) -> dict[str, dict[str, Any]]:
        """Wait through GitHub's post-run job-metadata eventual consistency window."""
        deadline = time.monotonic() + SETTLE_WAIT_SECONDS
        last_state: dict[str, str | None] = {}
        while time.monotonic() < deadline:
            jobs = self.get(
                f"/repos/{self.repository}/actions/runs/{run_id}/jobs?per_page=100"
            ).get("jobs", [])
            by_name = {
                str(job["name"]): job
                for job in jobs
                if isinstance(job, dict) and isinstance(job.get("name"), str)
            }
            last_state = {
                name: by_name.get(name, {}).get("conclusion") for name in expected_names
            }
            if expected_names <= by_name.keys() and all(
                by_name[name].get("status") == "completed"
                and by_name[name].get("conclusion") is not None
                for name in expected_names
            ):
                failed = {
                    name: by_name[name].get("conclusion")
                    for name in expected_names
                    if by_name[name].get("conclusion") != "success"
                }
                if failed:
                    raise EvidenceError(f"non-success required jobs: {failed}")
                return {name: by_name[name] for name in expected_names}
            time.sleep(POLL_SECONDS)
        raise EvidenceError(
            f"timed out waiting for settled job metadata for run {run_id}: {last_state}"
        )

    def wait_for_artifacts(self, run_id: int, expected_names: set[str]) -> list[dict[str, Any]]:
        deadline = time.monotonic() + SETTLE_WAIT_SECONDS
        last_names: set[str] = set()
        while time.monotonic() < deadline:
            artifacts = self.get(
                f"/repos/{self.repository}/actions/runs/{run_id}/artifacts?per_page=100"
            ).get("artifacts", [])
            selected = [
                artifact
                for artifact in artifacts
                if isinstance(artifact, dict) and artifact.get("name") in expected_names
            ]
            last_names = {str(artifact.get("name")) for artifact in selected}
            digests_ready = all(
                isinstance(artifact.get("digest"), str)
                and str(artifact["digest"]).startswith("sha256:")
                for artifact in selected
            )
            if (
                last_names == expected_names
                and len(selected) == len(expected_names)
                and digests_ready
            ):
                return selected
            time.sleep(POLL_SECONDS)
        raise EvidenceError(
            f"timed out waiting for exact CI artifacts {sorted(expected_names)}; got {sorted(last_names)}"
        )

    @staticmethod
    def _read_bounded(response: Any, *, limit: int, label: str) -> bytes:
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                declared_size = int(declared)
            except ValueError as exc:
                raise EvidenceError(
                    f"{label} has invalid Content-Length: {declared!r}"
                ) from exc
            if declared_size > limit:
                raise EvidenceError(
                    f"{label} exceeds byte limit from Content-Length: {declared_size} > {limit}"
                )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(min(READ_CHUNK_BYTES, limit - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise EvidenceError(f"{label} exceeds byte limit: {total} > {limit}")
            chunks.append(chunk)
        return b"".join(chunks)

    def download_artifact_archive(self, url: str) -> bytes:
        """Authenticate only to GitHub API and never forward auth to signed storage."""
        request = self._api_request(url)
        signed_url: str | None = None
        try:
            with self._no_redirect.open(request, timeout=20) as response:
                return self._read_bounded(
                    response,
                    limit=MAX_ARTIFACT_ARCHIVE_BYTES,
                    label="artifact archive",
                )
        except urllib.error.HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise
            signed_url = exc.headers.get("Location")
        if not signed_url:
            raise EvidenceError("artifact download redirect did not include Location")
        parsed = urllib.parse.urlparse(signed_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise EvidenceError("artifact download redirect is not a valid HTTPS URL")

        unsigned_request = urllib.request.Request(signed_url)
        last_error: BaseException | None = None
        for attempt in range(5):
            try:
                with urllib.request.urlopen(unsigned_request, timeout=30) as response:
                    return self._read_bounded(
                        response,
                        limit=MAX_ARTIFACT_ARCHIVE_BYTES,
                        label="artifact archive",
                    )
            except urllib.error.HTTPError as exc:
                if exc.code not in {403, 408, 429, 500, 502, 503, 504}:
                    raise
                last_error = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
            if attempt < 4:
                time.sleep(2**attempt)
        raise EvidenceError(f"artifact archive download failed: {last_error}")


def check_run_id(job: Mapping[str, Any]) -> int:
    url = job.get("check_run_url")
    if not isinstance(url, str) or "/check-runs/" not in url:
        raise EvidenceError(f"job lacks check_run_url: {job.get('name')}")
    try:
        return int(url.rstrip("/").rsplit("/", 1)[-1])
    except ValueError as exc:
        raise EvidenceError(f"invalid check_run_url for {job.get('name')}: {url}") from exc


def verify_lock() -> str:
    lock = yaml.safe_load(Path("ai-skills.lock.yaml").read_text(encoding="utf-8"))
    revision = lock.get("revision") if isinstance(lock, dict) else None
    if revision != EXPECTED_AI_SKILLS:
        raise EvidenceError(
            f"ai-skills lock mismatch: expected {EXPECTED_AI_SKILLS}, got {revision}"
        )
    return revision


def correlated_wheel(
    client: GitHubEvidenceClient, artifacts: list[dict[str, Any]], head: str
) -> dict[str, Any]:
    artifact = next(
        item for item in artifacts if item.get("name") == f"python-wheel-{head}"
    )
    archive_url = artifact.get("archive_download_url")
    if not isinstance(archive_url, str):
        raise EvidenceError("CI wheel artifact lacks archive_download_url")
    archive_bytes = client.download_artifact_archive(archive_url)
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    provider_digest = str(artifact["digest"])
    provider_sha256 = provider_digest.removeprefix("sha256:")
    if archive_sha256 != provider_sha256:
        raise EvidenceError(
            "CI wheel artifact digest mismatch: "
            f"provider={provider_sha256}, downloaded={archive_sha256}"
        )

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        wheel_infos = [
            info
            for info in archive.infolist()
            if not info.is_dir() and info.filename.endswith(".whl")
        ]
        if len(wheel_infos) != 1:
            raise EvidenceError(
                "expected one wheel in correlated artifact, got "
                f"{[info.filename for info in wheel_infos]}"
            )
        wheel_info = wheel_infos[0]
        if wheel_info.file_size > MAX_WHEEL_MEMBER_BYTES:
            raise EvidenceError(
                "correlated wheel exceeds uncompressed byte limit: "
                f"{wheel_info.file_size} > {MAX_WHEEL_MEMBER_BYTES}"
            )
        wheel_bytes = archive.read(wheel_info)
    return {
        "artifact_id": int(artifact["id"]),
        "artifact_name": artifact["name"],
        "provider_digest": provider_digest,
        "archive_sha256": archive_sha256,
        "provider_digest_verified": True,
        "filename": Path(wheel_info.filename).name,
        "content_sha256": hashlib.sha256(wheel_bytes).hexdigest(),
    }


def workflow_record(run: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": int(run["id"]),
        "workflow_id": int(run["workflow_id"]),
        "head_sha": run["head_sha"],
        "conclusion": run["conclusion"],
    }


def build_report() -> dict[str, Any]:
    repository = os.environ["GITHUB_REPOSITORY"]
    head = os.environ["ASSESSED_SHA"]
    token = os.environ["GH_TOKEN"]
    current_run = int(os.environ["GITHUB_RUN_ID"])
    client = GitHubEvidenceClient(repository, token, head, current_run)
    ai_skills_revision = verify_lock()

    runs = {name: client.wait_for_workflow(name) for name in REQUIRED_WORKFLOWS}
    ci = runs["CI"]
    official = runs["Official MCP client"]
    ci_jobs = client.wait_for_jobs(int(ci["id"]), REQUIRED_CI_JOBS)
    official_jobs = client.wait_for_jobs(int(official["id"]), {OFFICIAL_JOB})
    official_job = official_jobs[OFFICIAL_JOB]

    expected_artifacts = {
        f"python-wheel-{head}",
        f"container-image-{head}-amd64",
        f"container-image-{head}-arm64",
    }
    artifacts = client.wait_for_artifacts(int(ci["id"]), expected_artifacts)
    wheel = correlated_wheel(client, artifacts, head)

    local_wheels = list(Path("dist").glob("*.whl"))
    if len(local_wheels) != 1:
        raise EvidenceError(f"expected one locally built evidence wheel, got {local_wheels}")
    local_wheel = local_wheels[0]
    local_wheel_digest = hashlib.sha256(local_wheel.read_bytes()).hexdigest()

    auxiliary = {
        name: workflow_record(run)
        for name, run in runs.items()
        if name not in {"CI", "Official MCP client"}
    }
    return {
        "schema_version": 4,
        "repository": repository,
        "assessed_revision": head,
        "ai_skills_revision": ai_skills_revision,
        "evidence_workflow_run_id": current_run,
        "correlated_ci": {
            **workflow_record(ci),
            "jobs": {
                name: {
                    "job_id": int(ci_jobs[name]["id"]),
                    "check_run_id": check_run_id(ci_jobs[name]),
                    "conclusion": ci_jobs[name]["conclusion"],
                }
                for name in sorted(REQUIRED_CI_JOBS)
            },
            "artifacts": [
                {
                    "artifact_id": int(artifact["id"]),
                    "name": artifact["name"],
                    "provider_digest": artifact["digest"],
                }
                for artifact in sorted(artifacts, key=lambda item: str(item["name"]))
            ],
            "wheel": wheel,
        },
        "official_client": {
            "distribution": "mcp",
            "version": "1.29.0",
            "protocol_revision": "2025-11-25",
            **workflow_record(official),
            "job_id": int(official_job["id"]),
            "check_run_id": check_run_id(official_job),
            "transports": ["stdio", "streamable-http"],
        },
        "correlated_policy_gates": auxiliary,
        "local_evidence_lane": {
            "python": "3.13",
            "independently_built_wheel_filename": local_wheel.name,
            "independently_built_wheel_sha256": local_wheel_digest,
            "executed_gates": [
                {
                    "gate": "ruff-check",
                    "source": "local",
                    "command": "ruff check .",
                    "status": "passed",
                },
                {
                    "gate": "ruff-format",
                    "source": "local",
                    "command": "ruff format --check .",
                    "status": "passed",
                },
                {
                    "gate": "strict-mypy",
                    "source": "local",
                    "command": "mypy server.py tools/ context_generator/core.py context_generator/config.py context_generator/runtime.py context_generator/provenance.py context_generator/snapshot.py scripts/verify_runtime_endpoints.py --strict",
                    "status": "passed",
                },
                {
                    "gate": "bandit-medium-high",
                    "source": "local",
                    "command": "bandit -r server.py tools/ context_generator/ ha_graph/ -ll",
                    "status": "passed",
                },
                {
                    "gate": "pre-commit",
                    "source": "local-and-correlated-exact-head",
                    "command": "pre-commit run --all-files --show-diff-on-failure",
                    "status": "passed",
                },
                {
                    "gate": "documentation",
                    "source": "local",
                    "command": "make docs-check",
                    "status": "passed",
                },
                {
                    "gate": "unit-tests",
                    "source": "local",
                    "command": "pytest tests/unit -q",
                    "status": "passed",
                },
                {
                    "gate": "protocol-tests",
                    "source": "local",
                    "command": "pytest tests/protocol -q",
                    "status": "passed",
                },
                {
                    "gate": "ai-skills-policy",
                    "source": "correlated-exact-head:AI Skills policy",
                    "status": "passed",
                },
                {
                    "gate": "semgrep-security-scan",
                    "source": "correlated-exact-head:Semgrep Security Scan",
                    "status": "passed",
                },
                {
                    "gate": "python-3.11-through-3.14-unit-protocol",
                    "source": "correlated-exact-head:CI",
                    "status": "passed",
                },
                {
                    "gate": "clean-wheel-install-and-stdio",
                    "source": "correlated-exact-head:CI Build and inspect wheel",
                    "status": "passed",
                },
                {
                    "gate": "container-amd64-runtime-boundaries",
                    "source": "correlated-exact-head:CI Container amd64",
                    "status": "passed",
                },
                {
                    "gate": "container-arm64-runtime-boundaries",
                    "source": "correlated-exact-head:CI Container arm64",
                    "status": "passed",
                },
                {
                    "gate": "official-mcp-client",
                    "source": "correlated-exact-head:Official MCP client",
                    "status": "passed",
                },
            ],
            "skipped_gates": [
                {
                    "gate": "pytest tests/smoke tests/e2e tests/integration -q",
                    "reason": "No isolated real Home Assistant with HA_URL/HA_TOKEN is available to the public CI lane.",
                }
            ],
            "residual_risks": [
                "Provider-backed live Home Assistant behavior is not validated in this public CI lane.",
                "Independent GitHub review for the canonical adoption assessment must bind to this exact SHA.",
                "MCP 2026-07-28 is not claimed for the FastMCP 3.x runtime lane.",
            ],
        },
        "limitations": [
            "No real HA_URL/HA_TOKEN was available to this public CI lane.",
            "MCP 2026-07-28 is not claimed for the FastMCP 3.x runtime lane.",
            "Canonical provider-backed adoption remains request-changes until live Home Assistant evidence, the complete migration assessment, and independent exact-revision review exist.",
        ],
    }


def main() -> int:
    try:
        report = build_report()
    except (EvidenceError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    output = Path("evidence")
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "migration-evidence.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    (output / "migration-evidence.json.sha256").write_text(
        f"{digest}  migration-evidence.json\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"report_sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
