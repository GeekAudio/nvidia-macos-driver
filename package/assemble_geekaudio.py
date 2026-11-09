#!/usr/bin/env python3
"""Assemble the GeekAudio 2.8.1 RC into a new, offline installer directory."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from stage_nvencvt import check_symlinks, sha256, verify_manifest, write_manifest


UPSTREAM_SHA256 = '1aa25a7f0ae28a27e77e1a761420cef9655af8b2b8effc1433142d1071795df8'
BUNDLES = [
    'Library/Extensions/NVRM.kext',
    'Library/Extensions/NVRMFB.kext',
    'Library/Extensions/NVAccel.kext',
    'Library/NullMoth/kexts/15/NVAccel.kext',
    'Library/NullMoth/kexts/26/NVAccel.kext',
    'Library/GPUBundles/NVMTLDriver.bundle',
]
PLUGIN = Path(BUNDLES[-1])
CODECS = ['libnvencvt.dylib', 'libnvencvt-loader.dylib']
EXECUTABLES = {str(Path(bundle) / 'Contents/MacOS' / Path(bundle).stem) for bundle in BUNDLES}
CODEC_PATHS = {str(PLUGIN / 'Contents/MacOS' / name) for name in CODECS}
LICENSE_NAMES = {
    'NVEncVT-MIT-LICENSE.txt', 'NVEncVT-THIRD_PARTY_NOTICES.md',
    'NVEncVT-NVIDIA-open-gpu-kernel-modules-COPYING.txt', 'NVEncVT-open-gpu-doc-LICENSE.md',
    'Lilu-LICENSE.txt', 'VirtualSMC-LICENSE.txt', 'SMCNvidiaGPU-LICENSE.txt',
}
PAYLOAD = EXECUTABLES | CODEC_PATHS | {'install.sh', 'extras/nvfb-edid', 'extras/firefox-nvdec.sh',
                                       'licenses/NOTICE.txt', str(PLUGIN / 'Contents/Resources/nvencvt-build-info.json')}
PAYLOAD |= {f'licenses/{name}' for name in LICENSE_NAMES}
PAYLOAD |= {str(PLUGIN / 'Contents/Resources' / name) for name in LICENSE_NAMES if name.startswith('NVEncVT-')}


def run(*args):
    subprocess.run(args, check=True)


def assemble(archive: Path, output: Path):
    if os.geteuid() == 0:
        raise RuntimeError('Assemble as your own user. The resulting installer handles authorization separately.')
    overlay = Path(__file__).resolve().parent
    check_symlinks(overlay)
    verify_manifest(overlay)
    if sha256(archive) != UPSTREAM_SHA256:
        raise RuntimeError('The upstream archive does not match official v2.8.1.')
    if output.exists() or output.is_symlink():
        raise RuntimeError('Choose a new output directory.')
    payload = overlay / 'payload'
    files = {path.relative_to(payload).as_posix() for path in payload.rglob('*') if path.is_file()}
    if files != PAYLOAD or any(p.is_symlink() for p in payload.rglob('*')):
        raise RuntimeError('Unexpected or missing overlay payload files.')
    identity = json.loads((overlay / 'RELEASE-IDENTITY.json').read_text())
    with tempfile.TemporaryDirectory(prefix='.geekaudio-assemble-', dir=output.parent) as temp:
        temp = Path(temp)
        run('/usr/bin/tar', '-xzf', str(archive), '-C', str(temp))
        pkg = (temp / 'pkgroot').resolve()
        check_symlinks(pkg)
        subprocess.run(['/usr/bin/shasum', '-a', '256', '-c', '--strict', '--quiet', 'SHA256SUMS'],
                       cwd=pkg, check=True)
        for name in sorted(files):
            dest = pkg / name
            if dest.is_symlink() or not dest.resolve().is_relative_to(pkg):
                raise RuntimeError(f'Unsafe package path: {name}')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(payload / name, dest)
        run('/usr/bin/xattr', '-cr', str(pkg))
        for name in CODEC_PATHS:
            run('/usr/bin/codesign', '--force', '--sign', '-', str(pkg / name))
        for bundle in BUNDLES:
            path = str(pkg / bundle)
            run('/usr/bin/codesign', '--force', '--sign', '-', path)
            run('/usr/bin/codesign', '--verify', '--deep', '--strict', path)
        shutil.copyfile(pkg / 'BASELINE-IDENTITY.json', pkg / 'UPSTREAM-BASELINE-IDENTITY.json')
        identity['assembled_binary_sha256'] = {name: sha256(pkg / name) for name in sorted(EXECUTABLES | CODEC_PATHS)}
        (pkg / 'BASELINE-IDENTITY.json').write_text(json.dumps(identity, ensure_ascii=False, indent=2) + '\n')
        (pkg / 'MANIFEST.txt').write_text(''.join(name + '\n' for name in sorted(
            p.relative_to(pkg).as_posix() for p in pkg.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')))
        check_symlinks(pkg)
        write_manifest(pkg)
        verify_manifest(pkg)
        output.mkdir()
        try:
            pkg.rename(output)
        except OSError:
            output.rmdir()
            raise
    print(f'Assembled and verified: {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('official_archive', type=Path)
    parser.add_argument('output_directory', type=Path)
    args = parser.parse_args()
    try:
        assemble(args.official_archive.resolve(strict=True), args.output_directory.absolute())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(f'Assembly stopped: {error}')
