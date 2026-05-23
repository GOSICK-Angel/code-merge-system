import logging
from pathlib import Path
import git
from git import Repo, InvalidGitRepositoryError

logger = logging.getLogger(__name__)


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
        raw = self.get_file_bytes(ref, file_path)
        if raw is None:
            return None
        return raw.decode("utf-8", errors="surrogateescape")

    def get_file_bytes(self, ref: str, file_path: str) -> bytes | None:
        """Binary-safe file read from a git ref. Use for PNG/woff/mp3/zip
        etc. where `git show` text decode would corrupt the payload.

        strip_newline_in_stdout=False is critical: GitPython's default
        (True) silently drops a trailing 0x0a, which broke B-class
        take_target byte-equality with upstream blobs.
        """
        try:
            result = self.repo.git.show(
                f"{ref}:{file_path}",
                stdout_as_string=False,
                strip_newline_in_stdout=False,
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

    def three_way_merge_file(
        self,
        base_ref: str,
        ours_ref: str,
        theirs_ref: str,
        file_path: str,
    ) -> str | None:
        """Attempt git's native line-level 3-way merge for one file.

        Returns the merged content on a clean merge (no conflict markers,
        git exit 0). Returns ``None`` on any conflict, missing ref, or
        error — caller must then fall back to LLM-driven semantic merge.

        Side-effect free: operates on temp files; does not touch the
        worktree or the index.

        Calibrated via P-γ-1.5: covers C-class files where fork and
        upstream edited disjoint line ranges (e.g. fork edits manifest
        ``author`` line 1, upstream edits ``version`` line 37) — git's
        3-way merge resolves these deterministically without LLM.
        """
        import tempfile

        base_content = self.get_file_content(base_ref, file_path)
        ours_content = self.get_file_content(ours_ref, file_path)
        theirs_content = self.get_file_content(theirs_ref, file_path)
        if base_content is None or ours_content is None or theirs_content is None:
            return None

        with tempfile.TemporaryDirectory() as td:
            base_p = Path(td) / "base"
            ours_p = Path(td) / "ours"
            theirs_p = Path(td) / "theirs"
            base_p.write_text(base_content, encoding="utf-8")
            ours_p.write_text(ours_content, encoding="utf-8")
            theirs_p.write_text(theirs_content, encoding="utf-8")
            try:
                output = self.repo.git.merge_file(
                    "--stdout",
                    "-L",
                    "fork",
                    "-L",
                    "base",
                    "-L",
                    "upstream",
                    str(ours_p),
                    str(base_p),
                    str(theirs_p),
                    strip_newline_in_stdout=False,
                )
            except git.GitCommandError:
                # exit code > 0 = conflicts; defer to LLM.
                return None

        text = (
            output
            if isinstance(output, str)
            else output.decode("utf-8", errors="surrogateescape")
        )
        if "<<<<<<< " in text or "\n=======\n" in text or ">>>>>>> " in text:
            return None
        return text

    def three_way_merge_file_union(
        self,
        base_ref: str,
        ours_ref: str,
        theirs_ref: str,
        file_path: str,
    ) -> str | None:
        """Union merge: keep all changes from BOTH sides, ordered by
        position. Drives the ``union_additions`` user decision — both
        fork and upstream only added lines, so concatenation in place
        of conflict markers is what the reviewer wants.

        Returns the merged content, or ``None`` if any input ref is
        missing or git refuses to merge (e.g. binary).
        """
        import tempfile

        base_content = self.get_file_content(base_ref, file_path)
        ours_content = self.get_file_content(ours_ref, file_path)
        theirs_content = self.get_file_content(theirs_ref, file_path)
        if base_content is None or ours_content is None or theirs_content is None:
            return None

        with tempfile.TemporaryDirectory() as td:
            base_p = Path(td) / "base"
            ours_p = Path(td) / "ours"
            theirs_p = Path(td) / "theirs"
            base_p.write_text(base_content, encoding="utf-8")
            ours_p.write_text(ours_content, encoding="utf-8")
            theirs_p.write_text(theirs_content, encoding="utf-8")
            try:
                output = self.repo.git.merge_file(
                    "--union",
                    "--stdout",
                    "-L",
                    "fork",
                    "-L",
                    "base",
                    "-L",
                    "upstream",
                    str(ours_p),
                    str(base_p),
                    str(theirs_p),
                    strip_newline_in_stdout=False,
                )
            except git.GitCommandError:
                return None

        return (
            output
            if isinstance(output, str)
            else output.decode("utf-8", errors="surrogateescape")
        )

    def create_working_branch(self, branch_name: str, base_ref: str) -> str:
        from datetime import datetime

        resolved = branch_name.replace(
            "{timestamp}", datetime.now().strftime("%Y%m%d-%H%M%S")
        )
        unmerged = self.repo.git.ls_files("--unmerged")
        if unmerged.strip():
            self.repo.git.reset("--hard", "HEAD")
        # Remove .merge/ from the index before checkout. If a previous run
        # committed .merge/ to this branch (pre-fix), the tracked state would
        # cause "changes would be overwritten" and block the checkout.
        try:
            self.repo.git.rm("--cached", "-r", "--ignore-unmatch", "--", ".merge/")
        except Exception as exc:
            logger.warning("create_working_branch: untrack .merge/ failed: %s", exc)
        self.repo.git.checkout(base_ref)
        self.repo.git.checkout("-b", resolved)
        return resolved

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

    def checkout_file(self, ref: str, file_path: str) -> bool:
        """Restore one path to its content at ``ref`` in BOTH the index and
        the working tree, clearing any unmerged (conflict) state left by a
        cherry-pick fall-back.

        Used to drop conflict markers on C-class files before routing them to
        conflict analysis (which reads clean content from refs anyway), so the
        interim auto_merge commit never captures marker-laden content.
        """
        try:
            self.repo.git.checkout(ref, "--", file_path)
            return True
        except git.GitCommandError:
            return False

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

    def reload_index(self) -> None:
        """Re-read `.git/index` from disk. Needed after we use CLI
        (`git add`, `git rm --cached`) that bypasses the GitPython
        IndexFile cache; without this `repo.index.commit()` still sees
        the stale (unmerged) entries and raises UnmergedEntriesError."""
        try:
            # `Repo.index` is a read-only property in the public typeshed
            # but GitPython stores the backing IndexFile on `_index`.
            # Replacing it forces a fresh read on next attribute access.
            self.repo._index = git.IndexFile(self.repo)  # type: ignore[attr-defined]
        except Exception:
            pass

    def get_unmerged_files(self) -> list[str]:
        """Return paths that have unmerged index entries (stages 1/2/3),
        i.e. leftover conflicts from cherry-pick / merge fallback. Returns
        each path once even if multiple stages are present.

        `git ls-files -u` output format: `<mode> <sha> <stage>\\t<path>`.
        We do not use `--name-only` because it is not supported on older
        git versions that still ship in many CI images."""
        try:
            output = self.repo.git.ls_files("-u")
        except git.GitCommandError:
            return []
        seen: set[str] = set()
        result: list[str] = []
        for line in output.splitlines():
            if "\t" not in line:
                continue
            path = line.split("\t", 1)[1].strip()
            if path and path not in seen:
                seen.add(path)
                result.append(path)
        return result

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

    def get_head_sha(self) -> str:
        return str(self.repo.git.rev_parse("HEAD")).strip()

    def get_worktree_blob_sha(self, file_path: str) -> str | None:
        abs_path = self.repo_path / file_path
        if not abs_path.exists():
            return None
        try:
            return str(self.repo.git.hash_object(str(abs_path))).strip()
        except git.GitCommandError:
            return None

    def diff_files_between(self, before_sha: str, after_sha: str) -> list[str]:
        if before_sha == after_sha:
            return []
        try:
            output = self.repo.git.diff("--name-only", before_sha, after_sha)
            return [line.strip() for line in output.splitlines() if line.strip()]
        except git.GitCommandError:
            return []

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
                if not self.cherry_pick_abort():
                    logger.warning(
                        "cherry_pick_strategy_ladder: abort failed for %s "
                        "after strategy %s — bailing out to prevent "
                        "cascading 'cherry-pick already in progress' errors",
                        sha[:8],
                        label,
                    )
                    return False, label
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

    def cherry_pick_abort(self) -> bool:
        """Clean up after a failed cherry-pick so the next op starts clean.

        Two failure shapes need different handling:
          * full ``cherry-pick`` → leaves CHERRY_PICK_HEAD / a sequencer
            dir; ``--abort`` is the correct unwind.
          * ``cherry-pick -n`` (O-R1 per-file) → never creates sequencer
            state, so ``--abort`` errors with "no cherry-pick in progress"
            while the partial application still dirties the index/worktree.
            Left uncleaned, the next commit's cherry-pick starts from a
            dirty tree and cascades into spurious O-R4 bail-outs. Discard
            the partial with ``reset --hard`` instead.
        """
        git_dir = Path(self.repo.git_dir)
        sequencer_active = (git_dir / "CHERRY_PICK_HEAD").exists() or (
            git_dir / "sequencer"
        ).is_dir()
        if sequencer_active:
            # Full cherry-pick failure: ``--abort`` is the only correct
            # unwind (``reset --hard`` would NOT clear CHERRY_PICK_HEAD, so
            # the next cherry-pick would still hit "previous cherry-pick
            # still in progress"). If ``--abort`` fails the sequencer is
            # genuinely stuck — return False so the strategy ladder bails
            # out instead of cascading (Run 6dd6a513 P0 hang vector).
            try:
                self.repo.git.cherry_pick("--abort")
                return True
            except git.GitCommandError as exc:
                logger.warning(
                    "cherry_pick_abort failed (sequencer stuck, worktree "
                    "still holds CHERRY_PICK_HEAD or unmerged paths): %s",
                    exc,
                )
                return False
        # No sequencer state — a failed ``cherry-pick -n`` (O-R1 per-file)
        # never creates one, so ``--abort`` would error with "no cherry-pick
        # in progress" while its partial application still dirties the tree.
        # Discard the partial with reset --hard so the next op starts clean.
        try:
            self.repo.git.reset("--hard", "HEAD")
            return True
        except git.GitCommandError as exc:
            logger.warning("cherry_pick_abort: reset --hard cleanup failed: %s", exc)
            return False

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

    def detect_renames(self, base_ref: str, head_ref: str) -> list[tuple[str, str]]:
        """Return (old_path, new_path) pairs for files renamed between base_ref and head_ref.

        Uses ``git diff -M --name-status`` which scores renames by content
        similarity (default threshold 50%). Empty list on any git error.
        """
        try:
            output = self.repo.git.diff("-M", "--name-status", base_ref, head_ref)
        except git.GitCommandError:
            return []
        pairs: list[tuple[str, str]] = []
        for line in output.splitlines():
            if not line or not line.startswith("R"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            old_path, new_path = parts[1].strip(), parts[2].strip()
            if old_path and new_path:
                pairs.append((old_path, new_path))
        return pairs

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
