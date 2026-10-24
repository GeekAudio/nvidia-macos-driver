#!/usr/bin/env python3
"""Replay local NVMTL_FC_DUMP_ALL/FAIL_DUMP .ll/.txt pairs without using a GPU.

Every translation runs in a fresh child with a time limit. This replays the AIR,
stage and function constants; runtime resource states and dynamic threadgroup
lengths are not in the dump. Provide the latter explicitly when needed. A zero
translator return code does not establish SPIR-V validity or GPU correctness.
"""
import argparse
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_dump(path):
    text = path.with_suffix(".txt").read_text()
    stage = None
    constants = []
    for line in text.splitlines():
        fields = line.split()
        if fields[:1] == ["stage"]:
            stage = fields[1]
        elif fields[:1] == ["fc"]:
            if len(fields) < 5 or fields[2] != "size" or fields[4] != "bytes":
                raise ValueError(f"malformed function constant: {line}")
            index, size = int(fields[1]), int(fields[3])
            data = bytes.fromhex("".join(fields[5:]))
            if not 0 <= index <= 0xFFFFFFFF or len(data) != size:
                raise ValueError(f"function constant index/length mismatch: {line}")
            constants.append({"index": index, "size": size, "hex": data.hex()})
    if stage not in ("kernel", "vertex", "fragment"):
        raise ValueError("missing or invalid stage")
    if len({c["index"] for c in constants}) != len(constants):
        raise ValueError("duplicate function constant index")
    return stage, constants


def threadgroups(specs):
    values = {}
    for spec in specs:
        index, length = map(int, spec.split("=", 1))
        if not 0 <= index < 32 or not 0 <= length <= 0xFFFFFFFF:
            raise ValueError("threadgroup must be INDEX=BYTES with index 0..31 and uint32 bytes")
        values[index] = length
    return [values.get(i, 0) for i in range(max(values, default=-1) + 1)]


def worker(args):
    path = args.inputs[0].resolve()
    stage, constants = read_dump(path)
    library = C.CDLL(str(args.library.resolve()))
    u32p = C.POINTER(C.c_uint32)
    bytep = C.POINTER(C.c_uint8)
    translator = library.nvmtl_translate_fc
    translator.argtypes = [C.c_char_p, C.c_char_p, u32p, u32p, C.POINTER(bytep), C.c_size_t,
                          C.POINTER(bytep), C.POINTER(C.c_size_t), C.c_char_p, C.c_size_t]
    translator.restype = C.c_int
    free = library.nvmtl_translate_free
    free.argtypes = [bytep, C.c_size_t]
    free.restype = None
    setcaps = library.nvmtl_translate_set_caps
    setcaps.argtypes = [C.c_uint32]
    setcaps.restype = None
    setcaps(args.caps)
    lengths = threadgroups(args.threadgroup)
    if lengths:
        settg = library.nvmtl_translate_set_threadgroup_lengths
        settg.argtypes = [u32p, C.c_uint32]
        settg.restype = None
        settg((C.c_uint32 * len(lengths))(*lengths), len(lengths))
    count = len(constants)
    indices = (C.c_uint32 * count)(*(c["index"] for c in constants))
    sizes = (C.c_uint32 * count)(*(c["size"] for c in constants))
    buffers = [(C.c_uint8 * c["size"]).from_buffer_copy(bytes.fromhex(c["hex"])) for c in constants]
    payloads = (bytep * count)(*(C.cast(buf, bytep) for buf in buffers))
    output = bytep()
    output_len = C.c_size_t()
    error = C.create_string_buffer(8192)
    started = time.monotonic()
    rc = translator(path.read_bytes(), stage.encode(), indices, sizes, payloads, count,
                    C.byref(output), C.byref(output_len), error, len(error))
    result = {"translator_return": rc, "translated": rc == 0,
              "translation_seconds": round(time.monotonic() - started, 6),
              "error": error.value.decode(errors="replace"), "validated": False}
    try:
        if rc == 0:
            if not output or output_len.value < 20 or output_len.value % 4:
                raise ValueError("translator returned an invalid output pointer or length")
            data = C.string_at(output, output_len.value)
            destination = args.output / (path.stem + ".spv")
            destination.write_bytes(data)
            result.update(output=str(destination), output_bytes=len(data), output_sha256=sha(data),
                          spirv_magic=data[:4].hex(), structurally_plausible=data[:4] == bytes.fromhex("03022307"))
    finally:
        if output:
            free(output, output_len)
    print(json.dumps(result))


def limits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024 * 1024, 128 * 1024 * 1024))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--caps", type=int, default=3, help="driver uses 3 for formatless image capabilities")
    parser.add_argument("--threadgroup", action="append", default=[], metavar="INDEX=BYTES")
    parser.add_argument("--validator", help="optional spirv-val executable path, or 'auto'")
    parser.add_argument("--target-env", default="vulkan1.2", help="SPIR-V validation target; matches translator/src/tools.rs")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("inputs", type=Path, nargs="+", help="local dump .ll files with adjacent .txt metadata")
    args = parser.parse_args()
    if args.timeout <= 0 or not 0 <= args.caps <= 0xFFFFFFFF:
        parser.error("timeout must be positive and caps must fit uint32")
    threadgroups(args.threadgroup)
    args.library = args.library.resolve(strict=True)
    args.output = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix="nvmtl-fc-replay-"))
    args.output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        worker(args)
        return 0
    validator = shutil.which("spirv-val") if args.validator == "auto" else args.validator
    if args.validator and not validator:
        parser.error("requested validator is unavailable")
    lib_sha = sha(args.library.read_bytes())
    results = []
    for path in args.inputs:
        path = path.resolve(strict=True)
        stage, constants = read_dump(path)
        constant_json = json.dumps(constants, sort_keys=True, separators=(",", ":")).encode()
        result = {"input": str(path), "input_sha256": sha(path.read_bytes()),
                  "metadata_sha256": sha(path.with_suffix(".txt").read_bytes()),
                  "constants_sha256": sha(constant_json), "constants": constants, "stage": stage,
                  "library": str(args.library), "library_sha256": lib_sha, "caps": args.caps,
                  "threadgroup_lengths": threadgroups(args.threadgroup), "validated": False,
                  "target_env": args.target_env,
                  "translated": False, "timeout": False}
        cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", "--library", str(args.library),
               "--output", str(args.output), "--caps", str(args.caps), str(path)]
        for spec in args.threadgroup:
            cmd += ["--threadgroup", spec]
        started = time.monotonic()
        with (args.output / (path.stem + ".stdout.log")).open("wb") as stdout, (args.output / (path.stem + ".stderr.log")).open("wb") as stderr:
            child = subprocess.Popen(cmd, stdout=stdout, stderr=stderr, start_new_session=True, preexec_fn=limits)
            try:
                result["exit_code"] = child.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                result["timeout"] = True
                os.killpg(child.pid, signal.SIGKILL)
                result["exit_code"] = child.wait(timeout=5)
        result["seconds"] = round(time.monotonic() - started, 6)
        if result["exit_code"] == 0:
            lines = (args.output / (path.stem + ".stdout.log")).read_text().splitlines()
            try:
                result.update(json.loads(lines[-1]))
            except (IndexError, json.JSONDecodeError):
                result["error"] = "worker did not return JSON"
        if validator and result["translated"]:
            try:
                check = subprocess.run([validator, "--target-env", args.target_env, result["output"]], capture_output=True, text=True, timeout=min(args.timeout, 30))
                result.update(validator=str(validator), validator_exit_code=check.returncode,
                              validator_output=(check.stdout + check.stderr)[:8192], validated=check.returncode == 0)
            except subprocess.TimeoutExpired:
                result.update(validator=str(validator), validator_timeout=True)
        results.append(result)
        (args.output / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    print(json.dumps({"summary": str(args.output / "summary.json"), "count": len(results)}))
    return 0 if all(r["translated"] and (not validator or r["validated"]) for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
