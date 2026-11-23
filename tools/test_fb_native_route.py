from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'kexts/NVRMFB/fb/nvrm-fb.cpp').read_text()
def function(signature):
    start = source.index(signature)
    return source[start:source.index('\n}\n', start) + 3]

fixture = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include "nvrm_ddc_vcp.h"
using UInt32 = uint32_t;
using SInt32 = int32_t;
struct OSSymbol {};
static OSSymbol brightness, volume, parametersKey, other;
static const OSSymbol *gIODisplayBrightnessKey = &brightness;
static const OSSymbol *gIODisplayVolumeKey = &volume;
static const OSSymbol *gIODisplayParametersKey = &parametersKey;
static const char *kIODisplayCapabilityStringKey = "IODisplayCapabilityString";
struct OSObject { void release() {} };
struct OSDictionary : OSObject {
    OSObject *brightnessRange = nullptr;
    OSObject *volumeRange = nullptr;
    OSObject *getObject(const OSSymbol *key) {
        return key == &brightness ? brightnessRange : key == &volume ? volumeRange : nullptr;
    }
    void removeObject(const OSSymbol *key) {
        if (key == &brightness) brightnessRange = nullptr;
        if (key == &volume) volumeRange = nullptr;
    }
    static OSDictionary *withCapacity(int) { return new OSDictionary; }
    static OSDictionary *withDictionary(OSDictionary *old) { return new OSDictionary(*old); }
};
struct OSData : OSObject {
    static OSData *withBytes(const char *text, size_t length) {
        assert(length == 10 && std::memcmp(text, "model(EDM)", length) == 0);
        return new OSData;
    }
};
#define OSDynamicCast(T, ptr) static_cast<T*>(ptr)
#define OSSafeReleaseNULL(ptr) do { if (ptr) ptr->release(); ptr = nullptr; } while (0)
#define BLLOG(...) do {} while (0)
struct IODisplay {
    OSDictionary *params = nullptr;
    OSObject *capability = nullptr;
    bool allowParams = true;
    static bool addParameter(OSDictionary *p, const OSSymbol *k, SInt32 min, SInt32 max) {
        assert((k == &brightness && min == 0 && max == 100) ||
               (k == &volume && min == 1 && max == 100));
        auto *range = new OSDictionary;
        if (k == &brightness) p->brightnessRange = range;
        else if (k == &volume) p->volumeRange = range;
        else return false;
        return true;
    }
    static bool setParameter(OSDictionary *p, const OSSymbol *k, SInt32 value) {
        assert(p->getObject(k));
        if (k == &brightness) return value == 30;
        if (k == &volume) return value == 42;
        return false;
    }
    OSObject *copyProperty(const OSSymbol *key) { return key == gIODisplayParametersKey ? params : nullptr; }
    OSObject *getProperty(const char *key) { return std::strcmp(key, kIODisplayCapabilityStringKey) == 0 ? capability : nullptr; }
    bool setProperty(const OSSymbol *key, OSDictionary *p) {
        if (allowParams && key == gIODisplayParametersKey) params = p;
        return allowParams && key == gIODisplayParametersKey;
    }
    bool setProperty(const char *key, OSObject *p) {
        if (std::strcmp(key, kIODisplayCapabilityStringKey) != 0) return false;
        capability = p;
        return true;
    }
    void removeProperty(const char *key) { if (std::strcmp(key, kIODisplayCapabilityStringKey) == 0) capability = nullptr; }
};
struct FB {
    bool fDdcSinkHasAudio = true;
    NVRMVcpReply brightnessReply = NVRMVcpReply::valid;
    NVRMVcpReply volumeReply = NVRMVcpReply::valid;
    unsigned brightnessWrites = 0, volumeWrites = 0;
    NVRMVcpReply readDdcControl(uint8_t code, UInt32 &value, UInt32 &max, UInt32 &gen, unsigned) {
        if (code == kNvrmVcpBrightness) {
            value = 30; max = 100; gen = 3;
            return brightnessReply;
        }
        assert(code == kNvrmVcpSpeakerVolume);
        value = 42; max = 100; gen = 4;
        return volumeReply;
    }
    bool queueDdcControl(uint8_t code, UInt32 value, UInt32 gen) {
        if (code == kNvrmVcpBrightness) {
            assert(value == 38 && gen == 3);
            ++brightnessWrites;
            return true;
        }
        assert(code == kNvrmVcpSpeakerVolume && value == 42 && gen == 4);
        ++volumeWrites;
        return true;
    }
    void setProperty(const char *, UInt32, int) {}
};
struct NVRMExternalBrightness {
    FB *fFramebuffer;
    OSDictionary *fParameters = nullptr;
    UInt32 fGeneration = 0, fMaximum = 0;
    const OSSymbol *fVolumeKey = gIODisplayVolumeKey;
    UInt32 fVolumeGeneration = 0, fVolumeMaximum = 0;
    bool fVolumeUnsupported = false;
    IODisplay *fRetryDisplay = nullptr;
    UInt32 fRetryGeneration = 0;
    unsigned fRetriesLeft = 0, fRetryDelayMs = 0;
    bool publishParameters(IODisplay *, bool, bool &);
    bool doIntegerSet(OSDictionary *, const OSSymbol *, UInt32);
};
'''
checks = r'''
int main() {
    FB fb;
    NVRMExternalBrightness handler{&fb};
    IODisplay display;
    bool retry = false;
    fb.brightnessReply = NVRMVcpReply::retry;
    assert(!handler.publishParameters(&display, false, retry) && retry && !display.params && !display.capability);
    fb.brightnessReply = NVRMVcpReply::valid;
    display.allowParams = false;
    assert(!handler.publishParameters(&display, false, retry) && !display.capability);
    display.allowParams = true;
    assert(handler.publishParameters(&display, false, retry) && display.params && display.capability && retry);
    auto *range = static_cast<OSDictionary*>(display.params->getObject(&brightness));
    OSDictionary unrelated;
    assert(!handler.doIntegerSet(&unrelated, &brightness, 38));
    assert(!handler.doIntegerSet(range, &other, 38));
    assert(!handler.doIntegerSet(range, &brightness, 101));
    assert(handler.doIntegerSet(range, &brightness, 38));
    assert(handler.doIntegerSet(display.params, &brightness, 38));
    assert(fb.brightnessWrites == 2 && fb.volumeWrites == 0);

    assert(handler.publishParameters(&display, true, retry) && !retry);
    auto *volumeRange = static_cast<OSDictionary*>(display.params->getObject(&volume));
    assert(volumeRange);
    assert(!handler.doIntegerSet(volumeRange, &volume, 0));
    assert(!handler.doIntegerSet(volumeRange, &volume, 101));
    assert(!handler.doIntegerSet(volumeRange, &brightness, 42));
    assert(handler.doIntegerSet(volumeRange, &volume, 42));
    assert(handler.doIntegerSet(display.params, &volume, 42));
    assert(fb.volumeWrites == 2);

    FB unsupportedVolume;
    unsupportedVolume.volumeReply = NVRMVcpReply::unsupported;
    NVRMExternalBrightness unsupportedHandler{&unsupportedVolume};
    IODisplay unsupportedDisplay;
    assert(unsupportedHandler.publishParameters(&unsupportedDisplay, true, retry));
    assert(!retry && unsupportedDisplay.params->getObject(&brightness) &&
           !unsupportedDisplay.params->getObject(&volume) && unsupportedHandler.fVolumeUnsupported);
    unsupportedVolume.volumeReply = NVRMVcpReply::valid;
    assert(!unsupportedHandler.publishParameters(&unsupportedDisplay, true, retry));
    assert(!retry && !unsupportedDisplay.params->getObject(&volume));

    IODisplay another;
    OSObject existing;
    another.capability = &existing;
    assert(handler.publishParameters(&another, false, retry) && another.capability == &existing);
    puts("PASS: brightness and supported speaker controls publish only valid DDC parameters and preserve existing capabilities");
}
'''
with tempfile.TemporaryDirectory() as name:
    cpp = Path(name) / 'route.cpp'
    exe = Path(name) / 'route'
    constants = '\n'.join(line for line in source.splitlines() if line.startswith('static constexpr unsigned kNVRMDdcProbe') and 'ReplyMs' in line)
    cpp.write_text(fixture + constants + function('bool NVRMExternalBrightness::publishParameters') +
        function('bool NVRMExternalBrightness::doIntegerSet') + checks)
    subprocess.run(['clang++', '-std=c++17', f'-I{root / "kexts/NVRMFB"}', '-Wall', '-Werror', str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
