#!/usr/bin/env python3
"""Exercise the production copy/flip path with two software NVKMS heads."""
from rotation.test_support import FB, ROOT, method, run


accel = (ROOT / "kexts/NVRM/accel/nvrm-accel.cpp").read_text()
transaction = method(accel, "IOReturn NVDisplayPipe::performTransaction(")
assert "s && !buffered && nvAccelIopFlip(" in transaction
buffered = transaction[transaction.index("if (buffered) {"):]
assert buffered.index("callPlatformFunction(present") < buffered.index("return kIOReturnSuccess;\n    }")
assert buffered.index("return kIOReturnSuccess;\n    }") < buffered.index("askFramebufferForScanout(")
assert "nvAccelSurfCopyReady(s, head, buffered)" in transaction

fixture = r'''
#include "../../NVRM/nvrm_copy_present.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

using IOReturn = int;
using NvU16 = unsigned short;
enum { kIOReturnSuccess, kIOReturnUnsupported, kIOReturnNotReady, kIOReturnBadArgument,
       kIOReturnBusy, kIOReturnNoMemory, kIOReturnIOError };
enum { NV_FALSE, NV_TRUE, NV_KMS_FLIP_RESULT_IN_PROGRESS };
enum { NVKMS_KAPI_LAYER_PRIMARY_IDX = 0, NVKMS_COMPOSITION_BLENDING_MODE_OPAQUE = 1 };
struct Surface { unsigned words[8] = {}; };
struct NvKmsKapiRequestedModeSetConfig {
    unsigned headsMask;
    struct Head {
        struct ModeSet { unsigned bActive, numDisplays, displays[1], mode; } modeSetConfig;
        struct Layer {
            struct Config {
                Surface *surface;
                NvU16 srcWidth, srcHeight, dstWidth, dstHeight;
                struct Composition { unsigned compMode; } compParams;
                unsigned minPresentInterval, tearing;
            } config;
            struct Flags { bool surfaceChanged; } flags;
        } layerRequestedConfig[1];
    } headRequestedConfig[8];
};
struct NvKmsKapiModeSetReplyConfig { int flipResult; };
struct NVRMDisplayGate { bool valid() { return true; } };
struct NVRMHeadGate { NVRMHeadGate(int, int, bool) {} bool valid() { return true; } };
int startLock() { return 0; }
void nvrmfbInstallSysctl() {}
void *IOMalloc(size_t size) { return std::malloc(size); }
void IOFree(void *memory, size_t) { std::free(memory); }
void nvu_zero(void *memory, size_t size) { std::memset(memory, 0, size); }
int OSAddAtomic(int value, int *counter) { return *counter += value; }
void OSAddAtomic64(int value, long long *counter) { *counter += value; }
bool OSCompareAndSwap(int, int, unsigned *) { return false; }
#define FBLOG(...) ((void)0)
long long gCopyPresentLatched, gCopyPresentBusy, gCopyPresentFailed;

struct NVRMNVDAFramebuffer {
    struct CopyScanout { Surface *surface = nullptr; void *kva = nullptr; unsigned pitch = 16; };
    Surface home, owned[2];
    CopyScanout fCopyScanout[2];
    Surface *fSurf = &home, *fFrontSurf = fSurf;
    void *fKms = this, *fDev = this;
    bool fCopyPresent = true, fCopyAwaitingLatch = false;
    bool fSubmissionSeen = false, fSubmissionFaulted = false;
    bool fModeSet = true, fConsoleAperture = false, fPowerOn = true, fHeadSlept = false, fDpyLost = false;
    bool pending = false, statusAvailable = true, canAllocate = true, accept = true;
    unsigned fHead, fDisplay = 5, fMode = 9;
    unsigned fW = 2, fH = 2, fPitch = 16, fRasterW = 4, fRasterH = 4;
    unsigned fTransform = 0, fCurTransform = 0, fServerFresh = 0, fCopyPresents = 0;
    unsigned preparations = 0, commits = 0, firstPixelAtCommit = 0;
    int fStopping = 0, fPrimaryGate = 0;
    struct { void *proc = nullptr; } fConnect;

    explicit NVRMNVDAFramebuffer(unsigned head) : fHead(head) {}
    bool rmGpuReady() { return true; }
    bool waitFlipDone(unsigned us) { assert(us == 0); return statusAvailable && !pending; }
    void notifyConnectionChanged(int) {}
    bool prepareCopyScanout(CopyScanout &slot) {
        ++preparations;
        assert(!pending && statusAvailable && (!slot.surface || slot.surface != fFrontSurf));
        if (!canAllocate) return false;
        slot.surface = &owned[&slot - fCopyScanout];
        slot.kva = slot.surface->words;
        return true;
    }
    bool submitConfig(NvKmsKapiRequestedModeSetConfig *cfg, NvKmsKapiModeSetReplyConfig *rep,
                      bool commit, bool bootstrap, bool cursorOnly, bool frame, bool deferred) {
        assert(commit && !bootstrap && !cursorOnly && frame && deferred);
        assert(cfg->headsMask == 1u << fHead);
        const auto &head = cfg->headRequestedConfig[fHead];
        const auto &layer = head.layerRequestedConfig[0];
        assert(head.modeSetConfig.bActive && head.modeSetConfig.displays[0] == fDisplay);
        assert(layer.flags.surfaceChanged && layer.config.srcWidth == fW && layer.config.dstWidth == fRasterW);
        assert(layer.config.minPresentInterval == 1 && !layer.config.tearing);
        assert(layer.config.surface != fFrontSurf && layer.config.surface != &home);
        ++commits;
        firstPixelAtCommit = layer.config.surface->words[0];
        rep->flipResult = accept ? 0 : 3;
        if (accept) fSubmissionSeen = pending = true;
        return accept;
    }
    IOReturn presentCopiedFrame(const NVRMCopyPresentRequest &request);
};
PRODUCTION_METHOD

int main() {
    NVRMNVDAFramebuffer main(0), secondary(1);
    unsigned pixels[] = {1, 2, 9, 3, 4, 9};
    NVRMCopyPresentRequest frame = {NVRM_COPY_PRESENT_ABI_VERSION, NV_IOP_FMT_BGRA, 2, 2, 12, pixels};
    assert(main.presentCopiedFrame(frame) == kIOReturnSuccess);
    Surface *front = main.fFrontSurf;
    assert(main.firstPixelAtCommit == 1 && front->words[1] == 2 && front->words[4] == 3);
    assert(front->words[2] == 0 && main.home.words[0] == 0);
    pixels[0] = 7;
    assert(main.presentCopiedFrame(frame) == kIOReturnBusy && main.preparations == 1);
    assert(front->words[0] == 1);
    assert(secondary.presentCopiedFrame(frame) == kIOReturnSuccess && secondary.firstPixelAtCommit == 7);
    assert(main.fFrontSurf == front && front->words[0] == 1);
    main.pending = false;
    main.statusAvailable = false;
    assert(main.presentCopiedFrame(frame) == kIOReturnBusy && main.preparations == 1);
    main.statusAvailable = true;
    main.canAllocate = false;
    assert(main.presentCopiedFrame(frame) == kIOReturnNoMemory && main.fFrontSurf == front);
    main.canAllocate = true;
    main.accept = false;
    assert(main.presentCopiedFrame(frame) == kIOReturnIOError && main.fFrontSurf == front);
    main.accept = true;
    assert(main.presentCopiedFrame(frame) == kIOReturnSuccess && main.fFrontSurf != front);
    assert(main.fFrontSurf->words[0] == 7 && front->words[0] == 1);
    main.pending = false;
    ++frame.width;
    assert(main.presentCopiedFrame(frame) == kIOReturnBadArgument && main.commits == 3);
    --frame.width;
    frame.format = NV_IOP_FMT_L10R;
    pixels[0] = 1023u << 20;
    assert(main.presentCopiedFrame(frame) == kIOReturnSuccess && main.fFrontSurf == front);
    assert(main.firstPixelAtCommit == 0xffff0000u && secondary.fFrontSurf->words[0] == 7);
    assert(gCopyPresentLatched == 2 && gCopyPresentBusy == 2 && gCopyPresentFailed == 3);
    std::puts("PASS: buffered copy, independent heads, latch safety, refusal, pitch and l10r");
}
'''

run(fixture.replace("PRODUCTION_METHOD", method(FB, "IOReturn NVRMNVDAFramebuffer::presentCopiedFrame(")))
