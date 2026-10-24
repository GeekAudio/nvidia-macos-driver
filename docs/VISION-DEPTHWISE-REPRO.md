# Vision / MPS depthwise translator regression

This fork investigates QQ 7.0.2 (53644) on macOS 26.7.1 (25G241), with an RTX 3080 and the installed NullMoth 2.5.4 driver. The observed crash stack enters `VNDetectBarcodesRequest`, Espresso, MPSGraph DepthwiseConv3D, `MPSKey_Compile`, then aborts after a Metal compilation failure.

The translator change is commit `09209702200c79acffc3ee9e4d542cb1e353fd24`, followed by pointer-type preservation in `ecdf8f6`, based on upstream `46779055f68208baa5ddfb9b2e9c77ed028e1d59`.

## Cause and scope

A public MPSGraph depthwise reproduction captures an immutable local pointer in a structure field initialized outside the entry block. After helper inlining, a store dominates all loads, but the previous forwarding pass only considered entry-block stores. The emitted pointer merge then reports Function versus Private storage, followed by differing structural sources for a Private pointer phi.

The new proof handles a unique pointer-leaf store only when normalized constant GEP paths, alias/use checks, identical stored/loaded pointer types, and reachable CFG dominance establish that forwarding is valid. Ambiguous aliases, dynamic indexes, overlapping paths, aggregate writes, unknown calls, volatile/atomic access, and quoted address/block names conservatively decline the proof. The wrapper retries the existing pointer-merge failure path after helper inlining and proven forwarding.

## Regression checks

`translator/translator/tests/immutable_local_pointer_fields.rs` contains eleven tests and synthetic LLVM fixtures. They cover a dominating non-entry store and rejected cases involving a missing predecessor initialization, multiple aliased stores, differently sized constant indexes, dynamic indexes, memset, aggregate stores, a fake lifetime-call comment, volatile loads, pointer address-space reinterpretation, and quoted names.

Run the dedicated integration test from the repository root with a working SPIR-V validator configured as required by its existing tooling:

```sh
METAL2VULKAN_SPIRV_VAL=/absolute/spirv-val \
  cargo test --manifest-path translator/translator/Cargo.toml --test immutable_local_pointer_fields
```

The integration test passed all eleven cases locally. The full upstream unit suite could not be built from the public checkout because referenced fixture files are absent; this is not a claim that the complete suite passed.

## Local reproduction tools

- `tools/build_mps_debug_plugin.py` stages an isolated debug Metal plugin and prefixes its Objective-C classes to avoid collisions with the installed release plugin. It does not install the result.
- `tools/mps_depthwise_probe.m` compiles or encodes a small public MPSGraph operation. It has no shader-execution mode. It requires the debug plugin submission guard and stops if MPS attempts a command-buffer submit or wait. Sparse residency binding and driver/device setup are outside its command-buffer counter; it is not a general zero-GPU-activity guarantee.
- `tools/replay_nvmtl_fc.py` replays a local AIR/function-constant dump through the real translator C ABI in a bounded child process, optionally validates the result for Vulkan 1.2, and records source/library/output hashes. This does not prove GPU execution correctness.
- `tools/run_vision_barcode_probe.py` and `tools/vision_barcode_probe.m` provide a standalone Vision probe. CPU mode is the default. A standalone Vision result does not establish QQ behavior.

Example isolated build and guarded encode, using separate absolute output directories and a locally built translator:

```sh
python3 tools/build_mps_debug_plugin.py \
  --output /absolute/experiment/plugin \
  --vulkan-headers /absolute/Vulkan-Headers/include \
  --translator /absolute/candidate/libnvmtl_translate.dylib
xcrun clang -fobjc-arc -O1 tools/mps_depthwise_probe.m \
  -o /absolute/experiment/mps_depthwise_probe \
  -framework Foundation -framework Metal -framework MetalPerformanceShaders \
  -framework MetalPerformanceShadersGraph -framework IOKit
/absolute/experiment/mps_depthwise_probe \
  --plugin /absolute/experiment/plugin/NVMTLDriver \
  --output /absolute/experiment/capture --stage encode
```

The public rank-3 MPSGraph probe captured actual Apple AIR for `depthwiseConv_3x3`; this is not a byte-for-byte capture from QQ. The original QQ stack identifies the same depthwise compilation path. The local baseline failed translating this probe kernel. With the candidate, the same captured AIR and constants translated to 283,484 bytes of SPIR-V and passed the compiled SPIRV-Tools Vulkan 1.2 validator. The guarded MPS run completed compilation and encoding in 35.816 seconds, with zero graph command buffers submitted. It reused the exact prefixed, guarded plugin from the baseline run, changing the adjacent translator symlink only.

Apple AIR dumps, generated binaries, account data, and QQ application logs are deliberately kept outside this repository. The final build was also tested in the actual installed QQ process by invoking its native `scanQBar` service sequentially on 20 distinct local PNGs with distinct known plaintext payloads. All 20 results matched, QQ remained alive, and no new QQ crash report or GPU fault line appeared in the test output. The run took 93.291 seconds including startup and a ten-second observation after completion. A prior three-image smoke run also passed.

A thread sample confirms `VNDetectBarcodesRequest` → Espresso/MPS convolution → the installed NVMTL plugin → the exact candidate translator. The sample did not capture the original MPSGraph DepthwiseConv3D stage, so it proves the real Vision/MPS backend was exercised, not that these synthetic images exercised every original failing kernel. No original triggering image was available. Treat this as a successful bounded regression test, not universal crash freedom.

For QQ, only a verified process-local translator redirect was used. No system driver, kext, EFI, or QQ application binary was replaced. The diagnostic interface shim and synthetic images are local experiment artifacts rather than upstream driver tools. Its initial launcher-only eager Node-API binding failed before scanning; a lazily bound diagnostic tool corrected that harness problem before the successful runs.
