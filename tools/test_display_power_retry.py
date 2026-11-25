#!/usr/bin/env python3
"""Exercise the production display power transitions and asynchronous wake worker."""
import pathlib
import subprocess
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
source = (root / 'kexts/NVRMFB/fb/nvrm-fb.cpp').read_text()
head_start = source.index('bool NVRMNVDAFramebuffer::setHeadPower(')
power_end = source.index('IOReturn NVRMNVDAFramebuffer::setAttribute(', head_start)
power_methods = source[head_start:power_end]
power_state_start = power_methods.index('IOReturn NVRMNVDAFramebuffer::setPowerState(')
power_state = power_methods[power_state_start:]
gpu_start = source.index('void NVRMNVDAFramebuffer::gpuWakeFire(')
gpu_end = source.index('IOReturn NVRMNVDAFramebuffer::setAttributeForConnection(', gpu_start)
gpu_wake = source[gpu_start:gpu_end]

head_off = source[source.index('bool NVRMNVDAFramebuffer::headOff()'):head_start]
assert 'submitConfig(cfg, rep, NV_TRUE)' in head_off
assert 'fKms->applyModeSetConfig' not in head_off
assert 'setHeadPower(true)' not in power_state
assert power_state.index('familyDidWake()') < power_state.index('scheduleCall(fGpuWake, 0)')
assert 'scheduleCall(fGpuWake, 0)' in power_state
assert 'setHeadPower(true)' in gpu_wake
connection = source[source.index('IOReturn NVRMNVDAFramebuffer::setAttributeForConnection('):]
# Per-connection power remains a success-only no-op; head power follows setPowerState.
connection_power = connection[:connection.index('switch (attribute)')]
assert 'return kIOReturnSuccess' in connection_power and 'setHeadPower' not in connection_power

fixture = r'''
#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <mutex>
#include <thread>
using IOReturn=int;
using thread_call_t=int;
using thread_call_param_t=void *;
struct IOService {};
enum { kIOPMAckImplied=0, kIOFBNotifyDidPowerOn=1, kIOFBNotifyWillPowerOff=2 };
static bool disabled=false, lockAvailable=true;
static std::recursive_mutex displayLock;
using IORecursiveLock = std::recursive_mutex;
static IORecursiveLock *startLock() { return &displayLock; }
static bool IORecursiveLockHaveLock(IORecursiveLock *) { return lockAvailable; }
static thread_local char threadIdentity;
static void *IOThreadSelf() { return &threadIdentity; }
enum { THREAD_UNINT=0 };
static void IORecursiveLockSleep(IORecursiveLock *,void *,unsigned) { assert(false && "unexpected competing head transaction"); }
static void IORecursiveLockWakeup(IORecursiveLock *,void *,bool) {}
HEAD_GATE_INCLUDE
struct NVRMDisplayGate {
    NVRMDisplayGate() { displayLock.lock(); }
    ~NVRMDisplayGate() { displayLock.unlock(); }
    bool valid() { return lockAvailable; }
};
static bool PE_parse_boot_argn(const char*,char*,unsigned long) { return disabled; }
static int OSAddAtomic(int n,volatile int *value) { int old=*value;*value+=n;return old; }
#define FBLOG(...) ((void)0)
#define FBLOGV(...) ((void)0)
struct NVRMNVDAFramebuffer;
struct NVRMCallReference { explicit NVRMCallReference(NVRMNVDAFramebuffer *) {} };
struct NVRMNVDAFramebuffer {
    NVRMHeadGateState fPrimaryGate;
    NVRMHeadGateState &primaryGate() { return fPrimaryGate; }
    bool fHidden=false;
    bool fHeadSlept=false, fModeSet=true, sleepOK=true, wakeOK=true;
    bool fPowerOn=true, pagingState=true, fSysSlept=false, gpuReady=true;
    volatile int fStopping=0;
    int fReflipRc=0;
    void *fSurf=this;
    unsigned fHead=0, sleeps=0, notifications=0, familyWakes=0, audioPublishes=0;
    unsigned fGpuWake=1;
    std::atomic<unsigned> wakes{0};

    std::mutex queueMutex;
    bool fGpuWakeQueued=false;
    unsigned scheduleRequests=0;

    std::mutex reflipMutex;
    std::condition_variable reflipCv;
    bool blockReflip=false, reflipEntered=false, releaseReflip=false;

    void publishDisplayAudioProperties() { audioPublishes++; }
    unsigned ddcCalls=0; bool ddcLast=true;
    void ddcSetPowered(bool on) { ddcCalls++; ddcLast=on; }
    bool headOff() { sleeps++; return sleepOK; }
    bool reflip() {
        ++wakes;
        if (blockReflip) {
            std::unique_lock<std::mutex> lock(reflipMutex);
            reflipEntered=true;
            reflipCv.notify_all();
            reflipCv.wait(lock, [&] { return releaseReflip; });
        }
        return wakeOK;
    }
    void handleEvent(int event) { if(event==kIOFBNotifyDidPowerOn) notifications++; }
    bool rmGpuReady() { return gpuReady; }
    void familyDidWake() { familyWakes++; notifications++; pagingState=true; }
    void scheduleCall(thread_call_t, uint64_t) {
        std::lock_guard<std::mutex> lock(queueMutex);
        scheduleRequests++;
        fGpuWakeQueued=true;  // thread_call_enter coalesces repeated submissions
    }
    bool hasQueuedWake() {
        std::lock_guard<std::mutex> lock(queueMutex);
        return fGpuWakeQueued;
    }
    bool runQueuedWake() {
        {
            std::lock_guard<std::mutex> lock(queueMutex);
            if (!fGpuWakeQueued) return false;
            fGpuWakeQueued=false;
        }
        gpuWakeFire(this, nullptr);
        return true;
    }
    bool waitForReflipEntry(std::chrono::milliseconds timeout) {
        std::unique_lock<std::mutex> lock(reflipMutex);
        return reflipCv.wait_for(lock, timeout, [&] { return reflipEntered; });
    }
    void unblockReflip() {
        {
            std::lock_guard<std::mutex> lock(reflipMutex);
            releaseReflip=true;
        }
        reflipCv.notify_all();
    }
    bool setHeadPower(bool awake);
    IOReturn setPowerState(unsigned long ordinal,IOService *device);
    static void gpuWakeFire(thread_call_param_t p0,thread_call_param_t);
};
'''

checks = r'''
static void baselineTransitions() {
    NVRMNVDAFramebuffer f;
    assert(f.setHeadPower(true) && f.wakes==0);
    f.sleepOK=false; assert(!f.setHeadPower(false) && !f.fHeadSlept);
    f.sleepOK=true; assert(f.setHeadPower(false) && f.fHeadSlept);
    unsigned sleeps=f.sleeps; assert(f.setHeadPower(false) && f.sleeps==sleeps);
    f.setPowerState(0,nullptr);
    assert(!f.fPowerOn && f.ddcCalls==1 && !f.ddcLast);

    f.wakeOK=false;
    f.setPowerState(1,nullptr);
    // The PM path returns after queuing; a returned modeset failure stays retryable.
    assert(f.fPowerOn && f.hasQueuedWake() && f.fHeadSlept && f.wakes==0 && f.notifications==1);
    assert(f.runQueuedWake());
    assert(f.fHeadSlept && f.wakes==1 && f.audioPublishes==0);
    f.wakeOK=true;
    f.setPowerState(1,nullptr);
    assert(f.hasQueuedWake() && f.notifications==2);
    assert(f.runQueuedWake());
    assert(!f.fHeadSlept && f.wakes==2 && f.audioPublishes==1);
    f.setPowerState(1,nullptr); assert(f.runQueuedWake());
    assert(f.wakes==2);  // a duplicate worker does not relight an awake head

    // -nvrmnodpms leaves an awake head alone on display power-off.
    disabled=true;
    f.setPowerState(0,nullptr);
    assert(!f.fPowerOn && !f.fHeadSlept);
    unsigned before=f.wakes.load();
    f.setPowerState(1,nullptr); assert(f.runQueuedWake());
    assert(f.wakes==before && !f.fHeadSlept);
    disabled=false;

    // A rejected display sleep and unavailable gates retain their prior behavior.
    f.sleepOK=false; assert(!f.setHeadPower(false) && !f.fHeadSlept);
    f.fStopping=true; assert(!f.setHeadPower(true)); f.fStopping=false;
    lockAvailable=false; assert(!f.setHeadPower(true)); lockAvailable=true;
    // DDC follows every PM transition, including both power-on requests above.
    assert(f.ddcCalls==6 && f.ddcLast);
}

static void framebufferWakeBeforeGpuReady() {
    NVRMNVDAFramebuffer f;
    f.gpuReady=false;
    f.fHeadSlept=true;
    f.fSysSlept=true;
    f.pagingState=false;
    f.setPowerState(1,nullptr);
    assert(f.familyWakes==1 && f.pagingState && !f.fSysSlept && f.fPowerOn);
    assert(f.hasQueuedWake() && f.wakes==0);
    assert(f.runQueuedWake());  // framebuffer PM arrives before GPU resume
    assert(f.fHeadSlept && f.wakes==0 && f.audioPublishes==0);

    f.gpuReady=true;  // NVRM's later GPU-ready notification queues the same worker
    f.scheduleCall(f.fGpuWake,0);
    f.scheduleCall(f.fGpuWake,0);
    assert(f.hasQueuedWake() && f.scheduleRequests==3);
    assert(f.runQueuedWake());
    assert(!f.fHeadSlept && f.wakes==1 && f.audioPublishes==1);
    assert(!f.runQueuedWake());
    f.scheduleCall(f.fGpuWake,0); assert(f.runQueuedWake());
    assert(f.wakes==1 && f.familyWakes==1); // coalesced/late duplicate is harmless
}

static void gpuReadyBeforeFramebufferWake() {
    NVRMNVDAFramebuffer f;
    f.setPowerState(0,nullptr);
    assert(!f.fPowerOn && f.fHeadSlept);
    f.fSysSlept=true; f.pagingState=false;
    f.scheduleCall(f.fGpuWake,0); // GPU resumes before the framebuffer PM callback
    assert(f.runQueuedWake());
    assert(f.fHeadSlept && f.wakes==0 && f.familyWakes==0);
    f.setPowerState(1,nullptr);
    assert(f.familyWakes==1 && f.pagingState && f.fPowerOn && f.hasQueuedWake());
    f.scheduleCall(f.fGpuWake,0); f.scheduleCall(f.fGpuWake,0);
    assert(f.runQueuedWake());
    assert(!f.fHeadSlept && f.wakes==1 && f.audioPublishes==1);
    assert(!f.runQueuedWake());
}

static void powerOffCancelsPendingWake() {
    NVRMNVDAFramebuffer f;
    f.setPowerState(1,nullptr);
    assert(f.hasQueuedWake() && f.wakes==0);
    f.setPowerState(0,nullptr); // power-off wins before the queued worker runs
    assert(!f.fPowerOn && f.fHeadSlept && f.hasQueuedWake());
    assert(f.runQueuedWake());
    assert(f.wakes==0 && f.fHeadSlept && f.audioPublishes==0);
}

static void blockedModesetDoesNotBlockPowerCallback() {
    NVRMNVDAFramebuffer f;
    f.fHeadSlept=true;
    f.fSysSlept=true;
    f.fPowerOn=false;
    f.pagingState=false;
    f.blockReflip=true;
    std::mutex doneMutex;
    std::condition_variable doneCv;
    bool pmReturned=false;
    std::thread pm([&] {
        f.setPowerState(1,nullptr);
        {
            std::lock_guard<std::mutex> lock(doneMutex);
            pmReturned=true;
        }
        doneCv.notify_all();
    });
    bool returned=false;
    {
        std::unique_lock<std::mutex> lock(doneMutex);
        returned=doneCv.wait_for(lock,std::chrono::milliseconds(500),[&] { return pmReturned; });
    }
    if (!returned) f.unblockReflip(); // release a regression that re-entered the blocking modeset on the PM thread
    pm.join();
    assert(returned && f.familyWakes==1 && f.pagingState && f.hasQueuedWake());

    std::thread worker([&] { assert(f.runQueuedWake()); });
    const bool entered=f.waitForReflipEntry(std::chrono::milliseconds(500));
    if (!entered) f.unblockReflip();
    // The reflip is now genuinely blocked, while setPowerState and IOGraphics wake work have already completed.
    assert(entered && pmReturned && f.familyWakes==1 && f.pagingState && f.fHeadSlept);
    f.scheduleCall(f.fGpuWake,0);
    f.scheduleCall(f.fGpuWake,0);
    f.unblockReflip();
    worker.join();
    assert(f.wakes==1 && !f.fHeadSlept && f.audioPublishes==1 && f.hasQueuedWake());
    assert(f.runQueuedWake()); // the coalesced duplicate sees an already-lit head
    assert(f.wakes==1 && !f.hasQueuedWake() && f.familyWakes==1);
}

int main() {
    baselineTransitions();
    framebufferWakeBeforeGpuReady();
    gpuReadyBeforeFramebufferWake();
    powerOffCancelsPendingWake();
    blockedModesetDoesNotBlockPowerCallback();
    puts("PASS: async PM wake, DPMS and returned-failure retry, DDC, both GPU/framebuffer wake orderings, coalescing, power-off cancellation, and blocked modeset isolation");
}
'''

with tempfile.TemporaryDirectory() as directory:
    path = pathlib.Path(directory)
    include = '#include "' + str(root / 'kexts/NVRMFB/fb/nvrm-head-gate.h') + '"'
    cpp = fixture.replace('HEAD_GATE_INCLUDE', include) + power_methods + gpu_wake + checks
    (path / 'test.cpp').write_text(cpp)
    subprocess.run(['clang++', '-std=c++17', '-pthread', str(path / 'test.cpp'),
                    '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
