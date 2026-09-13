"""`scripts/publish_trend.sh` — the remote decides whether a publish happened (RC1-415).

Each test builds a throwaway repo with a bare `origin` and a fake `uv` on PATH
that writes the page instead of rendering it from the store. The 2026-09-08
shape is reproduced with a client-side pre-push hook that lands the commit on
`origin` itself and then declines: the push command fails, and gh-pages is
already at the commit being pushed — which is what the client saw that morning.
(A server-side hook cannot do this: refs are frozen inside the push quarantine.)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish_trend.sh"

_FAKE_UV = """#!/bin/sh
# Stands in for `uv run python -m agent_evals.trend_cli --out site/index.html`.
mkdir -p site && printf '%s' "$FAKE_PAGE" > site/index.html
"""


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(origin), cwd=tmp_path)
    work = tmp_path / "work"
    _git("init", "-q", "-b", "main", str(work), cwd=tmp_path)
    (work / "README.md").write_text("main branch, untouched by a publish\n")
    _git("add", ".", cwd=work)
    _git("commit", "-q", "-m", "init", cwd=work)
    _git("remote", "add", "origin", str(origin), cwd=work)
    scripts = work / "scripts"
    scripts.mkdir()
    (scripts / "publish_trend.sh").write_text(SCRIPT.read_text())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "uv"
    fake.write_text(_FAKE_UV)
    fake.chmod(0o755)
    return work


def _publish(work: Path, page: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{work.parent / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "FAKE_PAGE": page,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    return subprocess.run(
        ["bash", str(work / "scripts" / "publish_trend.sh")],
        cwd=work, env=env, capture_output=True, text=True,
    )


def _gh_pages(work: Path) -> str:
    return _git("rev-parse", "refs/heads/gh-pages", cwd=work.parent / "origin.git")


def _page_at(work: Path, commit: str) -> str:
    return _git("show", f"{commit}:index.html", cwd=work.parent / "origin.git")


def _declining_hook(work: Path, *, land_first: bool) -> None:
    hook = work / ".git" / "hooks" / "pre-push"
    body = '#!/bin/sh\n[ -n "$NESTED" ] && exit 0\nread local_ref local_sha remote_ref remote_sha\n'
    if land_first:
        body += 'NESTED=1 git push -q -f "$(git remote get-url origin)" "$local_sha:$remote_ref"\n'
    body += "echo HOOK-DECLINED >&2\nexit 1\n"
    hook.write_text(body)
    hook.chmod(0o755)


def test_publishes_a_parentless_commit_and_leaves_the_working_tree_alone(repo):
    result = _publish(repo, "<h1>one</h1>")

    assert result.returncode == 0, result.stderr
    published = result.stdout.strip().splitlines()[-1].split()[1]
    assert _gh_pages(repo) == published
    assert _page_at(repo, published) == "<h1>one</h1>"
    assert _git("rev-list", "--count", published, cwd=repo) == "1"  # no parent
    assert _git("branch", "--show-current", cwd=repo) == "main"
    assert _git("status", "--porcelain", "--", "README.md", cwd=repo) == ""


def test_a_republish_in_either_order_wins_without_a_rejected_push(repo):
    # Manual republish, then the scheduled run — and again the other way round.
    # The push is of a raw commit against a fresh ref advertisement, so there is
    # no stale local state to lose to; this pins that down.
    for page in ("manual", "daily", "manual again"):
        result = _publish(repo, page)
        assert result.returncode == 0, result.stderr
        assert "rejected" not in result.stdout + result.stderr
        assert _page_at(repo, _gh_pages(repo)) == page


def test_a_rejection_with_the_ref_already_at_our_commit_is_a_publish(repo):
    # The 2026-09-08 shape: the update was applied, the client heard a rejection.
    _declining_hook(repo, land_first=True)

    result = _publish(repo, "landed anyway")

    assert result.returncode == 0, result.stderr
    assert "push reported a rejection but gh-pages is already at" in result.stdout
    assert result.stdout.strip().endswith(f"published {_gh_pages(repo)} to gh-pages")
    assert _page_at(repo, _gh_pages(repo)) == "landed anyway"


def test_a_rejection_that_left_the_ref_elsewhere_retries_once_then_fails(repo):
    before = _publish(repo, "yesterday")
    assert before.returncode == 0, before.stderr
    stale = _gh_pages(repo)
    _declining_hook(repo, land_first=False)

    result = _publish(repo, "today")

    assert result.returncode == 1
    assert "retrying once" in result.stdout
    assert result.stderr.count("HOOK-DECLINED") == 2  # first push, then the retry
    assert f"PUBLISH FAILED: gh-pages is at {stale}" in result.stderr
    assert _gh_pages(repo) == stale
    assert _page_at(repo, stale) == "yesterday"
