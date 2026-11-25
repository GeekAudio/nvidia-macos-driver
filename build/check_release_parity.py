#!/usr/bin/env python3
"""Compare release archive payloads without extracting either archive.

Directories and known Apple metadata do not participate in file parity. Every
other member beneath pkgroot must have the same kind and content identity.
An approved JSON report pins the complete review result, including both archive
digests, filenames, tags and every per-path identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA = "geekaudio.release-payload-parity"
SCHEMA_VERSION = 1
EXECUTABLE_MASK = 0o111


class ParityError(Exception):
    """An archive or approval report failed a safety or format check."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member_path(member: tarfile.TarInfo) -> str:
    raw_name = member.name
    if not raw_name or "\x00" in raw_name or "\\" in raw_name or raw_name.startswith("/"):
        raise ParityError(f"unsafe archive path: {raw_name!r}")

    parts = raw_name.split("/")
    if member.isdir() and parts[-1] == "":
        parts.pop()
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ParityError(f"unsafe archive path: {raw_name!r}")
    if parts[0] != "pkgroot":
        raise ParityError(f"archive member is outside top-level pkgroot: {raw_name!r}")
    return "/".join(parts)


def _validate_symlink_target(path: str, target: str) -> None:
    if not target or "\x00" in target or "\\" in target:
        raise ParityError(f"unsafe symlink target at {path!r}")

    if target.startswith("/"):
        components = target[1:].split("/")
        if not components or components[0] != "Library":
            raise ParityError(f"absolute symlink target is outside the installed Library tree at {path!r}")
        depth = 0
    else:
        components = target.split("/")
        depth = len(PurePosixPath(path).parts) - 2
    for component in components:
        if component in ("", "."):
            continue
        if component == "..":
            depth -= 1
            if depth < 0:
                raise ParityError(f"symlink escapes pkgroot at {path!r}")
        else:
            depth += 1


def _hash_member(archive: tarfile.TarFile, member: tarfile.TarInfo, path: str) -> dict[str, Any]:
    if member.isreg():
        stream = archive.extractfile(member)
        if stream is None:
            raise ParityError(f"cannot read regular file {path!r}")
        digest = hashlib.sha256()
        size = 0
        with stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        if size != member.size:
            raise ParityError(f"truncated regular file {path!r}")
        return {
            "kind": "file",
            "sha256": digest.hexdigest(),
            "executable_bits": member.mode & EXECUTABLE_MASK,
        }

    if member.issym():
        _validate_symlink_target(path, member.linkname)
        return {"kind": "symlink", "target": member.linkname}

    if member.isdir():
        raise ParityError(f"internal error: directory passed as a payload file: {path!r}")

    raise ParityError(f"unsupported archive member type at {path!r}")


def _check_member_ancestry(paths: set[str], directories: set[str]) -> None:
    files = paths - directories
    for path in files:
        parent = PurePosixPath(path).parent
        while parent != PurePosixPath("."):
            parent_name = parent.as_posix()
            if parent_name in files:
                raise ParityError(f"archive path descends through a non-directory member: {path!r}")
            parent = parent.parent


def _scan_archive(path: Path, *, reject_metadata: bool) -> tuple[str, dict[str, dict[str, Any]]]:
    if not path.is_file():
        raise ParityError(f"archive does not exist or is not a regular file: {path.name}")

    archive_digest = _sha256_file(path)
    entries: dict[str, dict[str, Any]] = {}
    all_paths: set[str] = set()
    directories: set[str] = set()
    root_directory_seen = False

    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive:
                member_path = _safe_member_path(member)
                if member_path in all_paths:
                    raise ParityError(f"duplicate archive path: {member_path!r}")
                all_paths.add(member_path)

                components = PurePosixPath(member_path).parts
                metadata_components = [component for component in components if component == ".DS_Store" or component.startswith("._")]
                is_metadata = bool(metadata_components)
                if reject_metadata and is_metadata:
                    raise ParityError(f"candidate contains archive metadata: {member_path!r}")
                if is_metadata:
                    if any(component == ".DS_Store" or component.startswith("._") for component in components[:-1]):
                        raise ParityError(f"archive metadata directory has payload beneath it: {member_path!r}")
                    if member.isdir():
                        raise ParityError(f"archive metadata path is a directory: {member_path!r}")
                    if not member.isreg():
                        raise ParityError(f"unsupported archive metadata member: {member_path!r}")
                    continue

                if member.isdir():
                    directories.add(member_path)
                    if member_path == "pkgroot":
                        root_directory_seen = True
                    continue

                entries[member_path] = _hash_member(archive, member, member_path)
    except (tarfile.TarError, OSError, EOFError) as error:
        raise ParityError(f"cannot read {path.name}: {error}") from error

    if not root_directory_seen:
        raise ParityError(f"archive lacks its top-level pkgroot directory: {path.name}")
    _check_member_ancestry(all_paths, directories)
    return archive_digest, entries


def _build_report(
    upstream_path: Path,
    candidate_path: Path,
    upstream_tag: str,
    candidate_tag: str,
) -> dict[str, Any]:
    upstream_digest, upstream_files = _scan_archive(upstream_path, reject_metadata=False)
    candidate_digest, candidate_files = _scan_archive(candidate_path, reject_metadata=True)

    paths = sorted(set(upstream_files) | set(candidate_files))
    members = [
        {
            "path": member_path,
            "upstream": upstream_files.get(member_path),
            "candidate": candidate_files.get(member_path),
        }
        for member_path in paths
    ]
    difference_count = sum(row["upstream"] != row["candidate"] for row in members)

    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "archives": {
            "upstream": {
                "filename": upstream_path.name,
                "sha256": upstream_digest,
                "tag": upstream_tag,
            },
            "candidate": {
                "filename": candidate_path.name,
                "sha256": candidate_digest,
                "tag": candidate_tag,
            },
        },
        "payload": {
            "identical": difference_count == 0,
            "upstream_member_count": len(upstream_files),
            "candidate_member_count": len(candidate_files),
            "difference_count": difference_count,
            "members": members,
        },
    }


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ParityError(f"duplicate JSON key in approved report: {key!r}")
        result[key] = value
    return result


def _json_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_json_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return left == right


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ParityError(f"report path is a symlink: {path.name}")
    contents = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(contents)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _read_approved(path: Path) -> Any:
    if not path.is_file() or path.is_symlink():
        raise ParityError(f"approved report is missing or not a regular file: {path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ParityError(f"cannot read approved report {path.name}: {error}") from error


def _same_path_or_file(left: Path, right: Path) -> bool:
    if left.resolve() == right.resolve():
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _report_paths_must_differ(
    report_path: Path,
    upstream_path: Path,
    candidate_path: Path,
    approved_path: Path | None,
) -> None:
    protected_paths = [upstream_path, candidate_path]
    if approved_path is not None:
        protected_paths.append(approved_path)
    if any(_same_path_or_file(report_path, protected) for protected in protected_paths):
        raise ParityError("--report must not alias either archive or the --approved file")


def _validate_approval_reasons(report: dict[str, Any], approved: Any) -> Any:
    if not isinstance(approved, dict) or "reasons" not in approved:
        raise ParityError("approved report must include a reasons object for reviewed path differences")
    reasons = approved["reasons"]
    if not isinstance(reasons, dict):
        raise ParityError("approved report reasons must be a JSON object")

    expected_paths = {
        row["path"]
        for row in report["payload"]["members"]
        if row["upstream"] != row["candidate"]
    }
    if set(reasons) != expected_paths:
        raise ParityError("approved report needs exactly one reason for each differing payload path")
    for path, reason in reasons.items():
        if not isinstance(reason, str) or not reason.strip() or reason.strip() != reason or re.search(r"[\x00-\x1f]", reason):
            raise ParityError(f"approved report has an empty or invalid reason for {path!r}")

    pinned_report = {key: value for key, value in approved.items() if key != "reasons"}
    return pinned_report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare two pkgroot release archives without extracting them."
    )
    parser.add_argument("upstream", type=Path, help="upstream .tar.gz archive")
    parser.add_argument("candidate", type=Path, help="candidate .tar.gz archive")
    parser.add_argument("--upstream-tag", required=True, help="tag identifying the upstream release")
    parser.add_argument("--candidate-tag", required=True, help="tag identifying the candidate release")
    parser.add_argument("--report", type=Path, required=True, help="write the deterministic review JSON here")
    parser.add_argument(
        "--approved", type=Path,
        help="compare the full result to a reviewed report with a reason for every path difference",
    )
    args = parser.parse_args(argv)
    for option in (args.upstream_tag, args.candidate_tag):
        if not option or option.strip() != option or re.search(r"[\x00-\x1f]", option):
            parser.error("release tags must be non-empty and contain no surrounding or control whitespace")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        _report_paths_must_differ(args.report, args.upstream, args.candidate, args.approved)
        report = _build_report(args.upstream, args.candidate, args.upstream_tag, args.candidate_tag)
        _write_report(args.report, report)

        if args.approved is None:
            differences = report["payload"]["difference_count"]
            if differences:
                print(f"payload differs at {differences} path(s); review {args.report.name}", file=sys.stderr)
                return 1
            print(f"payloads match; review report written to {args.report.name}")
            return 0

        approved = _read_approved(args.approved)
        pinned_report = _validate_approval_reasons(report, approved)
        if not _json_equal(report, pinned_report):
            print(f"release identity or payload differs from approved report; review {args.report.name}", file=sys.stderr)
            return 1
        differences = report["payload"]["difference_count"]
        print(f"approved report matches; recorded path differences: {differences}")
        return 0
    except ParityError as error:
        print(f"release parity check failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
