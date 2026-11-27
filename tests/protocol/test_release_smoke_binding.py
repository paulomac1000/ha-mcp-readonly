"""Exercise the release smoke step against synthetic registry identities."""

import os
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

from version import __version__


def test_release_smoke_ignores_changed_quarantine_tag(tmp_path: Path) -> None:
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / ".github/workflows/publish.yml").read_text()
    )
    smoke = next(
        step["run"]
        for step in workflow["jobs"]["validate-build"]["steps"]
        if step.get("name") == "Smoke quarantined candidate on every published platform"
    )
    output = tmp_path / "output"
    # The fake registry only admits reads of the immutable index and its two manifests.
    # The named tag does not resolve to either tested image.
    fake_registry = """
docker() {
  local ref="${!#}" amd64="sha256:$(printf '%064d' 2)" arm64="sha256:$(printf '%064d' 3)"
  local image_id="sha256:$(printf '%064d' 4)"
  case "$1 $2" in
    'buildx imagetools')
      case "$ref" in
        "$repo@$QUARANTINE_DIGEST")
          printf '{"manifests":[{"digest":"%s","platform":{"os":"linux","architecture":"amd64"}},{"digest":"%s","platform":{"os":"linux","architecture":"arm64"}}]}\\n' "$amd64" "$arm64" ;;
        "$repo@$amd64"|"$repo@$arm64") printf '{"config":{"digest":"%s"}}\\n' "$image_id" ;;
        *) return 90 ;;
      esac ;;
    'pull --platform') [[ "$ref" == "$repo@$amd64" || "$ref" == "$repo@$arm64" ]] ;;
    'image inspect') printf '%s\\n' "$image_id" ;;
    'run --pull=never')
      [[ "$*" == *"$image_id"* ]] || return 91
      if [[ "$*" == *--entrypoint=id* ]]; then printf '10001\\n'; fi ;;
    'inspect --format')
      case "$3" in
        '{{.Image}}') printf '%s\\n' "$image_id" ;;
        *) printf 'healthy\\n' ;;
      esac ;;
    'rm -f') return 0 ;;
    *) return 92 ;;
  esac
}
repo=registry.invalid/team/quarantine
"""
    result = subprocess.run(
        ["bash", "-c", fake_registry + smoke],
        env={
            "PATH": "/usr/bin:/bin",
            "QUARANTINE_REF": "registry.invalid/team/quarantine:changed",
            "QUARANTINE_DIGEST": "sha256:" + f"{1:064d}",
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text().splitlines() == [
        f"amd64_digest=sha256:{2:064d}",
        f"arm64_digest=sha256:{3:064d}",
    ]


def test_official_client_expected_version_uses_canonical_source(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    official = yaml.safe_load((root / ".github/workflows/official-client.yml").read_text())
    steps = official["jobs"]["official-client"]["steps"]
    assert "MCP_EXPECTED_VERSION" not in official.get("env", {})
    names = [step.get("name") for step in steps]
    assert names.count("Resolve canonical server version") == 1
    resolver_index = names.index("Resolve canonical server version")
    resolver = steps[resolver_index]
    assert "from version import __version__" in resolver["run"]
    assert 'os.environ["GITHUB_ENV"]' in resolver["run"]
    assert 'output.write(f"MCP_EXPECTED_VERSION={__version__}\\n")' in resolver["run"]
    github_env = tmp_path / "github-env"
    result = subprocess.run(
        ["bash", "-c", resolver["run"]],
        cwd=root,
        env={**os.environ, "GITHUB_ENV": str(github_env)},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    expected = next(
        line.split("=", 1)[1]
        for line in github_env.read_text().splitlines()
        if line.startswith("MCP_EXPECTED_VERSION=")
    )
    assert expected == __version__
    fake_verifier = tmp_path / "verify_official_mcp_client.py"
    fake_verifier.write_text(
        "import os, sys\n"
        "assert os.environ.get('MCP_EXPECTED_VERSION') == sys.argv[1]\n"
        "assert sys.argv[2] == os.environ['EXPECTED_TRANSPORT']\n",
        encoding="utf-8",
    )
    for verification_name, transport in (
        ("Official-client stdio verification", "stdio"),
        ("Official-client Streamable HTTP verification", "http"),
    ):
        assert names.count(verification_name) == 1
        verification_index = names.index(verification_name)
        assert verification_index > resolver_index
        assert "MCP_EXPECTED_VERSION" not in steps[verification_index].get("env", {})
        command = steps[verification_index]["run"]
        assert f"scripts/verify_official_mcp_client.py {transport}" in command
        executable = shlex.quote(sys.executable)
        command = command.replace("/tmp/official-client/bin/python", executable)
        command = command.replace(
            "scripts/verify_official_mcp_client.py", shlex.quote(str(fake_verifier))
        )
        command = command.replace(
            f"{shlex.quote(str(fake_verifier))} {transport}",
            f"{shlex.quote(str(fake_verifier))} {expected} {transport}",
        )
        result = subprocess.run(
            ["bash", "-c", command],
            cwd=root,
            env={
                **os.environ,
                "MCP_EXPECTED_VERSION": expected,
                "EXPECTED_TRANSPORT": transport,
            },
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    migration = yaml.safe_load((root / ".github/workflows/migration-evidence.yml").read_text())
    assert "MCP_EXPECTED_VERSION" not in migration.get("env", {})
    commands = "\n".join(
        step.get("run", "") for step in migration["jobs"].values() for step in step["steps"]
    )
    assert "from version import __version__" in commands
    assert 'MCP_EXPECTED_VERSION="$expected_version"' in commands
