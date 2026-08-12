#!/usr/bin/env python3
"""Correlate exact-head GitHub evidence without executing assessed repository code."""

from __future__ import annotations

import glob
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
MAX_LOCK_BYTES = 64 * 1024
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
    """Expose the API artifact redirect so credentials are not forwarded to storage."""

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
    """Bounded GitHub API client for one exact assessed revision."""

    def __init__(self, repository: str, token: str, head: str, current_run: int) -> None:
        if not token:
            raise EvidenceError("GH_TOKEN is required only at the trusted evidence boundary")
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
            raw_runs = self.get(f"/repos/{self.repository}/actions/runs?{query}").get(
                "workflow_runs", []
            )
            if not isinstance(raw_runs, list):
                raise EvidenceError("GitHub workflow-runs payload is malformed")
            matches = [
                run
                for run in raw_runs
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

    def wait_for_jobs(self, run_id: int, expected_names: set[str]) -> dict[str, dict[str, Any]]:
        deadline = time.monotonic() + SETTLE_WAIT_SECONDS
        last_state: dict[str, dict[str, str | None]] = {}
        while time.monotonic() < deadline:
            raw_jobs = self.get(
                f"/repos/{self.repository}/actions/runs/{run_id}/jobs?per_page=100"
            ).get("jobs", [])
            if not isinstance(raw_jobs, list):
                raise EvidenceError(f"GitHub jobs payload is malformed for run {run_id}")
            by_name = {
                str(job["name"]): job
                for job in raw_jobs
                if isinstance(job, dict) and isinstance(job.get("name"), str)
            }
            last_state = {
                name: {
                    "status": by_name.get(name, {}).get("status"),
                    "conclusion": by_name.get(name, {}).get("conclusion"),
                }
                for name in expected_names
            }
            if expected_names <= by_name.keys() and all(
                by_name[name].get("status") == "completed" for name in expected_names
            ):
                failed = {
                    name: by_name[name].get("conclusion")
                    for name in expected_names
                    if by_name[name].get("conclusion") not in {None, "success"}
                }
                if failed:
                    raise EvidenceError(f"non-success required jobs: {failed}")
                return {name: by_name[name] for name in expected_names}
            time.sleep(POLL_SECONDS)
        raise EvidenceError(
            f"timed out waiting for completed job metadata for run {run_id}: {last_state}"
        )

    def wait_for_artifacts(self, run_id: int, expected_names: set[str]) -> list[dict[str, Any]]:
        deadline = time.monotonic() + SETTLE_WAIT_SECONDS
        last_names: set[str] = set()
        while time.monotonic() < deadline:
            raw_artifacts = self.get(
                f"/repos/{self.repository}/actions/runs/{run_id}/artifacts?per_page=100"
            ).get("artifacts", [])
            if not isinstance(raw_artifacts, list):
                raise EvidenceError(f"GitHub artifacts payload is malformed for run {run_id}")
            selected = [
                artifact
                for artifact in raw_artifacts
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
            f"timed out waiting for exact CI artifacts {sorted(expected_names)}; "
            f"got {sorted(last_names)}"
        )

    @staticmethod
    def _read_bounded(response: Any, *, limit: int, label: str) -> bytes:
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                declared_size = int(declared)
            except ValueError as exc:
                raise EvidenceError(f"{label} has invalid Content-Length: {declared!r}") from exc
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


def _read_file_bounded(path: Path, *, limit: int, label: str) -> bytes:
    """Read one candidate-provided evidence file without unbounded allocation."""
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise EvidenceError(f"cannot inspect {label}: {exc}") from exc
    if path.is_symlink() or not path.is_file():
        raise EvidenceError(f"{label} must be a regular non-symlink file")
    if metadata.st_size > limit:
        raise EvidenceError(f"{label} exceeds byte limit: {metadata.st_size} > {limit}")

    chunks: list[bytes] = []
    total = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(min(READ_CHUNK_BYTES, limit - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise EvidenceError(
                        f"{label} exceeds byte limit while reading: {total} > {limit}"
                    )
                chunks.append(chunk)
    except OSError as exc:
        raise EvidenceError(f"cannot read {label}: {exc}") from exc
    return b"".join(chunks)


def _check_run_id(job: Mapping[str, Any]) -> int:
    url = job.get("check_run_url")
    if not isinstance(url, str) or "/check-runs/" not in url:
        raise EvidenceError(f"job lacks check_run_url: {job.get('name')}")
    try:
        return int(url.rstrip("/").rsplit("/", 1)[-1])
    except ValueError as exc:
        raise EvidenceError(f"invalid check_run_url for {job.get('name')}: {url}") from exc


def _verify_assessed_lock(path: Path) -> str:
    raw_lock = _read_file_bounded(path, limit=MAX_LOCK_BYTES, label="assessed ai-skills lock")
    lock = yaml.safe_load(raw_lock.decode("utf-8"))
    revision = lock.get("revision") if isinstance(lock, dict) else None
    if revision != EXPECTED_AI_SKILLS:
        raise EvidenceError(
            f"ai-skills lock mismatch: expected {EXPECTED_AI_SKILLS}, got {revision}"
        )
    return revision


def _correlated_wheel(
    client: GitHubEvidenceClient, artifacts: list[dict[str, Any]], head: str
) -> dict[str, Any]:
    artifact = next(item for item in artifacts if item.get("name") == f"python-wheel-{head}")
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


def _workflow_record(run: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": int(run["id"]),
        "workflow_id": int(run["workflow_id"]),
        "head_sha": run["head_sha"],
        "conclusion": run["conclusion"],
    }


def _job_record(job: Mapping[str, Any], workflow_conclusion: str) -> dict[str, Any]:
    return {
        "job_id": int(job["id"]),
        "check_run_id": _check_run_id(job),
        "status": job.get("status"),
        "provider_conclusion": job.get("conclusion"),
        "workflow_conclusion": workflow_conclusion,
    }


def _one_local_wheel(pattern: str) -> Path:
    matches = [Path(value) for value in glob.glob(pattern)]
    if len(matches) != 1:
        raise EvidenceError(f"expected one source-lane wheel for {pattern!r}, got {matches}")
    return matches[0]


def build_report() -> dict[str, Any]:
    repository = os.environ["GITHUB_REPOSITORY"]
    head = os.environ["ASSESSED_SHA"]
    token = os.environ["GH_TOKEN"]
    current_run = int(os.environ["GITHUB_RUN_ID"])
    collector_revision = os.environ["EVIDENCE_COLLECTOR_REVISION"]
    source_lane_result = os.environ["SOURCE_LANE_RESULT"]
    if source_lane_result != "success":
        raise EvidenceError(f"credential-free source lane did not succeed: {source_lane_result}")

    lock_path = Path(os.environ.get("ASSESSED_LOCK_PATH", "source-evidence/ai-skills.lock.yaml"))
    wheel_pattern = os.environ.get("LOCAL_WHEEL_GLOB", "source-evidence/dist/*.whl")
    ai_skills_revision = _verify_assessed_lock(lock_path)
    local_wheel = _one_local_wheel(wheel_pattern)
    local_wheel_bytes = _read_file_bounded(
        local_wheel,
        limit=MAX_WHEEL_MEMBER_BYTES,
        label="credential-free source wheel",
    )
    local_wheel_digest = hashlib.sha256(local_wheel_bytes).hexdigest()

    client = GitHubEvidenceClient(repository, token, head, current_run)
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
    wheel = _correlated_wheel(client, artifacts, head)

    auxiliary = {
        name: _workflow_record(run)
        for name, run in runs.items()
        if name not in {"CI", "Official MCP client"}
    }
    return {
        "schema_version": 5,
        "repository": repository,
        "assessed_revision": head,
        "ai_skills_revision": ai_skills_revision,
        "evidence_collector_revision": collector_revision,
        "evidence_workflow_run_id": current_run,
        "credential_boundary": {
            "assessed_code_received_github_token": False,
            "collector_source": "immutable pinned checkout",
            "collector_revision": collector_revision,
        },
        "credential_free_source_lane": {
            "status": source_lane_result,
            "source": "GitHub Actions needs.source-gates.result",
            "independently_built_wheel_filename": local_wheel.name,
            "independently_built_wheel_sha256": local_wheel_digest,
            "individual_command_statuses": "not fabricated; inspect the source-gates job log",
        },
        "correlated_ci": {
            **_workflow_record(ci),
            "jobs": {
                name: _job_record(ci_jobs[name], str(ci["conclusion"]))
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
            **_workflow_record(official),
            **_job_record(official_job, str(official["conclusion"])),
            "transports": ["stdio", "streamable-http"],
        },
        "correlated_policy_gates": auxiliary,
        "executed_gates": [
            {
                "gate": "credential-free-source-lane",
                "source": "workflow-needs",
                "status": source_lane_result,
            },
            {
                "gate": "python-3.11-through-3.14-unit-protocol-and-coverage-policy",
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
                "gate": "container-arm64-build-boundaries",
                "source": "correlated-exact-head:CI Container arm64",
                "status": "passed",
            },
            {
                "gate": "pre-commit",
                "source": "correlated-exact-head:Pre-commit gate",
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

    output = Path(os.environ.get("EVIDENCE_OUTPUT_DIR", "evidence"))
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "migration-evidence.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    (output / "migration-evidence.sha256").write_text(
        f"{digest}  {report_path.name}\n", encoding="utf-8"
    )
    print(
        "Migration evidence: "
        f"revision={report['assessed_revision']} "
        f"collector={report['evidence_collector_revision']} "
        f"sha256={digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
