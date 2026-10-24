#!/usr/bin/env python3
"""Build an isolated debug Metal plugin; nothing is installed or executed on GPU.

All plugin-owned Objective-C class names, including runtime string lookups, are
prefixed in a staged source copy. This avoids collisions if Apple frameworks also
load the installed release bundle while compiling an MPS graph.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--vulkan-headers", required=True, type=Path,
                        help="include directory containing vulkan/vulkan.h")
    parser.add_argument("--translator", required=True, type=Path)
    parser.add_argument("--class-prefix", default="QMP")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", args.class_prefix):
        parser.error("class-prefix must be an Objective-C identifier")
    output = args.output.resolve()
    if output.exists():
        parser.error("output already exists; use a new directory to preserve prior evidence")
    if not (args.vulkan_headers / "vulkan/vulkan.h").is_file() or not args.translator.is_file():
        parser.error("Vulkan headers or translator not found")
    source = Path(__file__).resolve().parent.parent / "plugin"
    texts = {p.name: p.read_text() for p in source.iterdir() if p.suffix in (".h", ".m", ".c")}
    names = sorted(set(re.findall(r"@(?:interface|implementation)\s+(NVMTL\w+)", "\n".join(texts.values()))))
    substitutions = {name: args.class_prefix + name for name in names}
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(name) for name in names) + r")\b")
    staged = output / "source-plugin"
    shutil.copytree(source, staged)
    for name, text in texts.items():
        (staged / name).write_text(pattern.sub(lambda match: substitutions[match.group()], text))
    binary = output / "NVMTLDriver"
    command = ["xcrun", "clang", "-arch", "x86_64", "-bundle", "-fobjc-arc", "-O1",
               "-Wno-protocol", "-Wno-incomplete-implementation", "-I" + str(args.vulkan_headers.resolve()),
               "-o", str(binary), str(staged / "NVMTLDevice.m"),
               "-framework", "Foundation", "-framework", "Metal", "-framework", "IOSurface", "-framework", "IOKit"]
    metadata = {"command": command, "class_prefix": args.class_prefix,
                "renamed_classes": substitutions, "translator": str(args.translator.resolve())}
    (output / "build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    with (output / "build.log").open("w") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
    subprocess.run(["codesign", "-f", "-s", "-", str(binary)], check=True, capture_output=True, timeout=30)
    (output / "libnvmtl_translate.dylib").symlink_to(args.translator.resolve())
    runtime = args.translator.resolve().parent / "air-runtime"
    if runtime.exists():
        (output / "air-runtime").symlink_to(runtime)
    print(json.dumps({"plugin": str(binary), "class_prefix": args.class_prefix,
                      "renamed_class_count": len(names), "gpu_used": False}, indent=2))


if __name__ == "__main__":
    main()
