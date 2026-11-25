#!/usr/bin/env python3
"""Focused tests for the release payload parity checker."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "build/check_release_parity.py"


def add_directory(archive: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    archive.addfile(info)


def add_file(archive: tarfile.TarFile, name: str, content: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = mode
    archive.addfile(info, io.BytesIO(content))


def add_symlink(archive: tarfile.TarFile, name: str, target: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.mode = 0o777
    info.linkname = target
    archive.addfile(info)


def add_hardlink(archive: tarfile.TarFile, name: str, target: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.LNKTYPE
    info.linkname = target
    archive.addfile(info)


def write_archive(path: Path, members: list[tuple[str, str, Any]]) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        add_directory(archive, "pkgroot/")
        for name, kind, value in members:
            if kind == "directory":
                add_directory(archive, name)
            elif kind == "file":
                content, mode = value
                add_file(archive, name, content, mode)
            elif kind == "symlink":
                add_symlink(archive, name, value)
            elif kind == "hardlink":
                add_hardlink(archive, name, value)
            elif kind == "fifo":
                info = tarfile.TarInfo(name)
                info.type = tarfile.FIFOTYPE
                archive.addfile(info)
            else:
                raise ValueError(f"unknown fixture member kind: {kind}")


class ReleaseParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="release-parity-test-")
        self.folder = Path(self.temporary.name)
        self.upstream = self.folder / "upstream.tar.gz"
        self.candidate = self.folder / "candidate.tar.gz"
        self.report = self.folder / "review.json"
        self.approved = self.folder / "approved.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def compare(self, *, approved: bool = False, candidate_tag: str = "fork-v1") -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(CHECKER),
            str(self.upstream),
            str(self.candidate),
            "--upstream-tag",
            "upstream-v1",
            "--candidate-tag",
            candidate_tag,
            "--report",
            str(self.report),
        ]
        if approved:
            command.extend(["--approved", str(self.approved)])
        return subprocess.run(command, capture_output=True, text=True)

    def base_members(self) -> list[tuple[str, str, Any]]:
        return [
            ("pkgroot/Library", "directory", None),
            ("pkgroot/Install.command", "file", (b"#!/bin/sh\n", 0o755)),
            ("pkgroot/Library/libvulkan.dylib", "symlink", "libvulkan.1.dylib"),
            ("pkgroot/Library/libvulkan.1.dylib", "file", (b"vulkan loader", 0o644)),
        ]

    def test_identical_payload_allows_different_gzip_metadata_and_writes_stable_report(self) -> None:
        members = self.base_members()
        write_archive(self.upstream, members)
        write_archive(self.candidate, members)

        first = self.compare()
        self.assertEqual(first.returncode, 0, first.stderr)
        report_bytes = self.report.read_bytes()
        report = json.loads(report_bytes)
        self.assertTrue(report["payload"]["identical"])
        self.assertEqual(report["payload"]["difference_count"], 0)
        self.assertIn("pkgroot/Library/libvulkan.dylib", [row["path"] for row in report["payload"]["members"]])

        second = self.compare()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.report.read_bytes(), report_bytes)

    def test_content_executable_bits_and_symlink_literal_are_compared(self) -> None:
        write_archive(self.upstream, self.base_members())
        candidate = self.base_members()
        candidate[1] = ("pkgroot/Install.command", "file", (b"#!/bin/sh\necho changed\n", 0o700))
        candidate[2] = ("pkgroot/Library/libvulkan.dylib", "symlink", "./libvulkan.1.dylib")
        candidate.append(("pkgroot/extra", "file", (b"new", 0o644)))
        write_archive(self.candidate, candidate)

        result = self.compare()
        self.assertEqual(result.returncode, 1)
        report = json.loads(self.report.read_text(encoding="utf-8"))
        by_path = {row["path"]: row for row in report["payload"]["members"]}
        self.assertEqual(report["payload"]["difference_count"], 3)
        self.assertEqual(by_path["pkgroot/Install.command"]["upstream"]["executable_bits"], 0o111)
        self.assertEqual(by_path["pkgroot/Install.command"]["candidate"]["executable_bits"], 0o100)
        self.assertEqual(by_path["pkgroot/Library/libvulkan.dylib"]["candidate"]["target"], "./libvulkan.1.dylib")
        self.assertIsNone(by_path["pkgroot/extra"]["upstream"])

    def test_upstream_metadata_is_ignored_and_candidate_metadata_is_rejected(self) -> None:
        upstream_members = self.base_members() + [
            ("pkgroot/._Install.command", "file", (b"AppleDouble", 0o644)),
            ("pkgroot/.DS_Store", "file", (b"Finder metadata", 0o644)),
        ]
        write_archive(self.upstream, upstream_members)
        write_archive(self.candidate, self.base_members())

        result = self.compare()
        self.assertEqual(result.returncode, 0, result.stderr)

        write_archive(self.candidate, self.base_members() + [("pkgroot/._new", "file", (b"x", 0o644))])
        result = self.compare()
        self.assertEqual(result.returncode, 2)
        self.assertIn("candidate contains archive metadata", result.stderr)

    def test_unsafe_paths_links_duplicates_and_special_members_fail_closed(self) -> None:
        invalid_members = [
            [("pkgroot/../escape", "file", (b"x", 0o644))],
            [("/pkgroot/absolute", "file", (b"x", 0o644))],
            [("pkgroot/link", "symlink", "../../outside")],
            [("pkgroot/fifo", "fifo", None)],
        ]
        for members in invalid_members:
            with self.subTest(members=members):
                write_archive(self.upstream, self.base_members())
                write_archive(self.candidate, members)
                result = self.compare()
                self.assertEqual(result.returncode, 2, result.stderr)

        write_archive(self.upstream, self.base_members())
        with tarfile.open(self.candidate, mode="w:gz") as archive:
            add_directory(archive, "pkgroot/")
            add_file(archive, "pkgroot/repeated", b"first")
            add_file(archive, "pkgroot/repeated", b"second")
        result = self.compare()
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate archive path", result.stderr)

        with tarfile.open(self.candidate, mode="w:gz") as archive:
            add_directory(archive, "pkgroot/")
            add_hardlink(archive, "pkgroot/hardlink", "pkgroot/Install.command")
        result = self.compare()
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsupported archive member type", result.stderr)

    def test_approved_report_pins_complete_differences_archives_filenames_and_tags(self) -> None:
        write_archive(self.upstream, self.base_members())
        changed = self.base_members() + [("pkgroot/additional", "file", (b"approved addition", 0o644))]
        write_archive(self.candidate, changed)

        initial = self.compare()
        self.assertEqual(initial.returncode, 1)
        approved_report = json.loads(self.report.read_text(encoding="utf-8"))
        approved_report["reasons"] = {"pkgroot/additional": "This runtime file is intentionally fork-specific."}
        self.approved.write_text(json.dumps(approved_report), encoding="utf-8")

        accepted = self.compare(approved=True)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("approved report matches", accepted.stdout)

        approved_report["reasons"]["pkgroot/additional"] = "  "
        self.approved.write_text(json.dumps(approved_report), encoding="utf-8")
        blank_reason = self.compare(approved=True)
        self.assertEqual(blank_reason.returncode, 2)
        self.assertIn("empty or invalid reason", blank_reason.stderr)

        approved_report["reasons"]["pkgroot/additional"] = "This runtime file is intentionally fork-specific."
        self.approved.write_text(json.dumps(approved_report), encoding="utf-8")

        changed[-1] = ("pkgroot/additional", "file", (b"different addition", 0o644))
        write_archive(self.candidate, changed)
        rejected = self.compare(approved=True)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("differs from approved report", rejected.stderr)

        write_archive(self.candidate, changed)
        rejected_tag = self.compare(approved=True, candidate_tag="fork-v2")
        self.assertEqual(rejected_tag.returncode, 1)
        self.assertIn("differs from approved report", rejected_tag.stderr)

        approved_report.pop("reasons")
        self.approved.write_text(json.dumps(approved_report), encoding="utf-8")
        missing_reason = self.compare(approved=True)
        self.assertEqual(missing_reason.returncode, 2)
        self.assertIn("must include a reasons object", missing_reason.stderr)

    def test_report_cannot_alias_an_input_archive(self) -> None:
        write_archive(self.upstream, self.base_members())
        write_archive(self.candidate, self.base_members())
        command = [
            sys.executable,
            str(CHECKER),
            str(self.upstream),
            str(self.candidate),
            "--upstream-tag",
            "upstream-v1",
            "--candidate-tag",
            "fork-v1",
            "--report",
            str(self.upstream),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("must not alias either archive", result.stderr)

    def test_candidate_rejects_metadata_directories(self) -> None:
        write_archive(self.upstream, self.base_members())
        for members in (
            [("pkgroot/._empty-directory", "directory", None)],
            [
                ("pkgroot/._directory", "directory", None),
                ("pkgroot/._directory/file", "file", (b"payload", 0o644)),
            ],
        ):
            with self.subTest(members=members):
                write_archive(self.candidate, members)
                result = self.compare()
                self.assertEqual(result.returncode, 2)
                self.assertIn("candidate contains archive metadata", result.stderr)

    def test_report_and_approved_paths_must_be_distinct_and_approved_json_must_be_strict(self) -> None:
        write_archive(self.upstream, self.base_members())
        write_archive(self.candidate, self.base_members())
        command = [
            sys.executable,
            str(CHECKER),
            str(self.upstream),
            str(self.candidate),
            "--upstream-tag",
            "upstream-v1",
            "--candidate-tag",
            "fork-v1",
            "--report",
            str(self.report),
            "--approved",
            str(self.report),
        ]
        same_path = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(same_path.returncode, 2)
        self.assertIn("must not alias", same_path.stderr)

        self.approved.write_text('{"schema": 1, "schema": 2}', encoding="utf-8")
        result = self.compare(approved=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate JSON key", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
