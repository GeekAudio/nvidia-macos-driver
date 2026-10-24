#!/usr/bin/env python3
"""Build/run the Vision probe in a child process; keep generated fixtures and logs.

CPU is the default. A timeout kills the child process group, but cannot guarantee
recovery from a driver-level GPU hang. Nothing is installed or sent to QQ.
"""
import argparse
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("cpu", "default", "gpu"), default="cpu")
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--revision", type=int, default=0, help="0 uses Vision's default revision")
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--expected")
    parser.add_argument("--plugin", type=Path, help="local debug NVMTLDriver executable; requires --mode gpu")
    parser.add_argument("--capture-all", action="store_true", help="capture every function-constant translation")
    args = parser.parse_args()
    if not 1 <= args.iterations <= 1000 or args.timeout <= 0:
        parser.error("iterations must be 1..1000 and timeout must be positive")
    if args.plugin and args.mode != "gpu":
        parser.error("--plugin requires --mode gpu; local device selection must be explicit")
    output = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix="vision-barcode-"))
    output.mkdir(parents=True, exist_ok=True)
    binary = output / "vision_barcode_probe"
    source = Path(__file__).with_name("vision_barcode_probe.m").resolve()
    compile_cmd = ["xcrun", "clang", "-fobjc-arc", "-O1", str(source), "-o", str(binary)]
    for framework in ("Foundation", "Vision", "CoreML", "CoreImage", "ImageIO", "Metal", "CoreGraphics", "IOKit"):
        compile_cmd += ["-framework", framework]
    with (output / "build.log").open("w") as log:
        subprocess.run(compile_cmd, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
    env = os.environ.copy()
    # These settings belong only to the new child. Keep caches separate from GUI apps.
    env["NVMTL_LOG_PATH"] = str(output / "nvmtl.log")
    env["NVMTL_AIRCACHE"] = str(output / "aircache")
    env["MESA_SHADER_CACHE_DIR"] = str(output / "mesa-cache")
    for key, folder in (("NVMTL_FC_FAIL_DUMP", "fc-fail"), ("NVMTL_DUMP_FAILED_DIR", "failed")):
        path = output / folder
        path.mkdir(exist_ok=True)
        env[key] = str(path)
    if args.capture_all:
        folder = output / "fc-all"
        folder.mkdir(exist_ok=True)
        env["NVMTL_FC_DUMP_ALL"] = str(folder)
    cmd = [str(binary), "--mode", args.mode, "--iterations", str(args.iterations),
           "--revision", str(args.revision), "--fixture-out", str(output / "input.png")]
    if args.mode != "cpu" or args.capture_all:
        cmd += ["--capture-dir", str(output), "--capture-all", str(int(args.capture_all))]
    if args.image:
        cmd += ["--image", str(args.image.resolve())]
    if args.expected is not None:
        cmd += ["--expected", args.expected]
    if args.plugin:
        cmd += ["--plugin", str(args.plugin.resolve(strict=True))]
    def limits():
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024 * 1024, 128 * 1024 * 1024))
    started = time.monotonic()
    timed_out = False
    with (output / "stdout.jsonl").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
        child = subprocess.Popen(cmd, stdout=stdout, stderr=stderr, env=env,
                                 start_new_session=True, preexec_fn=limits)
        try:
            rc = child.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(child.pid, signal.SIGKILL)
            rc = child.wait(timeout=5)
    faults = []
    pattern = re.compile(r"GPU[_ ]fault|MMU.*fault|device lost|Xid\s*[:=]", re.IGNORECASE)
    for file in (output / "nvmtl.log", output / "stderr.log"):
        if file.exists():
            faults += [line[:500] for line in file.read_text(errors="replace").splitlines() if pattern.search(line)]
    summary = {"output": str(output), "command": cmd, "mode": args.mode,
               "exit_code": rc, "timeout": timed_out, "seconds": round(time.monotonic() - started, 3),
               "gpu_fault_lines": faults, "success": rc == 0 and not faults}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
