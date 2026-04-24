from pathlib import Path
import git
from git import Repo, InvalidGitRepositoryError


class GitTool:
    def __init__(self, repo_path: str):
        try:
            self.repo = Repo(repo_path, search_parent_directories=True)
        except InvalidGitRepositoryError as e:
            raise ValueError(f"Not a valid git repository: {repo_path}") from e
        assert self.repo.working_tree_dir is not None
        self.repo_path = Path(self.repo.working_tree_dir)

    def get_merge_base(self, upstream_ref: str, fork_ref: str) -> str:
        try:
            result = self.repo.git.merge_base(upstream_ref, fork_ref)
            return str(result).strip()
        except git.GitCommandError:
            return str(self.repo.git.rev_parse(upstream_ref)).strip()

    def get_changed_files(self, base: str, head: str) -> list[tuple[str, str]]:
        diff_output = self.repo.git.diff("--name-status", base, head)
        results: list[tuple[str, str]] = []
        for line in diff_output.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) < 2:
                continue
            status_raw, file_path = parts[0].strip(), parts[1].strip()
            status = status_raw[0]
            if status == "R":
                file_path = file_path.split("\t")[-1]
            results.append((status, file_path))
        return results

    def get_file_content(self, ref: str, file_path: str) -> str | None:
        try:
            return str(self.repo.git.show(f"{ref}:{file_path}"))
        except git.GitCommandError:
            return None

    def get_file_bytes(self, ref: str, file_path: str) -> bytes | None:
        """Binary-safe file read from a git ref. Use for PNG/woff/mp3/zip
        etc. where `git show` text decode would corrupt the payload."""
        try:
            result = self.repo.git.show(
                f"{ref}:{file_path}",
                stdout_as_string=False,
            )
        except git.GitCommandError:
            return None
        if isinstance(result, bytes):
            return result
        if isinstance(result, str):
            return result.encode("utf-8", errors="surrogateescape")
        return None

    def get_three_way_diff(
        self, base: str, current: str, target: str, file_path: str
    ) -> tuple[str | None, str | None, str | None]:
        base_content = self.get_file_content(base, file_path)
        current_content = self.get_file_content(current, file_path)
        target_content = self.get_file_content(target, file_path)
        return base_content, current_content, target_content

    def create_working_branch(self, branch_name: str, base_ref: str) -> str:
        self.repo.git.checkout(base_ref)
        self.repo.git.checkout("-b", branch_name)
        return branch_name

    def apply_patch(self, patch_content: str) -> bool:
        try:
            self.repo.git.apply("--check", input=patch_content)
            self.repo.git.apply(input=patch_content)
            return True
        except git.GitCommandError:
            return False

    def write_file_content(self, file_path: str, content: str) -> None:
        target = self.repo_path / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def get_commit_messages(
        self, file_path: str, ref: str, limit: int = 10
    ) -> list[str]:
        try:
            log_output = self.repo.git.log(
                "--oneline", f"-{limit}", ref, "--", file_path
            )
            return [line.strip() for line in log_output.splitlines() if line.strip()]
        except git.GitCommandError:
            return []

    def get_unified_diff(self, base: str, head: str, file_path: str) -> str:
        try:
            return str(self.repo.git.diff(base, head, "--", file_path))
        except git.GitCommandError:
            return ""

    def is_binary_file(self, ref: str, file_path: str) -> bool:
        try:
            result = self.repo.git.diff("--numstat", f"{ref}^", ref, "--", file_path)
            if result.startswith("-\t-"):
                return True
            return False
        except git.GitCommandError:
            return False

    def get_current_branch(self) -> str:
        return self.repo.active_branch.name

    def stage_file(self, file_path: str) -> None:
        self.repo.index.add([file_path])

    def get_status(self) -> list[tuple[str, str]]:
        status_output = self.repo.git.status("--porcelain")
        results: list[tuple[str, str]] = []
        for line in status_output.splitlines():
            if len(line) >= 3:
                status_code = line[:2].strip()
                path = line[3:].strip()
                results.append((status_code, path))
        return results

    def get_file_hash(self, ref: str, file_path: str) -> str | None:
        try:
            return str(self.repo.git.rev_parse(f"{ref}:{file_path}")).strip()
        except git.GitCommandError:
            return None

    def list_files(self, ref: str) -> list[str]:
        try:
            output = self.repo.git.ls_tree("-r", "--name-only", ref)
            return [line.strip() for line in output.splitlines() if line.strip()]
        except git.GitCommandError:
            return []

    def list_commits(self, base: str, head: str) -> list[dict[str, str | list[str]]]:
        try:
            log_output = self.repo.git.log(
                "--topo-order",
                "--reverse",
                "--format=%H|%an|%ae|%ai|%s",
                f"{base}..{head}",
            )
        except git.GitCommandError:
            return []
        commits: list[dict[str, str | list[str]]] = []
        for line in log_output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|", 4)
            if len(parts) < 5:
                continue
            sha = parts[0]
            files = self.get_commit_files(sha)
            commits.append(
                {
                    "sha": sha,
                    "author_name": parts[1],
                    "author_email": parts[2],
                    "date": parts[3],
                    "message": parts[4],
                    "files": files,
                }
            )
        return commits

    def get_commit_files(self, sha: str) -> list[str]:
        try:
            output = self.repo.git.diff_tree("--no-commit-id", "--name-only", "-r", sha)
            return [f.strip() for f in output.splitlines() if f.strip()]
        except git.GitCommandError:
            return []

    def get_commit_patch_id(self, sha: str) -> str | None:
        """Return the patch-id for a commit's diff (content hash ignoring metadata)."""
        try:
            diff_output = self.repo.git.diff_tree("-p", sha)
            if not diff_output.strip():
                return None
            pid_output = self.repo.git.patch_id(input=diff_output)
            return pid_output.split()[0] if pid_output.strip() else None
        except git.GitCommandError:
            return None

    def get_diff_patch_id(self, base: str, head: str, file_path: str) -> str | None:
        """Return the patch-id for a specific file diff between two refs."""
        try:
            diff_output = self.repo.git.diff(base, head, "--", file_path)
            if not diff_output.strip():
                return None
            pid_output = self.repo.git.patch_id(input=diff_output)
            return pid_output.split()[0] if pid_output.strip() else None
        except git.GitCommandError:
            return None

    def cherry_pick(self, sha: str) -> bool:
        try:
            self.repo.git.cherry_pick(sha)
            return True
        except git.GitCommandError:
            return False

    def cherry_pick_strategy_ladder(
        self, sha: str, strategies: list[tuple[str, ...]] | None = None
    ) -> tuple[bool, str]:
        """O-R3: try a sequence of cherry-pick strategies, aborting failures.

        Each strategy is a tuple of extra CLI args passed to ``git cherry-pick``.
        Returns ``(success, strategy_label)`` where ``strategy_label`` is a
        human-readable description of the strategy that succeeded, or the
        last-attempted one on failure.
        """
        ladder = strategies or [
            (),
            ("-X", "theirs"),
            ("--strategy=recursive", "-X", "patience"),
        ]
        last_label = "default"
        for args in ladder:
            label = " ".join(args) if args else "default"
            last_label = label
            try:
                if args:
                    self.repo.git.cherry_pick(*args, sha)
                else:
                    self.repo.git.cherry_pick(sha)
                return True, label
            except git.GitCommandError:
                self.cherry_pick_abort()
                continue
        return False, last_label

    def cherry_pick_per_file(
        self, sha: str, keep_files: list[str]
    ) -> tuple[bool, list[str]]:
        """O-R1: apply a subset of files from ``sha`` using ``-n`` (no-commit).

        ``keep_files`` are staged; all other modified files from the commit
        are unstaged and restored to HEAD. Caller is responsible for running
        ``commit_staged`` with a faithful author/message. Returns
        ``(success, applied_files)``.
        """
        if not keep_files:
            return False, []
        keep_set = {f.strip() for f in keep_files if f.strip()}
        try:
            self.repo.git.cherry_pick("-n", sha)
        except git.GitCommandError:
            self.cherry_pick_abort()
            return False, []
        try:
            diff_output = self.repo.git.diff("--cached", "--name-only")
            staged = [line.strip() for line in diff_output.splitlines() if line.strip()]
            drop = [f for f in staged if f not in keep_set]
            applied = [f for f in staged if f in keep_set]
            if drop:
                self.repo.git.reset("HEAD", "--", *drop)
                self.repo.git.checkout("--", *drop)
            return bool(applied), applied
        except git.GitCommandError:
            self.cherry_pick_abort()
            return False, []

    def get_commit_author_and_message(self, sha: str) -> tuple[str, str, str]:
        """Return ``(author_name, author_email, message)`` for ``sha``."""
        try:
            name = str(self.repo.git.show("-s", "--format=%an", sha)).strip()
            email = str(self.repo.git.show("-s", "--format=%ae", sha)).strip()
            message = str(self.repo.git.show("-s", "--format=%B", sha)).strip()
            return name, email, message
        except git.GitCommandError:
            return "", "", ""

    def commit_with_author(
        self, message: str, author_name: str, author_email: str
    ) -> str:
        """Commit currently staged changes with a specific author identity."""
        author_spec = f"{author_name} <{author_email}>"
        try:
            self.repo.git.commit("-m", message, "--author", author_spec)
            return str(self.repo.head.commit.hexsha)
        except git.GitCommandError:
            return ""

    def cherry_pick_abort(self) -> None:
        try:
            self.repo.git.cherry_pick("--abort")
        except git.GitCommandError:
            pass

    def commit_staged(self, message: str) -> str:
        commit = self.repo.index.commit(message)
        return str(commit.hexsha)

    def stage_files(self, file_paths: list[str]) -> None:
        if file_paths:
            self.repo.index.add(file_paths)

    def has_staged_changes(self) -> bool:
        return len(self.repo.index.diff("HEAD")) > 0

    def list_files_with_hashes(self, ref: str) -> dict[str, str]:
        """Return {file_path: blob_hash} for all files at *ref* in one call."""
        try:
            output = self.repo.git.ls_tree("-r", ref)
        except git.GitCommandError:
            return {}
        result: dict[str, str] = {}
        for line in output.splitlines():
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            meta, path = parts
            blob_hash = meta.split()[2]
            result[path] = blob_hash
        return result

    def file_exists_at_ref(self, ref: str, file_path: str) -> bool:
        return self.get_file_hash(ref, file_path) is not None

    def grep_in_file(self, pattern: str, file_path: str) -> list[str]:
        import re

        abs_path = self.repo_path / file_path
        if not abs_path.exists():
            return []
        try:
            content = abs_path.read_text(encoding="utf-8")
            return re.findall(pattern, content)
        except Exception:
            return []

    def grep_in_files(
        self, pattern: str, file_patterns: list[str]
    ) -> dict[str, list[str]]:
        import fnmatch

        all_files = [
            str(p.relative_to(self.repo_path))
            for p in self.repo_path.rglob("*")
            if p.is_file()
        ]

        target_files: list[str] = []
        for glob_pat in file_patterns:
            for fp in all_files:
                if fnmatch.fnmatch(fp, glob_pat):
                    target_files.append(fp)

        results: dict[str, list[str]] = {}
        for fp in target_files:
            matches = self.grep_in_file(pattern, fp)
            if matches:
                results[fp] = matches
        return results
