/* Verify native cross-device events and notification-driven NVMTL waits.
 * clang -fobjc-arc tools/test_shared_event_interop_gpu.m -framework Foundation \
 *   -framework Metal -o shared-event-interop
 * Run on an NVMTL + Intel Metal system. Exit 77 means the second device is absent.
 */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static void fillBuffer(id<MTLCommandBuffer> commands, id<MTLBuffer> buffer, uint8_t byte)
{
    id<MTLBlitCommandEncoder> blit = [commands blitCommandEncoder];
    [blit fillBuffer:buffer range:NSMakeRange(0, buffer.length) value:byte];
    [blit endEncoding];
}

static BOOL bufferContains(id<MTLBuffer> buffer, uint8_t byte)
{
    const uint8_t *contents = buffer.contents;
    for (NSUInteger offset = 0; offset < buffer.length; offset++) {
        if (contents[offset] != byte) return NO;
    }
    return YES;
}

static BOOL testRoundTrip(id<MTLCommandQueue> first, id<MTLCommandQueue> second)
{
    id<MTLSharedEvent> event = [first.device newSharedEvent];
    MTLSharedEventHandle *handle = [event newSharedEventHandle];
    if (!handle) {
        fprintf(stderr, "%s did not export a native shared event\n", first.device.name.UTF8String);
        return NO;
    }
    id<MTLSharedEvent> imported = [second.device newSharedEventWithHandle:handle];
    if (!imported) {
        fprintf(stderr, "%s did not import the shared event\n", second.device.name.UTF8String);
        return NO;
    }
    id<MTLBuffer> a = [first.device newBufferWithLength:4096 options:MTLResourceStorageModeShared];
    id<MTLBuffer> b = [second.device newBufferWithLength:4096 options:MTLResourceStorageModeShared];
    id<MTLCommandBuffer> produce = [first commandBuffer];
    id<MTLCommandBuffer> consume = [second commandBuffer];
    id<MTLCommandBuffer> finish = [first commandBuffer];
    if (!a.contents || !b.contents || !produce || !consume || !finish) return NO;
    memset(a.contents, 0, a.length);
    memset(b.contents, 0, b.length);

    [produce encodeWaitForEvent:event value:1];
    fillBuffer(produce, a, 0xC3);
    [produce encodeSignalEvent:event value:2];

    [consume encodeWaitForEvent:imported value:2];
    fillBuffer(consume, b, 0xD4);
    [consume encodeSignalEvent:imported value:3];

    [finish encodeWaitForEvent:event value:3];
    [finish encodeSignalEvent:event value:4];

    // No new submits after the CPU signal. A blocked NVMTL worker must wake
    // when Metal signals the event, even when the producer uses another handle.
    [consume commit];
    [produce commit];
    [finish commit];
    usleep(20000);
    BOOL held = event.signaledValue == 0 && imported.signaledValue == 0 &&
        bufferContains(a, 0) && bufferContains(b, 0);
    event.signaledValue = 1;

    for (NSUInteger retry = 0; retry < 2000; retry++) {
        if (produce.status >= MTLCommandBufferStatusCompleted &&
            consume.status >= MTLCommandBufferStatusCompleted &&
            finish.status >= MTLCommandBufferStatusCompleted) break;
        usleep(1000);
    }

    BOOL completed = produce.status == MTLCommandBufferStatusCompleted &&
        consume.status == MTLCommandBufferStatusCompleted && finish.status == MTLCommandBufferStatusCompleted;
    if (held && completed && event.signaledValue == 4 && imported.signaledValue == 4 &&
        bufferContains(a, 0xC3) && bufferContains(b, 0xD4)) return YES;
    fprintf(stderr, "%s -> %s: held=%d statuses=%lu,%lu,%lu value=%llu/%llu\n",
        first.device.name.UTF8String, second.device.name.UTF8String, held,
        (unsigned long)produce.status, (unsigned long)consume.status, (unsigned long)finish.status,
        (unsigned long long)event.signaledValue, (unsigned long long)imported.signaledValue);
    return NO;
}

int main(void)
{
    @autoreleasepool {
        id<MTLDevice> nvmtl = nil, intel = nil;
        for (id<MTLDevice> device in MTLCopyAllDevices()) {
            if ([NSStringFromClass([(id)device class]) isEqualToString:@"NVMTLDevice"]) nvmtl = device;
            else if ([device.name containsString:@"Intel"]) intel = device;
        }
        if (!nvmtl || !intel) {
            fprintf(stderr, "Requires both NVMTL and an Intel Metal device\n");
            return 77;
        }
        id<MTLCommandQueue> nvmtlQueue = [nvmtl newCommandQueue];
        id<MTLCommandQueue> intelQueue = [intel newCommandQueue];
        if (!nvmtlQueue || !intelQueue) return 1;
        @try {
            for (NSUInteger iteration = 0; iteration < 12; iteration++) @autoreleasepool {
                if (!testRoundTrip(nvmtlQueue, intelQueue) || !testRoundTrip(intelQueue, nvmtlQueue)) return 1;
            }
        } @catch (NSException *exception) {
            fprintf(stderr, "%s: %s\n", exception.name.UTF8String, exception.reason.UTF8String);
            return 1;
        }
        puts("PASS: 24 cross-GPU shared-event round trips");
    }
    return 0;
}
