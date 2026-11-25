#!/bin/bash
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
/usr/bin/shasum -a 256 --strict -c SHA256SUMS --quiet
for bundle in NVMTLDriver.bundle NVKZinkGLDriver.bundle; do
  if [ -d "$HERE/Library/GPUBundles/$bundle" ]; then
    /usr/bin/codesign --verify --strict "$HERE/Library/GPUBundles/$bundle"
  fi
done
/bin/bash "$HERE/nullmoth-runtime-check.sh" "$HERE" "$(sw_vers -productVersion)"
RUNTIME="$HERE/Library/GPUBundles/nvmtl/zink"
export NVKZINK_PRESENT=gpu NVKZINK_REQUIRE_GPU=1
"$RUNTIME/zink_sync_probe"
"$RUNTIME/zink_probe" "$RUNTIME/libnvk_zink.dylib" "$RUNTIME/nvk_icd.json"
NVKZINK_RGBX=1 "$RUNTIME/zink_probe" "$RUNTIME/libnvk_zink.dylib" "$RUNTIME/nvk_icd.json"
MODE=()
NATIVE_MODE=()
if /usr/sbin/ioreg -r -c NVAccel -d 1 | /usr/bin/grep -q '"IOGLBundleName" = "NVKZinkGLDriver"'; then
  "$RUNTIME/cgl_renderer_probe" --require-zink
  MODE=(--system)
  NATIVE_MODE=(automatic)
fi
"$RUNTIME/cgl_zink_probe" "$RUNTIME/libnvk_cgl.dylib" "$RUNTIME/nvk_icd.json" ${MODE[@]+"${MODE[@]}"}
"$RUNTIME/cgl_zink_legacy_probe" "$RUNTIME/libnvk_cgl.dylib" "$RUNTIME/nvk_icd.json" ${MODE[@]+"${MODE[@]}"}
"$RUNTIME/cgl_zink_window_probe" "$RUNTIME/libnvk_cgl.dylib" "$RUNTIME/nvk_icd.json" ${MODE[@]+"${MODE[@]}"}
"$RUNTIME/cgl_zink_window_probe" "$RUNTIME/libnvk_cgl.dylib" "$RUNTIME/nvk_icd.json" ${MODE[@]+"${MODE[@]}"} --legacy --no-alpha --appkit-present
if [ "${#MODE[@]}" -gt 0 ]; then
  "$RUNTIME/cgl_zink_window_probe" "$RUNTIME/libnvk_cgl.dylib" "$RUNTIME/nvk_icd.json" "${MODE[@]}" --legacy --no-alpha --appkit-present --render-thread
fi
"$RUNTIME/cgl_zink_window_probe" "$RUNTIME/libnvk_cgl.dylib" "$RUNTIME/nvk_icd.json" ${MODE[@]+"${MODE[@]}"} --legacy --transparent
"$RUNTIME/zink_native_present_probe" "$RUNTIME/libnvk_cgl.dylib" "$RUNTIME/nvk_icd.json" ${NATIVE_MODE[@]+"${NATIVE_MODE[@]}"} default
"$RUNTIME/zink_native_present_probe" "$RUNTIME/libnvk_cgl.dylib" "$RUNTIME/nvk_icd.json" ${NATIVE_MODE[@]+"${NATIVE_MODE[@]}"} transparent
echo "Package verification passed."
