#!/usr/bin/env python3
"""A reserved offline framebuffer opens before its first modeset.

IOFramebuffer marks a framebuffer dead if enableController fails. Keep the
offline framebuffer available for its first hotplug, and preserve the error
and notification behavior for an attempted modeset on a connected head.
"""
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = "kexts/NVRMFB/fb/nvrm-fb.cpp"
fails = 0


def arm(name, ok, detail=""):
    global fails
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f" ({detail.strip()[-300:]})"))


def body(src):
    head = "IOReturn NVRMNVDAFramebuffer::enableController()\n{"
    i = src.index(head)
    return src[i + len(head) - 1:src.index("\n}\n", i) + 2]


HARNESS = r'''
#include <cstdio>
typedef int IOReturn;
static const IOReturn kIOReturnSuccess = 0, kIOReturnError = (IOReturn)0xe00002bc;
static constexpr unsigned kNVRMDdcProbeFirstDelayMs = 350;
#define OSSafeReleaseNULL(p) do { delete p; p = nullptr; } while (0)
struct IOWorkLoop { int addEventSource(void *) { return 0; } };
struct IOCommandGate { static IOCommandGate *commandGate(void *) { return new IOCommandGate; } };
#define FBLOG(...) (printf(__VA_ARGS__), printf("\n"))
#define FBLOGV(...) (printf(__VA_ARGS__), printf("\n"))
static unsigned long long mach_absolute_time() { return 1; }
struct NVRMNVDAFramebuffer {
    void *fDdcBrightness = nullptr; IOCommandGate *fDdcBrightnessGate = nullptr;
    IOWorkLoop *getWorkLoop() { return nullptr; }
    void scheduleDdcProbe(unsigned) {}
    bool modeOk; unsigned long long fEnableAbs = 0; bool fHotplugBorn = true, fConnectFired = false; unsigned fIndex = 0;
    bool fDpyLost = false;
    int notified = 0, audio = 0;
    explicit NVRMNVDAFramebuffer(bool ok) : modeOk(ok) {}
    bool applyMode() { return modeOk; }
    void refreshDisplayAudioProperties() { audio++; }
    void notifyConnectionChanged(int) { notified++; }
    IOReturn enableController();
};
IOReturn NVRMNVDAFramebuffer::enableController() BODY
int main() {
    NVRMNVDAFramebuffer good(true), bad(false), offline(false);
    offline.fDpyLost = true;
    IOReturn g = good.enableController(), b = bad.enableController(), o = offline.enableController();
    printf("RESULT good=%#x bad=%#x offline=%#x good_notified=%d bad_notified=%d bad_audio=%d"
           " offline_notified=%d offline_audio=%d\n", (unsigned)g, (unsigned)b, (unsigned)o,
           good.notified, bad.notified, bad.audio, offline.notified, offline.audio);
    return 0;
}
'''


def run(src):
    d = pathlib.Path(tempfile.mkdtemp(prefix="fbopen-"))
    c = d / "t.cpp"
    c.write_text(HARNESS.replace("BODY", body(src)))
    cc = subprocess.run(["clang++", "-std=c++17", "-w", str(c), "-o", str(d / "t")], capture_output=True, text=True)
    if cc.returncode:
        return "COMPILE " + cc.stderr
    return subprocess.run([str(d / "t")], capture_output=True, text=True, timeout=30).stdout


out = run((ROOT / SRC).read_text())
arm("a framebuffer whose boot mode set worked opens", "good=0 " in out, out)
arm("a reserved offline framebuffer opens", "offline=0 " in out, out)
arm("  without publishing connection or audio", "offline_notified=0 offline_audio=0" in out, out)
arm("a connected framebuffer returns an error on a rejected modeset", "bad=0xe00002bc" in out, out)
arm("  and does not announce a hot-plugged display it never lit", "bad_notified=0" in out and "good_notified=1" in out, out)
arm("  nor publish its audio properties", "bad_audio=0" in out, out)

# the base file can be named directly where the tree has no git history (a build host's copy)
base_file = os.environ.get("NVRMFB_BASE_SRC")
old = (subprocess.CompletedProcess([], 0, pathlib.Path(base_file).read_text(), "") if base_file else
       subprocess.run(["git", "-C", str(ROOT), "show", f"v2.8.1:{SRC}"], capture_output=True, text=True))
if old.returncode == 0:
    o = run(old.stdout)
    arm("negative control: 2.8.1 tries to set a mode even on the offline fixture", "offline=0xe00002bc" in o, o)
else:
    arm("negative control: 2.8.1's nvrm-fb.cpp is readable", False, old.stderr)
print(f"{fails} failed")
sys.exit(1 if fails else 0)
