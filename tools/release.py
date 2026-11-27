"""本番リリースの作業ブランチを作る。GitHub Actions ではマージ結果を検査する。"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
  return subprocess.run(["git", *args], check=check, text=True, stdout=subprocess.PIPE)


def next_version(version: str) -> str:
  match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version.strip())
  if not match:
    raise SystemExit("VERSION は 0.1.0 の形式で指定してください")
  major, minor, patch = match.groups()
  return f"{major}.{minor}.{int(patch) + 1}"


def prepare() -> None:
  if git("status", "--porcelain").stdout:
    raise SystemExit("作業ツリーをきれいにしてから実行してください")
  git("fetch", "origin", "master", "develop")
  master = git("rev-parse", "origin/master").stdout.strip()
  develop = git("rev-parse", "origin/develop").stdout.strip()
  if git("merge-base", "--is-ancestor", develop, master, check=False).returncode == 0:
    raise SystemExit("develop の変更はすでに master に入っています")
  merge_base = git("merge-base", master, develop).stdout.strip()
  code_diff = git(
    "diff", "--name-only", merge_base, develop, "--", ":/", ":(exclude,top).state/", ":(exclude,top)VERSION"
  )
  if not code_diff.stdout:
    raise SystemExit("ゲームの状態以外に公開する変更がありません")
  version = next_version(git("show", f"{master}:VERSION").stdout)
  branch = f"release/v{version}"
  previous_branch = git("symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()
  previous_commit = git("rev-parse", "HEAD").stdout.strip()
  git("switch", "-c", branch, master)
  merge = git("merge", "--no-ff", "--no-commit", develop, check=False)
  if merge.returncode and git("rev-parse", "--verify", "-q", "MERGE_HEAD", check=False).returncode:
    raise SystemExit("develop のマージを開始できませんでした")
  git("restore", f"--source={master}", "--staged", "--worktree", "--", ".state/")
  git("restore", f"--source={master}", "--staged", "--worktree", "--", "VERSION")
  if git("diff", "--cached", "--quiet", check=False).returncode == 0:
    git("merge", "--abort")
    if previous_branch:
      git("switch", previous_branch)
    else:
      git("switch", "--detach", previous_commit)
    git("update-ref", "-d", f"refs/heads/{branch}", master)
    raise SystemExit("ゲームの状態以外に公開する変更がありません")
  Path(git("rev-parse", "--show-toplevel").stdout.strip(), "VERSION").write_text(version + "\n", encoding="utf-8")
  git("add", "--", ":(top)VERSION")
  if git("diff", "--name-only", "--diff-filter=U").stdout:
    raise SystemExit(
      "VERSION と本番セーブを準備しました。コードの競合のみ解決し、git commit でマージを完了してください"
    )
  git("commit", "-m", f"Release v{version}")
  print(f"{branch} を作成しました。master 宛ての PR を作ってください。")


def check_candidate(base: str, candidate: str) -> None:
  if git("diff", "--name-only", base, candidate, "--", ".state/").stdout:
    raise SystemExit("master 宛ての PR に .state/ の変更が含まれています")
  old = git("show", f"{base}:VERSION", check=False)
  new = git("show", f"{candidate}:VERSION").stdout.strip()
  if old.returncode:
    if new != "0.1.0":
      raise SystemExit("初回の VERSION は 0.1.0 にしてください")
    return
  if new != next_version(old.stdout):
    raise SystemExit("VERSION の末尾を master より 1 つ上げてください")


def main() -> None:
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument("--check", nargs=2, metavar=("BASE", "CANDIDATE"), help="マージ結果の .state/ と VERSION を検査する")
  args = ap.parse_args()
  if args.check:
    check_candidate(*args.check)
  else:
    prepare()


if __name__ == "__main__":
  main()
