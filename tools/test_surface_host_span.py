#!/usr/bin/env python3
"""Check production host-import spans against allocation ends and overflow."""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools/rotation'))
from test_support import method

source = (root / 'plugin/nvmtl_vk.c').read_text()
body = method(source, 'static int nvmtl_host_page_span(')
pair = method(source, 'int nvmtl_vk_image_write_host_pair(')
owner_check = method((root / 'plugin/NVMTLObjects.m').read_text(), 'static bool nvmtl_surface_host_importable(')
fixture = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdbool.h>
static size_t g_host_align=4096;
typedef struct { void *base; } Surface;
typedef struct { Surface *_surf; } NVMTLTexture;
static size_t nvmtl_vk_host_import_align(void) { return g_host_align; }
static void *IOSurfaceGetBaseAddress(Surface *s) { return s->base; }
@@OWNER@@
@@BODY@@
typedef struct { unsigned bpp, samples, bw, bh, mips, layers, w, h; } nvk_image;
typedef struct { int begun; } nvk_cmdbuf;
typedef int nvk_buffer;
typedef int nvk_queue;
typedef struct { nvk_image *image; void *plane; size_t row_bytes; } nvmtl_host_plane;
static unsigned imports, begins, copies, waits, releases;
static int refuse_import, refuse_begin, refuse_wait;
static nvk_buffer *nvmtl_host_pages(nvk_buffer *cache, void *base, size_t bytes, nvk_buffer *tmp) {
    assert(base == (void *)0x10000 && bytes == 8192);
    imports++;
    return refuse_import ? NULL : cache;
}
static int nvmtl_vk_cmd_begin(nvk_queue *q, nvk_cmdbuf *cmd) {
    begins++;
    cmd->begun = !refuse_begin;
    return refuse_begin ? -1 : 0;
}
static void nvmtl_record_host_upload(nvk_cmdbuf *cmd, nvk_image *image, const nvk_buffer *host,
                                    size_t offset, uint32_t row_length) {
    assert(cmd->begun && *host == 7 && image->bpp == copies + 1);
    assert(offset == (copies ? 4096 : 0) && row_length == (copies ? 64 : 128));
    copies++;
}
static int nvmtl_vk_submit_wait_at(nvk_cmdbuf *cmd, const char *who) {
    assert(cmd->begun && copies == 2);
    waits++;
    return refuse_wait ? -1 : 0;
}
static void nvmtl_host_pages_done(nvk_buffer *cache, nvk_buffer *host) {
    assert(cache == host);
    releases++;
}
@@PAIR@@
static int span(uintptr_t address, size_t bytes, size_t bpp, size_t row, uint32_t w, uint32_t h) {
    void *base=(void *)address; size_t size=bytes, offset=SIZE_MAX;
    int result=nvmtl_host_page_span(&base,&size,bpp,row,w,h,&offset);
    if (!result) {
        assert((uintptr_t)base+offset==address && !((uintptr_t)base%g_host_align) && !(size%g_host_align));
        assert((uintptr_t)base+size<=address+bytes);
        assert(offset+(size_t)(h-1)*row+(size_t)w*bpp<=size);
    } else { assert(base==(void *)address && size==bytes && offset==SIZE_MAX); }
    return result;
}
int main(void) {
    Surface surface={(void *)0x10000}; NVMTLTexture texture={&surface};
    assert(nvmtl_surface_host_importable(&texture,(void *)0x10040));
    assert(!nvmtl_surface_host_importable(&texture,(void *)0xffff));
    g_host_align=16384; surface.base=(void *)0x11000;
    assert(!nvmtl_surface_host_importable(&texture,(void *)0x11040));
    surface.base=(void *)0x10000;
    assert(nvmtl_surface_host_importable(&texture,(void *)0x11040));
    surface.base=NULL; assert(!nvmtl_surface_host_importable(&texture,(void *)0x10040));
    g_host_align=4096;
    // Header-offset NV12/P010 planes and ordinary packed BGRA, with padding.
    for (size_t bpp=1;bpp<=4;bpp*=2) for (size_t at=0;at<4096;at+=bpp) {
        assert(!span(0x10000+at,65536-at,bpp,256,64,16));
        assert(span(0x10000+at,((16-1)*256+64*bpp)-1,bpp,256,64,16));
    }
    assert(!span(0x10040,8192-64,2,256,128,16));
    assert(span(0x10041,8192-65,2,256,128,16)); // offset is not a complete texel
    assert(span(0x10000,4095,4,256,64,16)); // no rounding into the next page
    assert(span(0x10000,4096,4,257,64,16));
    assert(span(0x10000,4096,4,128,64,16));
    assert(span(0x10000,4096,4,256,64,0));
    assert(span(0x10000,4096,4,256,0,16));
    assert(span(0,4096,4,256,64,16));
    assert(span(0x10040,SIZE_MAX,4,256,64,16));
    assert(span(UINTPTR_MAX-4095,8192,4,256,64,16));
    assert(span(0x10000,65536,4,SIZE_MAX-3,1,3));
    assert(span(0x10000,65536,1,(size_t)UINT32_MAX+1,1,1));
    g_host_align=0; assert(span(0x10000,65536,4,256,64,16));
    g_host_align=3000; assert(span(0x10000,65536,4,256,64,16));
    g_host_align=4096;
    nvk_image y = {1,1,1,1,1,1,120,30}, uv = {2,1,1,1,1,1,60,15};
    nvmtl_host_plane planes[2] = {{&y, (void *)0x10000, 128}, {&uv, (void *)0x11000, 128}};
    nvk_buffer cache = 7;
    assert(nvmtl_vk_image_write_host_pair(NULL, planes, (void *)0x10000, 8191, &cache));
    assert(nvmtl_vk_image_write_host_pair(NULL, planes, (void *)0x10004, 8192, &cache));
    planes[1].plane = (void *)0x11002;
    assert(nvmtl_vk_image_write_host_pair(NULL, planes, (void *)0x10000, 8192, &cache));
    planes[1].plane = (void *)0x11000;
    assert(!imports && !begins && !copies && !waits);
    refuse_import = 1;
    assert(nvmtl_vk_image_write_host_pair(NULL, planes, (void *)0x10000, 8192, &cache));
    assert(imports == 1 && !begins && !releases);
    refuse_import = 0; refuse_begin = 1;
    assert(nvmtl_vk_image_write_host_pair(NULL, planes, (void *)0x10000, 8192, &cache));
    assert(imports == 2 && begins == 1 && !copies && !waits && releases == 1);
    refuse_begin = 0; refuse_wait = 1;
    assert(nvmtl_vk_image_write_host_pair(NULL, planes, (void *)0x10000, 8192, &cache));
    assert(copies == 2 && waits == 1 && releases == 2);
    refuse_wait = 0; copies = 0;
    assert(!nvmtl_vk_image_write_host_pair(NULL, planes, (void *)0x10000, 8192, &cache));
    assert(imports == 4 && begins == 3 && copies == 2 && waits == 2 && releases == 3);
    puts("PASS: host-import allocation bounds and paired NV12 copy with one import and one fence");
}
'''
with tempfile.TemporaryDirectory(prefix='nvmtl-host-span-') as tmp:
    tmp = Path(tmp)
    (tmp / 'test.c').write_text(fixture.replace('@@BODY@@', body).replace('@@OWNER@@', owner_check).replace('@@PAIR@@', pair))
    subprocess.run(['xcrun', 'clang', '-O1', '-g', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', str(tmp / 'test.c'), '-o', str(tmp / 'test')], check=True)
    subprocess.run([str(tmp / 'test')], check=True)
