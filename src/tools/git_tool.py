from pathlib import Path
import git
from git import Repo, InvalidGitRepositoryError


class GitTool:
    def __init__(self, repo_path: str):
        try:
            self.repo = Repo(repo_path, search_parent_directories=True)
        except InvalidGitRepositoryError as e:
            raise ValueError(f"Not a valid git repository: {repo_path}") from e
        self.repo_path = Path(self.repo.working_tree_dir)

    def get_merge_base(self, upstream_ref: str, fork_ref: str) -> str:
        result = self.repo.git.merge_base(upstream_ref, fork_ref)
        return result.strip()

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
            return self.repo.git.show(f"{ref}:{file_path}")
        except git.GitCommandError:
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
            return self.repo.git.diff(base, head, "--", file_path)
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
