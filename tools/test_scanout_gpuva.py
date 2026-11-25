#!/usr/bin/env python3
"""Run the scanout GPU-VA commit and cleanup against a mock RM and two framebuffers.

The compiled method bodies come from the kexts. Run with --revision to test
the same contract against an earlier implementation.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
ACCEL = "kexts/NVRM/accel/nvrm-accel.cpp"
FB = "kexts/NVRMFB/fb/nvrm-fb.cpp"
GPUVA = "kexts/NVRM/nvrm_gpuva_abi.h"


def method(source, signature):
    start = source.index(signature)
    return source[start:source.index("\n}\n", start) + 3]


def test(accel, fb):
    framebuffer_map = "IOReturn NVRMNVDAFramebuffer::mapScanoutGpuVa("
    bodies = "\n".join(
        method(accel, signature) for signature in (
            "bool NVMemoryMap::commitIntoGPUPageTable()",
            "void NVMemoryMap::releaseFromGPUPageTable()",
            "void NVVidMemory::deallocPhysical()",
            "void NVVidMemory::free()",
        )
    )
    # The predecessor has no scanout map endpoint. Its real commit must fail
    # to map valid scanout even when a working mock RM is available.
    bodies += method(fb, framebuffer_map) if framebuffer_map in fb else (
        framebuffer_map + "NVRMScanoutGpuVaRequest *) { return kIOReturnUnsupported; }\n"
    )
    fixture = r'''
#include <cstdio>
#include <cstring>
#include "kexts/NVRM/nvrm_gpuva_abi.h"

using NvU64 = unsigned long long;
using SInt64 = long long;
using IOReturn = int;
enum { kIOReturnSuccess, kIOReturnBadArgument, kIOReturnNotReady, kIOReturnNotAttached,
       kIOReturnNoMemory, kIOReturnUnsupported, kIOReturnIOError };
#define ALOG(...) do {} while (false)
#define OSDynamicCast(type, ptr) dynamic_cast<type *>(ptr)
#define OSSafeReleaseNULL(ptr) do { if (ptr) (ptr)->release(); (ptr) = nullptr; } while (false)

struct OSSymbol {
    const char *name;
    static const OSSymbol *withCStringNoCopy(const char *name) {
        static OSSymbol symbol;
        symbol.name = name;
        return &symbol;
    }
    void release() const {}
};
struct OSMetaClass {
    const char *getClassName() { return "NVRM"; }
};
struct IOService {
    virtual IOReturn callPlatformFunction(const OSSymbol *, bool, void *, void *, void *, void *) = 0;
    virtual IOService *getProvider() { return nullptr; }
    OSMetaClass *getMetaClass() { static OSMetaClass meta; return &meta; }
    void release() { ++releases; }
    unsigned releases = 0;
};
struct NVRMDisplayGate {
    bool valid() { return true; }
};
int OSAddAtomic(int n, int *value) { return *value += n; }
void OSAddAtomic64(SInt64 n, SInt64 *value) { *value += n; }
void nvAccelZero(void *ptr, unsigned long size) { std::memset(ptr, 0, size); }
static int device, otherDevice, ownedMemory, frontMemory, secondMemory;
static SInt64 gCntMapCommit, gCntMapRelease, gCntVmDealloc, gCntVmDeallocBytes, gCntVmParked;
static unsigned frees;
static bool rmReady = true;

struct IOAccelVidMemory {
    virtual ~IOAccelVidMemory() = default;
    unsigned char opaque[0xc8] = {};
    void free() {}
};
struct NVVidMemory : IOAccelVidMemory {
    void *fRmHandle = nullptr, *fKva = nullptr, *fParkHandle = nullptr, *fParkKva = nullptr;
    NvU64 fPhys = 0, fBytes = 0, fAllocationCookie = 0, fParkPhys = 0, fParkBytes = 0, fParkCookie = 0;
    bool fScanout = false;
    IOService *fScanoutFramebuffer = nullptr;
    void releaseVram(void *, void *, NvU64, NvU64) { ++frees; }
    void releaseParked() {}
    void deallocPhysical();
    void free();
};
static int gVmSticky = 1;
struct NVMemoryMap {
    IOAccelVidMemory *fMem;
    NvU64 fVaLen, fGpuVa = 0x1000;
    unsigned fHVirt = 42, fHRmMemory = 0, fCommits = 0;
    bool fMapped = false;
    NvU64 getLength() { return fVaLen; }
    bool commitIntoGPUPageTable();
    void releaseFromGPUPageTable();
};
static bool nvAccelRmHandles(NVRMRmHandles *h) {
    h->kapiDevice = &device;
    return true;
}
static unsigned mapCalls, unmapCalls, unmapHandle, unmapFlags;
static NVRMGpuVaRequest lastMap;
static IOReturn nvAccelGpuVaCall(const char *sel, NVRMGpuVaRequest *g) {
    if (!std::strcmp(sel, NVRM_GPUVA_FN_FREE)) {
        ++unmapCalls;
        unmapHandle = g->hMemory;
        unmapFlags = g->flags;
        return kIOReturnSuccess;
    }
    if (std::strcmp(sel, NVRM_GPUVA_FN_MAP)) return kIOReturnUnsupported;
    ++mapCalls;
    lastMap = *g;
    if (!rmReady) {
        g->status = 0xaa;
        return kIOReturnIOError;
    }
    if (g->kapiDevice != &device || !g->kapiMemory || g->hMemory || g->hVirt != 42)
        return kIOReturnBadArgument;
    g->hMemory = g->kapiMemory == &secondMemory ? 43 : 44;
    g->gpuva = 0x2000;
    return kIOReturnSuccess;
}
struct RM : IOService {
    IOReturn callPlatformFunction(const OSSymbol *fn, bool, void *req, void *, void *, void *) override {
        return nvAccelGpuVaCall(fn->name, static_cast<NVRMGpuVaRequest *>(req));
    }
};
struct NVRMNVDAFramebuffer : IOService {
    IOService *rm = nullptr;
    void *fDev = &device, *fMem = &frontMemory;
    NvU64 fPhys = 0x100000, fMemBytes = 40 * 1024 * 1024, fScanoutBarBytes = fMemBytes;
    unsigned fPitch = 15360, fH = 2160;
    int fStopping = 0;

    IOService *getProvider() override { return rm; }
    IOReturn callPlatformFunction(const OSSymbol *fn, bool, void *req, void *, void *, void *) override {
        if (std::strcmp(fn->name, NVRM_GPUVA_FN_MAP_SCANOUT)) return kIOReturnUnsupported;
        return mapScanoutGpuVa(static_cast<NVRMScanoutGpuVaRequest *>(req));
    }
    IOReturn mapScanoutGpuVa(NVRMScanoutGpuVaRequest *);
};
''' + bodies + r'''
#define CHECK(condition, name) do { if (!(condition)) { std::puts("FAIL " name); ++failed; } } while (false)
int main() {
    int failed = 0;
    RM rm;
    NVRMNVDAFramebuffer first, second;
    first.rm = second.rm = &rm;
    second.fMem = &secondMemory;
    second.fPhys = 0x200000;
    second.fPitch = 7680;
    second.fH = 1080;
    second.fScanoutBarBytes = 0;

    NVVidMemory a, b;
    a.fScanout = b.fScanout = true;
    a.fScanoutFramebuffer = &first;
    b.fScanoutFramebuffer = &second;
    a.fPhys = first.fPhys;
    a.fBytes = 33177600;
    b.fPhys = second.fPhys;
    b.fBytes = 8294400;
    NVMemoryMap mapA{&a, a.fBytes}, mapB{&b, b.fBytes};

    CHECK(mapA.commitIntoGPUPageTable() && mapA.fMapped && mapA.fGpuVa == 0x2000 &&
          mapA.fHRmMemory == 44 && lastMap.kapiMemory == &frontMemory &&
          lastMap.size == 33177600 && lastMap.isVidmem == 1, "4K scanout reaches RM on its own video memory");
    const unsigned alreadyMapped = mapCalls;
    CHECK(mapA.commitIntoGPUPageTable() && mapCalls == alreadyMapped, "duplicate commit does not remap");
    CHECK(mapB.commitIntoGPUPageTable() && mapB.fHRmMemory == 43 &&
          lastMap.kapiMemory == &secondMemory && lastMap.size == 8294400 &&
          lastMap.isVidmem == 0, "1080p system-memory fallback uses its own scanout and snoops");
    mapB.releaseFromGPUPageTable();
    CHECK(!mapB.fMapped && unmapCalls == 1 && unmapHandle == 43 &&
          unmapFlags == NVRM_GPUVA_FLAG_UNMAP_ONLY, "unmap uses RM map output and does not free the scanout");

    const unsigned previousCalls = mapCalls;
    b.fScanoutFramebuffer = &first;
    CHECK(!mapB.commitIntoGPUPageTable(), "different head refuses the scanout");
    b.fScanoutFramebuffer = &second;
    second.fDev = &otherDevice;
    CHECK(!mapB.commitIntoGPUPageTable(), "different KAPI device refuses the VA namespace");
    second.fDev = &device;
    --second.fH;
    CHECK(!mapB.commitIntoGPUPageTable(), "changed mode rejects stale raster size");
    ++second.fH;
    mapB.fVaLen = second.fMemBytes + 1;
    CHECK(!mapB.commitIntoGPUPageTable(), "mapping beyond the allocation is rejected");
    mapB.fVaLen = b.fBytes;
    second.fMem = nullptr;
    CHECK(!mapB.commitIntoGPUPageTable(), "missing scanout allocation is rejected");
    second.fMem = &secondMemory;
    second.fStopping = 1;
    CHECK(!mapB.commitIntoGPUPageTable() && mapCalls == previousCalls, "stopping head never submits a map");
    second.fStopping = 0;

    rmReady = false;
    CHECK(!mapB.commitIntoGPUPageTable() && !mapB.fMapped && mapCalls == previousCalls + 1,
          "RM refusal cannot mark scanout mapped");
    rmReady = true;
    CHECK(mapB.commitIntoGPUPageTable() && mapB.fMapped, "failed map can be retried");
    mapB.releaseFromGPUPageTable();
    b.deallocPhysical();
    CHECK(!b.fPhys && !b.fBytes && b.fScanout && !mapB.commitIntoGPUPageTable(),
          "deallocated wrapper still identifies as scanout and cannot be mapped");
    b.free();
    CHECK(second.releases == 1 && frees == 0, "free drops only the framebuffer reference");

    NVVidMemory own;
    own.fRmHandle = &ownedMemory;
    NVMemoryMap regular{&own, 4096};
    CHECK(regular.commitIntoGPUPageTable() && lastMap.kapiMemory == &ownedMemory && lastMap.isVidmem,
          "ordinary owned VRAM still maps through its allocator handle");
    return failed ? 1 : 0;
}
'''
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "fixture.cpp"
        output = Path(directory) / "scanout"
        source.write_text(fixture, encoding="utf-8")
        subprocess.run(
            ["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT), str(source), "-o", str(output)],
            check=True,
        )
        subprocess.run([str(output)], check=True)

    init = method(accel, "IOAccelMemory *NVDisplayPipe::initFramebufferResource(")
    assert "a->pipeFramebuffer(this, &head)" in init
    assert init.index("r.kva && !r.phys") < init.index("m->fScanoutFramebuffer = fb")
    assert "if (r.handle) {\n        fb->retain();\n        m->fScanoutFramebuffer = fb;" in init
    wire = method(accel, "bool NVVidMemory::allocPhysical()")
    assert wire.index("if (fScanout)") < wire.index("askFramebufferForVram")
    dispatch = method(fb, "IOReturn NVRMNVDAFramebuffer::callPlatformFunction(")
    assert "isEqualTo(NVRM_GPUVA_FN_MAP_SCANOUT)" in dispatch and "return mapScanoutGpuVa(" in dispatch
    assert (ROOT / GPUVA).read_bytes() == (ROOT / "kexts/NVRMFB/nvrm_gpuva_abi.h").read_bytes()
    print("scanout GPU VA: two heads, VRAM and sysmem, stale and failed maps, unmap and free, owned VRAM: OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", help="run against a previous source revision")
    args = parser.parse_args()

    def read(path):
        if args.revision:
            return subprocess.check_output(["git", "show", f"{args.revision}:{path}"], cwd=ROOT, text=True)
        return (ROOT / path).read_text(encoding="utf-8")

    try:
        test(read(ACCEL), read(FB))
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from None
