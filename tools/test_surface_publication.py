#!/usr/bin/env python3
"""Force CPU import to overlap GPU surface publication in production methods."""
import pathlib
import subprocess
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
source = (root / 'plugin/NVMTLObjects.m').read_text()
assert 'nvmtl_release_forget_env(environ)' in source, 'surface knobs must follow the current release environment policy'
wrapper_start = source.index('static BOOL nvmtl_surfin_reader_only(NVMTLTexture *r);')
wrapper_end = source.index('static unsigned nvmtl_surf_id', wrapper_start)
wrappers = source[wrapper_start:wrapper_end]
reader_start = source.index('// Opt-in only. A read-write reader lock')
reader_end = source.index('// IOSurface locking wires memory and updates its seed', reader_start)
reader_methods = source[reader_start:reader_end]
start = source.index('static id nvmtl_surface_sync(NVMTLTexture *t) {')
end = source.index('- (NSUInteger)width ', start)
plane_macro = next(line for line in source.splitlines() if line.startswith('#define NVMTL_VP(')) + '\n'
methods = wrappers + reader_methods + source[start:end]
fixture = r'''
#import <Foundation/Foundation.h>
#include "nvmtl_reader_skip.h"
#include <assert.h>
#include <dispatch/dispatch.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#import <objc/runtime.h>
typedef struct __IOSurface { unsigned seed; unsigned char cpu[4]; unsigned planes; } Surface;
typedef int IOReturn;
enum { kIOSurfaceLockReadOnly = 1 };
typedef struct { unsigned w,h,bpp; unsigned char gpu[4]; } Image;
typedef void nvk_buffer;
static const char *test_program = "test";
const char *getprogname(void) { return test_program; }
typedef void *(*nvmtl_vk_lease_retain_fn)(void *);
typedef void (*nvmtl_vk_lease_release_fn)(void *);
static dispatch_semaphore_t entered, release_read;
static unsigned uploads, gpu_seed;
static unsigned wholeUploads, busyPlane, dirtyPlane;
static unsigned long long g_surfin_skip, g_surfin_copy, g_surfin_own_skip;
// Keep plane history off in this single-plane publication fixture. The actual
// history helpers are exercised separately by test_surface_plane_seed.py.
static BOOL nvmtl_plane_seed_on(void) { return NO; }
static BOOL nvmtl_own_bumps_cover(Surface *s, NSUInteger p, uint32_t from, uint32_t to) { return NO; }
static void nvmtl_own_bump_add(Surface *s, NSUInteger p, uint32_t from, uint32_t to) {}
static size_t IOSurfaceGetPlaneCount(Surface *s) { return s->planes; }
static uint32_t IOSurfaceGetSeed(IOSurfaceRef s) { return s->seed; }
static unsigned IOSurfaceGetID(IOSurfaceRef s) { return 1; }
static IOReturn IOSurfaceLock(IOSurfaceRef s, unsigned opts, uint32_t *seed) {
    if (!opts) s->seed++;
    if (seed) *seed=s->seed;
    return 0;
}
static IOReturn IOSurfaceUnlock(IOSurfaceRef s, unsigned opts, uint32_t *seed) {
    if (seed) *seed=s->seed;
    return 0;
}
static void *IOSurfaceGetBaseAddressOfPlane(IOSurfaceRef s, uint32_t p) { return s->cpu; }
static size_t IOSurfaceGetBytesPerRowOfPlane(IOSurfaceRef s, uint32_t p) { return 4; }
static void *IOSurfaceGetBaseAddress(IOSurfaceRef s) { return s->cpu; }
static size_t IOSurfaceGetAllocSize(IOSurfaceRef s) { return sizeof s->cpu; }
static int nvmtl_vk_buffer_fill_from_surface(void *q, void *dst, const void *base, size_t bytes) {
    assert(bytes == 4); memcpy(dst, base, bytes); wholeUploads++; return 0;
}
// the imported-pages upload is unavailable here, so every upload takes the CPU copy this test checks
// the host-page fast paths (#399) report "not available", so the copies below take the plain path this test checks
static int nvmtl_vk_image_write_host(void *q, Image *img, void *base, size_t row, size_t bytes, void *cache,
    void *lease, nvmtl_vk_lease_retain_fn retain, nvmtl_vk_lease_release_fn release) { return -1; }
static int nvmtl_vk_image_read_host(void *q, Image *img, void *base, size_t row, size_t bytes, void *cache) { return -1; }
static uint32_t nvmtl_vram_gen(IOSurfaceRef s, NSUInteger p) { return 1; }
static int nvmtl_vram_tag_current(IOSurfaceRef s, NSUInteger p, unsigned seed) { return gpu_seed == seed; }
static void nvmtl_vram_tag_set(IOSurfaceRef s, NSUInteger p, unsigned seed) { gpu_seed=seed; }
static unsigned nvmtl_vram_tag_bump(IOSurfaceRef s, NSUInteger p, unsigned seed) { return 1; }
// the kernel's VRAM mark: dirtyAnswer for the blocking one (1 = refused, the copy path), tryAnswer for the try that never
// waits for the accelerator lock (1 = the lock was taken, -1 = refused); each call counted
static int dirtyAnswer=1, tryAnswer=-1; static unsigned blockingDirties, tries;
static int nvmtl_vk_surface_dirty(unsigned s, unsigned p) { dirtyPlane=p; blockingDirties++; return dirtyAnswer; }
static int nvmtl_vk_surface_dirty_try(unsigned s, unsigned p) { dirtyPlane=p; tries++; return tryAnswer; }
static int nvmtl_noseed(void) { return 0; }
// cross-process busy marks (PR #398): who wrote the surface last, and the waits for another process's write
static int busyForeign=0, busyWaits=0;
static int nvmtl_busy_foreign(IOSurfaceRef s, NSUInteger p) { (void)s; (void)p; return busyForeign; }
static void nvmtl_busy_wait(IOSurfaceRef s, NSUInteger p) { (void)s; busyPlane=p; busyWaits++; }
static void nvmtl_frame_dump(const char *tag, void *base, size_t row, unsigned w, unsigned h) {}
#define nvlog(...) ((void)0)
// helpers the capture path calls (NVMTLEventSync.m); WindowServer kernel marks are off here
static BOOL nvmtl_ws_kernel_waits(void){return NO;}
static void nvmtl_ws_mark(uint32_t sid,BOOL pending){(void)sid;(void)pending;}
static void nvtrace(const char *f,...){(void)f;}
static int nvmtl_vk_image_write(void *q, Image *i, void *base, size_t row) {
    memcpy(i->gpu,base,4); uploads++; return 0;
}
static int nvmtl_vk_image_read(void *q, Image *i, void *base, size_t row) {
    dispatch_semaphore_signal(entered);
    assert(!dispatch_semaphore_wait(release_read,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC)));
    memcpy(base,i->gpu,4); return 0;
}
@class NVMTLTexture;
@interface NVMTLCommandBuffer : NSObject { @public NSMutableSet *_surfOverwrites; }
@end
@implementation NVMTLCommandBuffer @end
static BOOL nvmtl_surface_pending_write(NVMTLCommandBuffer *cb, NVMTLTexture *t) { (void)cb; (void)t; return NO; }
@interface NVMTLTexture : NSObject {
@public NVMTLTexture *_parent; IOSurfaceRef _surf; BOOL _vramOn, _vramMulti, _surfSynced, _clientWritten; void *_hostPages;
    unsigned char _vramBuf[4];
    uint32_t _surfSeed, _surfGen, _readerStreak; NSUInteger _plane; Image _i; void *_q;
}
- (BOOL)nvmtlSurfaceCopyable;
- (void)nvmtlSurfaceIn;
- (void)nvmtlSurfaceInForRenderTargetLoad:(BOOL)readerSkipCandidate;
- (BOOL)nvmtlSurfaceOut;
- (BOOL)nvmtlSurfaceOutDirtied:(BOOL)dirtied;
- (BOOL)nvmtlSurfaceOutDirtied:(BOOL)dirtied copied:(BOOL)copied;
@end
@interface NVMTLSurfaceUploadLease : NSObject
- (instancetype)initWithSurface:(IOSurfaceRef)surface options:(uint32_t)options pages:(id)pages;
@end
static id nvmtl_surface_page_lease(NVMTLTexture *r, nvk_buffer *pages) { (void)r; (void)pages; return nil; }
static void *nvmtl_surface_upload_lease_retain(void *lease) { (void)lease; return NULL; }
static void nvmtl_surface_upload_lease_release(void *lease) { (void)lease; }
@implementation NVMTLSurfaceUploadLease
- (instancetype)initWithSurface:(IOSurfaceRef)surface options:(uint32_t)options pages:(id)pages {
    (void)surface; (void)options; (void)pages; return [super init];
}
@end
@implementation NVMTLTexture
- (BOOL)nvmtlSurfaceCopyable { return YES; }
'''
methods = methods.replace('static nvk_buffer *nvmtl_surface_host_pages(NVMTLTexture *r, void *base, size_t bytes);',
    'static nvk_buffer *nvmtl_surface_host_pages(NVMTLTexture *r, void *base, size_t bytes) { return r->_hostPages; }')
checks = r'''
@end
int main(int argc, char **argv) { @autoreleasepool {
    if (argc > 1 && !strncmp(argv[1], "reader-", 7)) {
        test_program = !strcmp(argv[1], "reader-app") ? "QQ" : "WindowServer";
        Surface rs = { .seed = !strcmp(argv[1], "reader-wrap") ? UINT32_MAX - 2u : 10u,
                       .planes = !strcmp(argv[1], "reader-multiplane") ? 2u : 1u };
        NVMTLTexture *rt = [NVMTLTexture new]; rt->_surf = &rs; rt->_vramOn = YES;
        rt->_i = (Image){ .w=1, .h=1, .bpp=4, .gpu={0,0,0,0} };
        busyForeign = 0; busyWaits = 0;
        [rt nvmtlSurfaceIn]; assert(uploads == 1 && rt->_surfSynced);
        const unsigned before = uploads;
        if (!strcmp(argv[1], "reader-foreign")) {
            for (unsigned i = 0; i < 4; i++) { rs.seed++; nvmtl_surface_in_for_render_target(nil, rt); }
            assert(rt->_readerStreak == 4 && uploads == before + 4);
            rs.seed++; memset(rs.cpu, 123, sizeof rs.cpu); busyForeign = 1;
            nvmtl_surface_in_for_render_target(nil, rt);
            assert(busyWaits == 1 && rt->_clientWritten);
            assert(uploads == before + 5 && !memcmp(rt->_i.gpu, rs.cpu, sizeof rs.cpu));
            puts("PASS: pending foreign writer is waited for and imported; reader heuristic cannot skip it");
            return 0;
        }
        for (unsigned i = 0; i < 6; i++) {
            rs.seed++;
            if (!strcmp(argv[1], "reader-cpu-on") || !strcmp(argv[1], "reader-cpu-off"))
                rs.cpu[i % sizeof rs.cpu] = (unsigned char)(rs.cpu[i % sizeof rs.cpu] + i + 1);
            nvmtl_surface_in_for_render_target(nil, rt);
            if (!strcmp(argv[1], "reader-cpu-on") && i >= 4) {
                assert(uploads == before + 4);
                assert(memcmp(rt->_i.gpu, rs.cpu, sizeof rs.cpu) != 0);
            } else if ((!strcmp(argv[1], "reader-on") || !strcmp(argv[1], "reader-wrap")) && i >= 4) {
                assert(uploads == before + 4);
                assert(!memcmp(rt->_i.gpu, rs.cpu, sizeof rs.cpu));
            } else {
                assert(uploads == before + i + 1);
                assert(!memcmp(rt->_i.gpu, rs.cpu, sizeof rs.cpu));
            }
        }
        if (!strcmp(argv[1], "reader-cpu-on"))
            puts("PASS: opt-in limitation reproduced: five changing CPU writes with seed +1 are indistinguishable and the fifth is skipped");
        else if (!strcmp(argv[1], "reader-cpu-off"))
            puts("PASS: default-off keeps importing every changing CPU write");
        else if (!strcmp(argv[1], "reader-app"))
            puts("PASS: explicit opt-in is ignored outside WindowServer");
        else if (!strcmp(argv[1], "reader-invalid"))
            puts("PASS: a value other than the exact opt-in 1 stays disabled");
        else if (!strcmp(argv[1], "reader-multiplane"))
            puts("PASS: multi-plane shared-VRAM surfaces never use the reader heuristic");
        else if (!strcmp(argv[1], "reader-wrap")) {
            assert(rs.seed == 3u);
            puts("PASS: reader streak handles uint32 seed wrap without overflow ambiguity");
        } else
            puts("PASS: repeated unchanged reader bumps skip after four observed advances");
        return 0;
    }
    Surface s={.seed=1};
    NVMTLTexture *t=[NVMTLTexture new]; t->_surf=&s; t->_i=(Image){.w=1,.h=1,.bpp=4,.gpu={0,128,255,255}};
    NVMTLTexture *alias=[NVMTLTexture new];
    if (argc > 1) { alias->_surf=&s; alias->_vramOn=YES; t->_vramOn=YES; }
    else alias->_parent=t;
    entered=dispatch_semaphore_create(0); release_read=dispatch_semaphore_create(0);
    dispatch_group_t group=dispatch_group_create();
    dispatch_queue_t queue=dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0);
    dispatch_group_async(group,queue,^{ @autoreleasepool { assert([t nvmtlSurfaceOut]); } });
    assert(!dispatch_semaphore_wait(entered,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC)));
    dispatch_semaphore_t importing=dispatch_semaphore_create(0);
    dispatch_group_async(group,queue,^{ @autoreleasepool {
        dispatch_semaphore_signal(importing); [alias nvmtlSurfaceIn];
    } });
    assert(!dispatch_semaphore_wait(importing,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC)));
    // Give an unsynchronized importer time to read the still-old CPU pixels.
    [NSThread sleepForTimeInterval:0.1];
    dispatch_semaphore_signal(release_read);
    assert(!dispatch_group_wait(group,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC)));
    const unsigned char orange[4]={0,128,255,255};
    assert(!memcmp(t->_i.gpu,orange,4)); assert(!memcmp(s.cpu,orange,4)); assert(uploads==0);
    // A later CPU edit must still be imported once publication has finished.
    s.seed++; memset(s.cpu,71,4); [alias nvmtlSurfaceIn];
    assert(uploads==1); assert(!memcmp((argc>1?alias:t)->_i.gpu,s.cpu,4));
    // Publication after the commit's VRAM mark (WindowServer, an NVAccel whose page-off waits) runs on the thread that
    // releases the pending marks, so it never waits for the accelerator lock: busy, the commit's mark stands and nothing
    // is copied (a copy would block in the read stub and fail); free, the surface is marked again.
    Surface ws={.seed=9}; NVMTLTexture *w=[NVMTLTexture new]; w->_surf=&ws; w->_vramOn=YES; w->_i=(Image){.w=1,.h=1,.bpp=4};
    blockingDirties=0; tries=0; tryAnswer=-1;
    assert([w nvmtlSurfaceOutDirtied:YES] && tries==1 && !blockingDirties && w->_surfSynced && w->_surfSeed==9);
    tryAnswer=0;
    assert([w nvmtlSurfaceOutDirtied:YES] && tries==2 && !blockingDirties);
    // the lock was taken: this thread goes on, and the re-mark is retried off it, again only as tries (a mark that
    // waited for the lock could land after a newer CPU write), until one takes; a page-off that slipped in between the
    // commit's marks must not leave its copy current
    tryAnswer=1;
    assert([w nvmtlSurfaceOutDirtied:YES] && tries==3);
    for (int i=0; i<200 && tries<6; i++) [NSThread sleepForTimeInterval:0.002];
    assert(tries>=6 && !blockingDirties);
    tryAnswer=0; const unsigned settledTries=tries;
    for (int i=0; i<200 && tries==settledTries; i++) [NSThread sleepForTimeInterval:0.002];
    [NSThread sleepForTimeInterval:0.02];
    const unsigned after=tries; [NSThread sleepForTimeInterval:0.02];
    assert(after>settledTries && tries==after && !blockingDirties);   // it took, and the retries stopped
    tryAnswer=-1; tries=2;
    // any other publication asks with the blocking mark, as before
    dirtyAnswer=0;
    assert([w nvmtlSurfaceOut] && tries==2 && blockingDirties==1);
    dirtyAnswer=1;
    // a surface another process is still drawing (PR #398): this process waits for that write before it imports, and
    // remembers the surface as client-written so every later import waits too; its own writes never wait
    Surface cs={.seed=3}; NVMTLTexture *c=[NVMTLTexture new]; c->_surf=&cs; c->_i=(Image){.w=1,.h=1,.bpp=4};
    busyWaits=0; busyForeign=0; [c nvmtlSurfaceIn];
    assert(busyWaits==0 && !c->_clientWritten);
    cs.seed++; busyForeign=1; [c nvmtlSurfaceIn];
    assert(busyWaits==1 && c->_clientWritten);
    busyForeign=0; cs.seed++; [c nvmtlSurfaceIn];
    assert(busyWaits==2);
    // A completed capture copy keeps the CPU pixels, advances the seed and never calls either GPU readback path.
    // The readback stub would block waiting for release_read, which is deliberately not signalled again here.
    Surface captured={.seed=10,.cpu={17,18,19,20}};
    NVMTLTexture *capture=[NVMTLTexture new];capture->_surf=&captured;capture->_i=(Image){.w=1,.h=1,.bpp=4,.gpu={0,0,0,0}};
    assert([capture nvmtlSurfaceOutDirtied:NO copied:YES]);
    assert(capture->_surfSynced && capture->_surfSeed==11 && captured.cpu[0]==17 && captured.cpu[3]==20);
    // Two aliases of the same full allocation synchronize and publish through plane zero.
    NVMTLTexture *mp0=[NVMTLTexture new], *mp1=[NVMTLTexture new];
    Surface ms={.seed=99,.cpu={3,5,7,9}};
    mp0->_surf=mp1->_surf=&ms; mp0->_vramOn=mp1->_vramOn=YES;
    mp0->_vramMulti=mp1->_vramMulti=YES; mp1->_plane=1; mp1->_clientWritten=YES;
    mp0->_i=mp1->_i=(Image){.w=1,.h=1,.bpp=4};
    assert(nvmtl_surface_sync(mp0)==nvmtl_surface_sync(mp1));
    [mp1 nvmtlSurfaceIn];
    assert(wholeUploads==1 && busyPlane==0 && !memcmp(mp1->_vramBuf,ms.cpu,4));
    dirtyAnswer=0; assert([mp1 nvmtlSurfaceOut]); assert(dirtyPlane==0);
    puts("PASS: overlapping publication preserves GPU pixels; later CPU edits import; a publication after the commit's "
         "VRAM mark never waits for the accelerator lock and retries a busy re-mark off its thread; another process's "
         "write is waited for before import");
} return 0; }
'''
with tempfile.TemporaryDirectory() as directory:
    path = pathlib.Path(directory)
    (path / 'test.m').write_text(fixture + plane_macro + methods + checks)
    subprocess.run(['clang', '-fobjc-arc', '-framework', 'Foundation', '-I', str(root / 'plugin'),
                    str(path / 'test.m'), '-o', str(path / 'test')], check=True)
    import os
    env = dict(os.environ)
    env.pop('NVMTL_SURFIN_READER_SKIP', None)
    subprocess.run([str(path / 'test')], check=True, env=env)
    subprocess.run([str(path / 'test'), 'shared'], check=True, env=env)
    env['NVMTL_SURFIN_READER_SKIP'] = '1'
    for case in ('reader-on', 'reader-wrap', 'reader-app', 'reader-multiplane', 'reader-foreign'):
        subprocess.run([str(path / 'test'), case], check=True, env=env)
    env['NVMTL_SURFIN_READER_SKIP'] = 'yes'
    subprocess.run([str(path / 'test'), 'reader-invalid'], check=True, env=env)
    env.pop('NVMTL_SURFIN_READER_SKIP', None)
    subprocess.run([str(path / 'test'), 'reader-cpu-off'], check=True, env=env)
    env['NVMTL_SURFIN_READER_SKIP'] = '1'
    subprocess.run([str(path / 'test'), 'reader-cpu-on'], check=True, env=env)
