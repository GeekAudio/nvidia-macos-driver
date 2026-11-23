#!/usr/bin/env python3
'''Compile and exercise production NVRMFB connection and DDC methods.'''
import pathlib, subprocess, sys, tempfile
here = pathlib.Path(__file__).resolve()
roots = [pathlib.Path(sys.argv[1]).resolve()] if len(sys.argv) > 1 else [here.parent.parent, here.parent.parent.parent]
root = next((p for p in roots if (p / 'kexts/NVRMFB/fb/nvrm-fb.cpp').exists() or (p / 'source.cpp').exists()), None)
if root is None: raise SystemExit('source.cpp or kexts/NVRMFB/fb/nvrm-fb.cpp not found')
srcpath = root / 'kexts/NVRMFB/fb/nvrm-fb.cpp'
if not srcpath.exists(): srcpath = root / 'source.cpp'
src = srcpath.read_text(encoding='utf-8')
def extract(text, marker):
    start = text.index(marker); opening = text.index('{', start); depth = 0
    for pos in range(opening, len(text)):
        if text[pos] == '{': depth += 1
        elif text[pos] == '}':
            depth -= 1
            if not depth: return text[start:pos + 1]
    raise AssertionError(f'unclosed production function: {marker}')
attrs = extract(src, 'IOReturn NVRMNVDAFramebuffer::getAttributeForConnection(')
ddc = extract(src, 'IOReturn NVRMNVDAFramebuffer::getDDCBlock(')
notify = extract(src, 'void NVRMNVDAFramebuffer::notifyConnectionChanged(')
callback = extract(src, 'void NVRMNVDAFramebuffer::connectNotifyFire(')
assert 'scheduleCall(fConnectNotify, deadline)' in notify and 'thread_call_enter' not in notify and 'startLock()' not in notify
assert 'NVRMCallReference callReference(me)' in callback and 'me->fConnect.proc(me->fConnect.target, me->fConnect.ref)' in callback
assert '&fConnectNotify' in extract(src, 'void NVRMNVDAFramebuffer::stop(')
assert src.index('fConnectNotify = nvrmfbCall(connectNotifyFire, this)') < src.index('super::start(provider)')
assert 'return !fDpyLost && !fHidden && fEdidSize >= 128;' in src
hotplug = extract(src, 'void NVRMNVDAFramebuffer::hotplugFire(')
missing = hotplug[hotplug.index('for (int k = 0; k < gNDpy; k++'):]
missing = missing[:missing.index('const unsigned displayCount')]
assert 'if (!kms->getDynamicDisplayInfo(gDev, dd) || dd->connected) continue;' in missing
assert 'fb->fDpyLost = true;' in missing and 'fb->fEdidSize = 0;' in missing and 'fb->clearDisplayAudioProperties();' in missing
unlocked = hotplug.index('IORecursiveLockUnlock(sl);', hotplug.index('const unsigned displayCount'))
assert unlocked < hotplug.index('notify[f]->notifyConnectionChanged(500);')
assert unlocked < hotplug.index('fb->reflip()')
assert 'fb->fDpyLost = false;' in hotplug and 'fb->notifyConnectionChanged(500);' in hotplug
assert 'fb->fSurf =' not in hotplug and 'fb->fSize =' not in hotplug
standby = extract(src, 'bool NVRMNVDAFramebuffer::activateStandby(')
assert standby.index('if (!sink.connected) return false;') < standby.index('switchMode(kModeId')
assert standby.index('if (result != kIOReturnSuccess)') < standby.index('takeEdid(sink.edid')
assert standby.index('takeEdid(sink.edid') < standby.index('fDpyLost = false;')
assert standby.index('clock_interval_to_deadline(15000, kMillisecondScale, &fBlackLutDeadline)') < standby.index('fDpyLost = false;')
gamma = extract(src, 'IOReturn NVRMNVDAFramebuffer::nvrmSetGamma(')
assert 'black && (fStandby || (fBlackLutDeadline && mach_absolute_time() < fBlackLutDeadline))' in gamma
assert gamma.index('all-black table ignored') < gamma.index('if (!fGamma)')
assert 'fBlackLutDeadline = 0' not in gamma
assert standby.index('fDpyLost = false;') < standby.index('publishDisplayAudioProperties();')
assert 'if (standby->activateStandby()) standby->notifyConnectionChanged(100);' in hotplug
assert hotplug.index('IORecursiveLockUnlock(sl);', hotplug.index('const unsigned displayCount')) < hotplug.index('standby->activateStandby()')
for method in ('applyMode(', 'flipToMemory(', 'setDisplayMode('):
    assert 'if (fStandby) return' in extract(src, 'NVRMNVDAFramebuffer::' + method)
assert 'if (fDpyLost) return kIOReturnSuccess;' in extract(src, 'IOReturn NVRMNVDAFramebuffer::enableController(')
vbl = extract(src, 'void NVRMNVDAFramebuffer::vblFire(')
assert '!me->fStandby && me->fOwnsHead && me->fModeSet && !me->fDpyLost &&' in vbl
assert vbl.index('!me->fStandby && me->fOwnsHead') < vbl.index('registerVblankIntrCallback(')
fixture = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
using IOIndex=unsigned; using IOSelect=unsigned; using IOByteCount=unsigned long; using IOOptionBits=unsigned;
using UInt32=unsigned; using UInt8=unsigned char; using IOReturn=int; using thread_call_param_t=void*; using thread_call_t=void*;
enum { kIOReturnSuccess=0,kIOReturnUnsupported=-1,kIOReturnBadArgument=-2,kConnectionEnable=1,kConnectionFlags=2,
kConnectionColorModesSupported=3,kConnectionColorDepthsSupported=4,kConnectionControllerColorDepth=5,
kConnectionControllerDepthsSupported=6,kConnectionColorMode=7,kConnectionCheckEnable=8,kConnectionDisplayFlags=9,
kConnectionSupportsHLDDCSense=10,kConnectionSupportsAppleSense=11,kConnectionSupportsLLDDCSense=12,
kConnectionDisplayParameterCount=13,kConnectionDisplayParameters=14,kIOConnectionBuiltIn=1,kIODDCBlockTypeEDID=1 };
#define FBSEL(a) char _s[5]={(char)((a)>>24),(char)((a)>>16),(char)((a)>>8),(char)(a),0}
#define FBLOG(...) do { if (false) std::printf(__VA_ARGS__); } while (0)
static void nvu_copy(void*d,const void*s,size_t n){std::memcpy(d,s,n);}
static void clock_interval_to_deadline(unsigned ms,int,std::uint64_t*d){*d=ms;} static int kMillisecondScale=1;
struct OSObject{int refs=1;void retain(){++refs;}void release(){--refs;}};
class IOFramebuffer {
public:
    IOReturn getAttributeForConnection(IOIndex,IOSelect,std::uintptr_t*){return 77;}
    IOReturn open();
};
struct NVRMNVDAFramebuffer:OSObject,IOFramebuffer{
volatile int fConnectOwed=0;
using super=IOFramebuffer; bool fDpyLost=false,fHidden=false,fInternal=false,fConnectFired=false,fRotationEnabled=false; unsigned fEdidSize=0,fIndex=0; UInt8 fEdid[256]={};
bool fHotplugBorn=false,fHotplugStarted=false,hasAccelIndex=false,hasAccelPath=false;
unsigned fEnableAbs=0,opens=0;
bool getProperty(const char *name) { return !std::strcmp(name, "IOAccelIndex") ? hasAccelIndex : hasAccelPath; }
volatile int fStopping=0; thread_call_t fConnectNotify=reinterpret_cast<void*>(1);
struct{void(*proc)(void*,void*);void*target;void*ref;}fConnect{}; unsigned scheduled=0;std::uint64_t deadline=0;
void scheduleCall(thread_call_t,std::uint64_t d){++scheduled;deadline=d;retain();}
IOReturn getAttributeForConnection(IOIndex,IOSelect,std::uintptr_t*); IOReturn getDDCBlock(IOIndex,UInt32,IOSelect,IOOptionBits,UInt8*,IOByteCount*);
void notifyConnectionChanged(unsigned); static void connectNotifyFire(thread_call_param_t,thread_call_param_t);
};
static unsigned displayGateDepth=0;
struct NVRMDisplayGate {
    NVRMDisplayGate(){++displayGateDepth;}
    ~NVRMDisplayGate(){--displayGateDepth;}
    bool valid() const {return true;}
};
static NVRMNVDAFramebuffer *gFBs[1]={};
IOReturn IOFramebuffer::open() {
    assert(displayGateDepth==0);
    auto *fb=static_cast<NVRMNVDAFramebuffer *>(this);
    ++fb->opens;
    fb->fEnableAbs=1;
    fb->fConnect.proc=[](void *,void *){};
    fb->notifyConnectionChanged(1500);
    return kIOReturnSuccess;
}
class NVRMCallReference{OSObject*p;public:explicit NVRMCallReference(OSObject*v):p(v){}~NVRMCallReference(){p->release();}};
static int OSAddAtomic(int,volatile int*v){return *v;}
static bool OSCompareAndSwap(int o,int n,volatile int*v){if(*v!=o)return false;*v=n;return true;}
'''
checks = r'''
int main(){NVRMNVDAFramebuffer f;std::uintptr_t v=0;f.fEdidSize=128;
assert(f.getAttributeForConnection(0,kConnectionEnable,&v)==kIOReturnSuccess&&v==1);
assert(f.getAttributeForConnection(0,kConnectionCheckEnable,&v)==kIOReturnSuccess&&v==1);
assert(f.getAttributeForConnection(0,99,&v)==77);f.fDpyLost=true;f.fEdidSize=0;
assert(f.getAttributeForConnection(0,kConnectionEnable,&v)==kIOReturnSuccess&&v==0);
assert(f.getAttributeForConnection(0,kConnectionCheckEnable,&v)==kIOReturnSuccess&&v==0);
assert(f.getAttributeForConnection(0,kConnectionSupportsHLDDCSense,&v)==kIOReturnUnsupported);
UInt8 out[128]={};IOByteCount n=128;assert(f.getDDCBlock(0,1,kIODDCBlockTypeEDID,0,out,&n)==kIOReturnUnsupported);
f.fDpyLost=false;f.fEdidSize=128;for(unsigned i=0;i<128;i++)f.fEdid[i]=(UInt8)i;n=128;
assert(f.getDDCBlock(0,1,kIODDCBlockTypeEDID,0,out,&n)==kIOReturnSuccess&&n==128&&out[127]==127);
f.fHidden=true;
assert(f.getAttributeForConnection(0,kConnectionCheckEnable,&v)==kIOReturnSuccess&&v==0);
assert(f.getDDCBlock(0,1,kIODDCBlockTypeEDID,0,out,&n)==kIOReturnUnsupported);
f.fHidden=false;
assert(f.getDDCBlock(0,1u<<25,kIODDCBlockTypeEDID,0,out,&n)==kIOReturnUnsupported);
n=127;assert(f.getDDCBlock(0,1,kIODDCBlockTypeEDID,0,out,&n)==kIOReturnBadArgument);
assert(f.getDDCBlock(0,0,kIODDCBlockTypeEDID,0,out,&n)==kIOReturnUnsupported);
assert(f.getDDCBlock(0,1,99,0,out,&n)==kIOReturnUnsupported);f.notifyConnectionChanged(500);
assert(f.scheduled==1&&f.deadline==500&&f.refs==2);unsigned calls=0;
f.fConnect.proc=[](void*t,void*r){++*static_cast<unsigned*>(t);assert(r==reinterpret_cast<void*>(7));};
f.fConnect.target=&calls;f.fConnect.ref=reinterpret_cast<void*>(7);NVRMNVDAFramebuffer::connectNotifyFire(&f,nullptr);
assert(calls==1&&f.fConnectFired&&f.refs==1);

NVRMNVDAFramebuffer primary, late;
gFBs[0]=&primary;
auto fire=[&] {late.retain();NVRMNVDAFramebuffer::connectNotifyFire(&late,nullptr);};
late.hasAccelIndex=late.hasAccelPath=true;
fire();assert(late.opens==0); // Original boot framebuffer ownership.
late.fHotplugBorn=true;
fire();assert(late.opens==0); // Start is not complete.
late.fHotplugStarted=true;late.hasAccelPath=false;
fire();assert(late.opens==0); // The accelerator has not bound it.
late.hasAccelPath=true;late.fDpyLost=true;
fire();assert(late.opens==0); // It was disconnected before activation.
late.fDpyLost=false;
fire();
assert(late.opens==1&&late.scheduled==1&&late.deadline==1500);
assert(primary.scheduled==1&&primary.deadline==100);
fire();assert(late.opens==1&&primary.scheduled==1);
puts("PASS: connection and DDC methods, plus late framebuffer activation after the accelerator binds it");}
'''
with tempfile.TemporaryDirectory() as td:
    temp=pathlib.Path(td); cpp=temp/'check.cpp'; cpp.write_text(fixture+'\n'+attrs+'\n'+ddc+'\n'+notify+'\n'+callback+'\n'+checks,encoding='utf-8')
    subprocess.run(['clang++','-std=c++17','-Wall','-Wextra',str(cpp),'-o',str(temp/'check')],check=True)
    subprocess.run([str(temp/'check')],check=True)
print('PASS: hotplug transition path; omitted handles require successful offline query; callback and relight run outside display gate; scanout stays allocated')
