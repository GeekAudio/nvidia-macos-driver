#!/usr/bin/env python3
"""Run production topology publication and both slot consumers with OS doubles."""
from pathlib import Path
import re
from test_support import ROOT, FB, method, run

header = (ROOT / 'kexts/NVRM/nvrm_display_identity.h').read_text()
accel = (ROOT / 'kexts/NVRM/accel/nvrm-accel.cpp').read_text()
agdc = (ROOT / 'kexts/NVRMAGDC/nvrm-agdc.cpp').read_text()
start = method(FB, 'bool NVRMNVDAFramebuffer::start(')
assert start.index('configureRotation();') < start.index('configureControllerIdentity(provider)') < start.index('super::start(provider)')
assert start.count('"IOFBDependentIndex"') == 0
accel_start = method(accel, 'bool start(IOService *provider)')
assert accel_start.index('fPCI = ') < accel_start.index('fFBNote = ')

constants = '\n'.join(re.findall(r'^#define NVRM_[^\n]+', header, re.M))
helpers = '\n'.join(method(header, name) for name in (
    'static inline bool nvrmFramebufferIndex(', 'static inline IOService *nvrmFramebufferPCIOwner('))

fixture = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <vector>
using UInt32=uint32_t;
#define ALOG(...) ((void)0)
#define FBLOG(...) ((void)0)
#define SCANLOG(...) ((void)0)
#define OSDynamicCast(C,p) dynamic_cast<C *>(p)
#define OSSafeReleaseNULL(p) do { if(p) { (p)->release(); (p)=nullptr; } } while(0)
struct OSObject { int refs=1; virtual ~OSObject(){}; void retain(){++refs;} void release(){assert(refs>0);if(!--refs)delete this;} };
struct OSNumber:OSObject { unsigned long long value; explicit OSNumber(unsigned long long n):value(n){};
 unsigned long long unsigned64BitValue(){return value;} unsigned unsigned32BitValue(){return value;} };
struct OSBoolean:OSObject {};
static OSBoolean trueObject,falseObject;
static OSObject *kOSBooleanTrue=&trueObject,*kOSBooleanFalse=&falseObject;
struct OSDictionary:OSObject {};
struct IOService;
static std::vector<IOService *> registry;
struct OSIterator:OSObject { std::vector<IOService *> values; size_t next=0;
 explicit OSIterator(const std::vector<IOService *> &); ~OSIterator(); OSObject *getNextObject(); };
struct IOService:OSObject {
 IOService *parent; std::string name; unsigned long long id; bool pci=false,accept=true;
 std::map<std::string,OSObject *> properties;
 IOService(IOService *p,const char *n,unsigned long long i):parent(p),name(n),id(i){}
 ~IOService(){for(auto &p:properties)p.second->release();}
 IOService *getProvider(){return parent;} const char *getName(){return name.c_str();}
 unsigned long long getRegistryEntryID(){return id;}
 void *metaCast(const char *n){return pci&&!strcmp(n,"IOPCIDevice")?this:nullptr;}
 OSObject *getProperty(const char *n){auto i=properties.find(n);return i==properties.end()?nullptr:i->second;}
 OSObject *copyProperty(const char *n){auto *v=getProperty(n);if(v)v->retain();return v;}
 bool setProperty(const char *n,OSObject *v){if(!accept)return false;v->retain();removeProperty(n);properties[n]=v;return true;}
 bool setProperty(const char *n,unsigned long long v,unsigned){auto *p=new OSNumber(v);bool ok=setProperty(n,p);p->release();return ok;}
 void removeProperty(const char *n){auto i=properties.find(n);if(i!=properties.end()){i->second->release();properties.erase(i);}}
 static OSDictionary *serviceMatching(const char *n){assert(!strcmp(n,"NVRMNVDAFramebuffer"));return new OSDictionary;}
 static OSIterator *getMatchingServices(OSDictionary *){return new OSIterator(registry);}
};
OSIterator::OSIterator(const std::vector<IOService *> &v):values(v){for(auto *p:values)p->retain();}
OSIterator::~OSIterator(){for(auto *p:values)p->release();}
OSObject *OSIterator::getNextObject(){return next<values.size()?values[next++]:nullptr;}
using IOPCIDevice=IOService; struct IONotifier{};
static int boot=-1;
static bool PE_parse_boot_argn(const char *n,int *out,size_t size){assert(!strcmp(n,"nvfbcontrollers")&&size==sizeof(int));if(boot<0)return false;*out=boot;return true;}
static bool OSCompareAndSwapPtr(void *old,void *value,void *volatile *slot){if(*slot!=old)return false;*slot=value;return true;}
static void OSBitOrAtomic(unsigned value,volatile unsigned *slot){*slot|=value;}
@@CONSTANTS@@
@@HELPERS@@
struct NVRMNVDAFramebuffer:IOService {
 unsigned fIndex; bool fRotationEnabled=true,fHotplugBorn=false,fStandby=false;
 NVRMNVDAFramebuffer(IOService *p,unsigned i):IOService(p,"NVRMFramebuffer",p->id+1000),fIndex(i){}
 bool configureControllerIdentity(IOService *);
};
@@PUBLISH@@
static const unsigned kNvMaxFB=5;
static unsigned gNvHeadsPublished=0;
static int gNvAccelArmed=0;
static int gNvScanout=0;
struct Timer {
 unsigned queued=0;
 void setTimeoutMS(unsigned delay){assert(delay>0);++queued;}
};
struct DisplayMachine { unsigned getFramebufferCount(){return 0;} };
struct NVAccel {
 IOService *fPCI,*fFB=nullptr,*fFBs[kNvMaxFB]={}; DisplayMachine *fDM=nullptr;
 Timer *fLateFramebufferTimer=nullptr;
 explicit NVAccel(IOService *p):fPCI(p){}
 ~NVAccel(){if(fFB)fFB->release();for(auto *p:fFBs)if(p)p->release();}
 @@CALLBACK@@
};
enum {kAgdcMaxFB=4};
static IOService *gAgdcFB[kAgdcMaxFB]={};
static unsigned gAgdcNFB=0;
static int gAgdcScanLogged=0;
static char gAgdcFBMap[256]={};
@@DROP@@
@@SCAN@@
static unsigned long long number(IOService *s,const char *key){auto *p=dynamic_cast<OSNumber *>(s->getProperty(key));assert(p);return p->value;}
int main(){
 IOService gpu(nullptr,"GPU",1),nvrm(&gpu,"NVRM",2),otherGPU(nullptr,"other-GPU",3),otherRoot(&otherGPU,"NVRM",4);
 gpu.pci=otherGPU.pci=true;
 std::vector<IOService *> nubs; std::vector<NVRMNVDAFramebuffer *> heads;
 for(unsigned i=0;i<4;++i){
  auto *nub=new IOService(&nvrm,"NVRMDisplay",100+i);nubs.push_back(nub);
  auto *fb=new NVRMNVDAFramebuffer(nub,i);heads.push_back(fb);
  fb->setProperty("NVRMIndependentControllers",kOSBooleanTrue);
  assert(fb->configureControllerIdentity(nub));
  assert(number(fb,NVRM_DISPLAY_INDEX_KEY)==i&&number(fb,"IOFBDependentIndex")==0);
  assert(number(fb,"NVRMDisplayIdentityVersion")==NVRM_DISPLAY_IDENTITY_VERSION);
  auto group=number(fb,"IOFBDependentID");
  assert(group==(i?nub->id:NVRM_LEGACY_CONTROLLER_ID));
  for(unsigned j=0;j<i;++j)assert(group!=number(heads[j],"IOFBDependentID"));
  registry.push_back(fb);
 }
 {
  // Publishing the legacy Intel panel first must not steal NVIDIA's primary slot.
  IOService panel(&nvrm,"NMIntelFB",700);panel.setProperty("IOFBDependentIndex",4ull,32);
  NVAccel accelerator(&gpu);
  NVAccel::fbPublished(&accelerator,nullptr,&panel,nullptr);assert(!accelerator.fFB&&accelerator.fFBs[4]==&panel);
  for(auto *fb:heads)NVAccel::fbPublished(&accelerator,nullptr,fb,nullptr);
  assert(accelerator.fFB==heads[0]&&(gNvHeadsPublished&0x1f)==0x1f);
  for(unsigned i=0;i<4;++i)assert(accelerator.fFBs[i]==heads[i]);
  nvdaAgdcScan(&gpu);assert(gAgdcNFB==4);
  for(unsigned i=0;i<4;++i)assert(gAgdcFB[i]==heads[i]);
  // A second GPU's slot zero must not claim this GPU's slots.
  IOService foreign(&otherRoot,"NVRMFramebuffer",800);foreign.setProperty(NVRM_DISPLAY_INDEX_KEY,0ull,32);
  registry.insert(registry.begin(),&foreign);NVAccel::fbPublished(&accelerator,nullptr,&foreign,nullptr);
  nvdaAgdcScan(&gpu);assert(gAgdcNFB==4&&gAgdcFB[0]==heads[0]);
  // Invalid explicit slots cannot alias slot zero through truncation or fallback.
  IOService bad(&nvrm,"NVRMFramebuffer",900);bad.setProperty("IOFBDependentIndex",0ull,32);
  bad.setProperty(NVRM_DISPLAY_INDEX_KEY,1ull<<40,64);
  registry.insert(registry.begin(),&bad);NVAccel::fbPublished(&accelerator,nullptr,&bad,nullptr);
  nvdaAgdcScan(&gpu);assert(gAgdcNFB==4&&gAgdcFB[0]==heads[0]);
  auto *invalid=new OSObject;bad.setProperty(NVRM_DISPLAY_INDEX_KEY,invalid);invalid->release();
  unsigned long long slot=99;assert(!nvrmFramebufferIndex(&bad,&slot)&&slot==99);
  NVAccel::fbPublished(&accelerator,nullptr,&bad,nullptr);nvdaAgdcScan(&gpu);assert(gAgdcNFB==4);
  nvdaAgdcDropTable();registry.erase(registry.begin(),registry.begin()+2);
  // The opt-out restores shared grouping, without changing physical slots.
  boot=0;
  for(unsigned i=0;i<4;++i){assert(heads[i]->configureControllerIdentity(nubs[i]));
   assert(number(heads[i],"IOFBDependentID")==NVRM_LEGACY_CONTROLLER_ID);
   assert(number(heads[i],"IOFBDependentIndex")==i&&number(heads[i],NVRM_DISPLAY_INDEX_KEY)==i);}
  // Rotation disabled (including Sequoia) wins over an explicit split request.
  boot=1;heads[2]->fRotationEnabled=false;assert(heads[2]->configureControllerIdentity(nubs[2]));
  assert(number(heads[2],"IOFBDependentIndex")==2);
  // A newly discovered output opens a new controller, keeping its accelerator slot.
  boot=0;heads[2]->fHotplugBorn=true;assert(heads[2]->configureControllerIdentity(nubs[2]));
  assert(number(heads[2],"IOFBDependentID")==nubs[2]->id);
  assert(number(heads[2],"IOFBDependentIndex")==0&&number(heads[2],NVRM_DISPLAY_INDEX_KEY)==2);
  heads[2]->fHotplugBorn=false;
  // The offline slot joins the boot controller even with independent controllers enabled.
  boot=1;heads[2]->fRotationEnabled=true;heads[2]->fStandby=true;
  assert(heads[2]->configureControllerIdentity(nubs[2]));
  assert(number(heads[2],"IOFBDependentID")==NVRM_LEGACY_CONTROLLER_ID);
  assert(number(heads[2],"IOFBDependentIndex")==2&&number(heads[2],NVRM_DISPLAY_INDEX_KEY)==2);
  heads[2]->fStandby=false;
  // Older framebuffer builds still register using their original dependent indices.
  for(auto *fb:heads)fb->removeProperty(NVRM_DISPLAY_INDEX_KEY);
  heads[0]->setProperty("IOFBDependentIndex",0ull,32);heads[1]->setProperty("IOFBDependentIndex",1ull,32);
  heads[2]->setProperty("IOFBDependentIndex",2ull,32);heads[3]->setProperty("IOFBDependentIndex",3ull,32);
  nvdaAgdcScan(&gpu);assert(gAgdcNFB==4);nvdaAgdcDropTable();
  for(auto *fb:heads){unsigned long long slot=99;assert(nvrmFramebufferIndex(fb,&slot)&&slot==fb->fIndex);}
  // Repeated publication does not replace the original slot or leak references.
  int before=heads[0]->refs;NVAccel::fbPublished(&accelerator,nullptr,heads[0],nullptr);assert(heads[0]->refs==before);
 }
 {
  // Keep boot registration and the optional Intel panel out of the late worker.
  IOService panel(&nvrm,"NMIntelFB",700);panel.setProperty("IOFBDependentIndex",4ull,32);
  Timer timer;NVAccel accelerator(&gpu);accelerator.fLateFramebufferTimer=&timer;
  NVAccel::fbPublished(&accelerator,nullptr,heads[0],nullptr);assert(timer.queued==0);
  gNvAccelArmed=1;
  NVAccel::fbPublished(&accelerator,nullptr,heads[1],nullptr);assert(timer.queued==0);
  gNvScanout=1;
  NVAccel::fbPublished(&accelerator,nullptr,&panel,nullptr);assert(timer.queued==0);
  NVAccel::fbPublished(&accelerator,nullptr,heads[2],nullptr);assert(timer.queued==1);
  NVAccel::fbPublished(&accelerator,nullptr,heads[2],nullptr);assert(timer.queued==1);
 }
 registry.clear();
 // Property failure refuses startup; it cannot publish a partially usable split.
 NVRMNVDAFramebuffer failed(nubs[1],1);failed.accept=false;assert(!failed.configureControllerIdentity(nubs[1]));
 for(auto *fb:heads){assert(fb->refs==1);fb->release();}
 for(auto *nub:nubs){assert(nub->refs==1);nub->release();}
 puts("PASS: production four-controller publication, accelerator and AGDC slots 0..3, Intel slot 4, PCI isolation, invalid/64-bit slots, legacy fallback, opt-out, rotation-disabled grouping and balanced references");
}
'''
for token, body in {
    '@@CONSTANTS@@': constants,
    '@@HELPERS@@': helpers,
    '@@PUBLISH@@': method(FB, 'bool NVRMNVDAFramebuffer::configureControllerIdentity('),
    '@@CALLBACK@@': method(accel, 'static bool fbPublished('),
    '@@DROP@@': method(agdc, 'static void nvdaAgdcDropTable('),
    '@@SCAN@@': method(agdc, 'static void nvdaAgdcScan(IOService *pci)\n{'),
}.items():
    fixture = fixture.replace(token, body)
run(fixture)
