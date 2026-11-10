from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'kexts/NVRMFB/fb/nvrm-fb.cpp').read_text()
begin = source.index('IOReturn NVRMNVDAFramebuffer::writeDdcBrightness(')
end = source.index('\n}\n', begin) + 3
fixture = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdio>
using UInt64 = uint64_t; using UInt32 = uint32_t; using IOReturn = int;
enum {kIOReturnSuccess, kIOReturnOffline, kIOReturnCannotLock, kNvrmDdcWrite, kMillisecondScale};
static bool available = true;
static uint64_t now = 100;
static int OSAddAtomic(int, int* value) {return *value;}
static uint64_t mach_absolute_time() {return now;}
static void clock_interval_to_deadline(unsigned ms, int, uint64_t* deadline) {*deadline = now + ms;}
struct NVRMDdcTransaction {
    bool *flag;
    bool acquired;
    explicit NVRMDdcTransaction(bool *busy) : flag(busy), acquired(!__atomic_exchange_n(flag, true, __ATOMIC_ACQUIRE)) {}
    ~NVRMDdcTransaction() { if (acquired) __atomic_store_n(flag, false, __ATOMIC_RELEASE); }
    bool held() const { return acquired; }
};
struct NVRMBoundedGate { explicit NVRMBoundedGate(int) {} bool held() const {return available;} };
struct NVRMNVDAFramebuffer {
    int fStopping = 0, fDdcBrightnessCall = 0;
    bool fDpyLost = false, fEdidSwapped = false, fHidden = false, fHeadSlept = false;
    bool fModeSet = true, fDdcPowered = true, fDdcInFlight = false;
    UInt32 fDdcSinkGeneration = 2;
    UInt64 fDdcPending = 0, fDdcWriteAfter = 0, scheduled = 0;
    unsigned writes = 0;
    IOReturn writeDdcBrightness();
    IOReturn ddcTransfer(unsigned op, unsigned addr, unsigned char* msg, unsigned count) {
        assert(op == kNvrmDdcWrite && addr == 0x37 && count == 7);
        const unsigned char expected[] = {0x51, 0x84, 0x03, 0x10, 0, 23, 0xbf};
        assert(!std::memcmp(msg, expected, count));
        ++writes;
        return kIOReturnSuccess;
    }
    void scheduleCall(int, UInt64 deadline) {scheduled = deadline;}
    void setProperty(const char*, UInt32, int) {}
};
'''
checks = r'''
int main() {
    NVRMNVDAFramebuffer fb;
    auto fire = [&]{return fb.writeDdcBrightness();};
    fb.fDdcPending = (UInt64(1) << 32) | 24; // old monitor
    assert(fire() == kIOReturnOffline && fb.writes == 0);
    fb.fDdcPending = (UInt64(2) << 32) | 24;
    fb.fDdcWriteAfter = 140;
    assert(fire() == kIOReturnSuccess && fb.scheduled == 140 && fb.fDdcPending && fb.writes == 0);
    now = 141;
    available = false;
    assert(fire() == kIOReturnCannotLock && fb.fDdcPending && fb.writes == 0);
    available = true;
    fb.fDdcInFlight = true;
    assert(fire() == kIOReturnCannotLock && fb.fDdcPending && fb.writes == 0);
    fb.fDdcInFlight = false;
    assert(fire() == kIOReturnSuccess && fb.writes == 1 && !fb.fDdcPending && fb.fDdcWriteAfter == 181);
    fb.fDdcPending = (UInt64(2) << 32) | 24;
    now = 200;
    fb.fDdcPowered = false;
    assert(fire() == kIOReturnOffline && fb.writes == 1);
    puts("PASS: native brightness VCP frame, deferred writes, lock retry, stale monitor and sleep");
}
'''
with tempfile.TemporaryDirectory() as name:
    src = Path(name) / 'native.cpp'
    exe = Path(name) / 'native'
    src.write_text(fixture + source[begin:end] + checks)
    subprocess.run(['clang++', '-std=c++17', '-Wall', '-Werror', str(src), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
