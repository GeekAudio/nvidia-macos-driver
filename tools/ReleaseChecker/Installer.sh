#!/bin/bash
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin LC_ALL=C COPYFILE_DISABLE=1
unset PERL5OPT PERL5LIB
umask 077

mode=$1 base=$2 base_sha=$3 overlay=$4 overlay_sha=$5 overlay_dir=$6 tag=$7
if [ "$mode" = install ]; then
    [ "$(id -u)" = 0 ]
    work=$(mktemp -d /private/var/tmp/geekaudio-install.XXXXXX)
    trap 'rm -r "$work"' EXIT
else
    [ "$mode" = prepare ]
    work=$8
    mkdir -m 700 "$work"
fi

echo '== Verify release archives'
for input in "$base" "$overlay"; do [ -f "$input" ] && [ ! -L "$input" ]; done
cp -P "$base" "$work/base.tar.gz"
cp -P "$overlay" "$work/overlay.tar.gz"
[ ! -L "$work/base.tar.gz" ] && [ ! -L "$work/overlay.tar.gz" ]
(cd "$work" && printf '%s  base.tar.gz\n%s  overlay.tar.gz\n' "$base_sha" "$overlay_sha" | shasum -a 256 --strict --quiet -c -)

echo '== Assemble and verify installer'
mkdir "$work/base" "$work/overlay"
tar -xzf "$work/base.tar.gz" -C "$work/base"
tar -xzf "$work/overlay.tar.gz" -C "$work/overlay"
root="$work/base/pkgroot"
payload="$work/overlay/$overlay_dir/payload"
[ -d "$payload" ] && [ -f "$root/install.sh" ]
(cd "$root" && shasum -a 256 --strict --quiet -c SHA256SUMS)
ditto "$payload" "$root"
cp "$root/BASELINE-IDENTITY.json" "$root/UPSTREAM-BASELINE-IDENTITY.json"
cp "$work/overlay/$overlay_dir/RELEASE-IDENTITY.json" "$root/BASELINE-IDENTITY.json"
plutil -replace GeekAudioReleaseTag -string "$tag" "$root/Library/Extensions/NVRM.kext/Contents/Info.plist" 2>/dev/null || \
    plutil -insert GeekAudioReleaseTag -string "$tag" "$root/Library/Extensions/NVRM.kext/Contents/Info.plist"
plugin=Library/GPUBundles/NVMTLDriver.bundle
codesign --force --sign - "$root/$plugin/Contents/MacOS/libnvencvt.dylib"
if [ -f "$root/$plugin/Contents/MacOS/libnvencvt-loader.dylib" ]; then
    codesign --force --sign - "$root/$plugin/Contents/MacOS/libnvencvt-loader.dylib"
fi
for bundle in "$plugin" Library/Extensions/{NVRM,NVRMFB,NVAccel}.kext Library/NullMoth/kexts/{15,26}/NVAccel.kext; do
    codesign --force --sign - "$root/$bundle"
    codesign --verify --deep --strict "$root/$bundle"
done
cd "$root"
if [ -f MANIFEST.txt ]; then
    find . -type f ! -path ./SHA256SUMS | sed 's|^\./||' | sort > "$work/MANIFEST.txt"
    mv "$work/MANIFEST.txt" MANIFEST.txt
fi
find . -type f ! -path ./SHA256SUMS -print0 | xargs -0 shasum -a 256 > "$work/SHA256SUMS"
mv "$work/SHA256SUMS" SHA256SUMS
shasum -a 256 --strict --quiet -c SHA256SUMS
if [ -f ./sums_complete.sh ]; then /bin/bash ./sums_complete.sh "$root"; fi

if [ "$mode" = install ]; then
    /bin/bash ./install.sh
else
    echo 'CHECK PASS (prepared package only)'
fi
