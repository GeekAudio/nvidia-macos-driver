// Compile/encode a single public MPSGraph depthwise operation with a local debug plugin.
// No execution mode exists. MPSGraph may commit even from its encode API, so this
// process replaces the local plugin's submission methods with fail-closed guards.
// Build: xcrun clang -fobjc-arc -O1 tools/mps_depthwise_probe.m -o /tmp/mps_depthwise_probe \
//   -framework Foundation -framework Metal -framework MetalPerformanceShaders \
//   -framework MetalPerformanceShadersGraph -framework IOKit
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <MetalPerformanceShaders/MetalPerformanceShaders.h>
#import <MetalPerformanceShadersGraph/MetalPerformanceShadersGraph.h>
#import <IOKit/IOKitLib.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void emit(NSDictionary *value) {
    NSData *json = [NSJSONSerialization dataWithJSONObject:value options:NSJSONWritingSortedKeys error:NULL];
    if (json) { fwrite(json.bytes, 1, json.length, stdout); fputc('\n', stdout); fflush(stdout); }
}

static void fail(NSString *message, int code) {
    emit(@{@"event": @"error", @"detail": message, @"gpu_submitted": @NO});
    // Framework teardown can wait for command buffers we deliberately did not commit.
    _Exit(code);
}

static void blocked(id self, SEL selector) {
    (void)self;
    fprintf(stderr, "ENCODE_ONLY_GUARD: blocked %s before GPU submission or wait\n", sel_getName(selector));
    fflush(stderr);
    _Exit(20);
}

static NSString *canonical(NSString *path) {
    return path.stringByStandardizingPath.stringByResolvingSymlinksInPath;
}

static void requireImage(id object, NSString *label, NSString *path) {
    const char *image = object ? class_getImageName(object_getClass(object)) : NULL;
    if (!image || ![canonical(@(image)) isEqualToString:canonical(path)])
        fail([NSString stringWithFormat:@"%@ did not come from the requested plugin", label], 3);
}

static NSDictionary<NSString *, NSString *> *captureEnvironment;
static void restoreCapture(void) {
    for (NSString *key in captureEnvironment)
        setenv(key.UTF8String, captureEnvironment[key].fileSystemRepresentation, 1);
}

static id<MTLDevice> localDevice(NSString *path, NSString *prefix) {
    NSString *deviceClass = [prefix stringByAppendingString:@"NVMTLDevice"];
    NSString *commandClass = [prefix stringByAppendingString:@"NVMTLCommandBuffer"];
    if (NSClassFromString(deviceClass))
        fail(@"The requested local device class was already loaded", 3);
    void *handle = dlopen(path.fileSystemRepresentation, RTLD_NOW | RTLD_LOCAL);
    if (!handle) fail([NSString stringWithFormat:@"dlopen failed: %s", dlerror()], 3);
    // The plugin also initializes dummy textures with a raw Vulkan queue submit.
    // Objective-C commit guards cannot catch it, so require the debug plugin's
    // lower-level guard before any device or pipeline initialization is allowed.
    int (*encodeOnly)(void) = (int (*)(void))dlsym(handle, "nvmtl_vk_encode_only_active");
    if (!encodeOnly || encodeOnly() != 1)
        fail(@"Plugin lacks the active NVMTL_ENCODE_ONLY Vulkan submission guard", 3);
    for (NSString *name in @[deviceClass, commandClass]) {
        Class cls = NSClassFromString(name);
        const char *image = cls ? class_getImageName(cls) : NULL;
        if (!image || ![canonical(@(image)) isEqualToString:canonical(path)])
            fail([NSString stringWithFormat:@"%@ did not come from the requested plugin", name], 3);
    }
    // The SDK explicitly warns that encodeToCommandBuffer may commitAndContinue.
    // Install these guards BEFORE creating a Metal device or any graph objects.
    Class commands = NSClassFromString(commandClass);
    for (NSString *name in @[@"commit", @"nvmtlSubmitWithEvents", @"waitUntilCompleted", @"waitUntilScheduled"]) {
        Method method = class_getInstanceMethod(commands, NSSelectorFromString(name));
        if (!method) fail([NSString stringWithFormat:@"Required no-submit guard %@ is unavailable", name], 3);
        method_setImplementation(method, (IMP)blocked);
    }
    io_service_t accelerator = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("NVAccel"));
    if (!accelerator) fail(@"No NVAccel accelerator was found", 3);
    id<MTLDevice> device = ((id (*)(id, SEL, mach_port_t))objc_msgSend)(
        [NSClassFromString(deviceClass) alloc], NSSelectorFromString(@"initWithAcceleratorPort:"), accelerator);
    IOObjectRelease(accelerator);
    if (!device) fail(@"The local NVMTLDevice initializer returned nil", 3);
    requireImage(device, @"device", path);
    return device;
}

static NSUInteger number(NSDictionary *args, NSString *name, NSUInteger fallback, NSUInteger limit) {
    NSString *value = args[name];
    if (!value) return fallback;
    NSScanner *scanner = [NSScanner scannerWithString:value]; unsigned long long parsed = 0;
    if (![scanner scanUnsignedLongLong:&parsed] || !scanner.isAtEnd || !parsed || parsed > limit)
        fail([NSString stringWithFormat:@"Invalid --%@ (expected 1..%lu)", name, (unsigned long)limit], 2);
    return (NSUInteger)parsed;
}

static void captureFolder(NSString *output, NSString *key, NSString *leaf) {
    NSString *path = [output stringByAppendingPathComponent:leaf];
    NSError *error = nil;
    if (![NSFileManager.defaultManager createDirectoryAtPath:path withIntermediateDirectories:YES attributes:nil error:&error])
        fail(error.description, 2);
    setenv(key.UTF8String, path.fileSystemRepresentation, 1);
}

int main(int argc, const char **argv) { @autoreleasepool {
    NSMutableDictionary<NSString *, NSString *> *args = [NSMutableDictionary new];
    NSSet *known = [NSSet setWithArray:@[@"plugin", @"output", @"stage", @"rank", @"width", @"height", @"depth", @"channels", @"dtype", @"layout", @"epilogue", @"class-prefix"]];
    for (int i = 1; i < argc; i += 2) {
        if (!strcmp(argv[i], "--help")) {
            puts("Usage: mps_depthwise_probe --plugin /absolute/NVMTLDriver --output /absolute/capture\n"
                 "  [--stage compile|encode] [--rank 2|3] [--width 64] [--height 64]\n"
                 "  [--depth 1] [--channels 32] [--dtype f16|f32] [--layout nhwc|nchw]\n"
                 "  [--epilogue none|bias|relu|bias-relu]\n"
                 "  [--class-prefix QMP] (must match build_mps_debug_plugin.py)\n"
                 "Defaults: compile, rank=3, depth=1, f16, nhwc, bias-relu. No GPU execution mode.\n"
                 "Exit 20 means MPS attempted a submit/wait and the encode-only guard stopped it.");
            return 0;
        }
        if (i + 1 >= argc || strncmp(argv[i], "--", 2)) fail(@"Expected --name value pairs", 2);
        NSString *key = @(argv[i] + 2);
        if (![known containsObject:key]) fail([@"Unknown argument --" stringByAppendingString:key], 2);
        args[key] = @(argv[i + 1]);
    }
    if (![args[@"plugin"] isAbsolutePath] || ![args[@"output"] isAbsolutePath])
        fail(@"--plugin and --output must both be absolute paths", 2);
    NSString *stage = args[@"stage"] ?: @"compile", *dtype = args[@"dtype"] ?: @"f16";
    NSString *layout = args[@"layout"] ?: @"nhwc", *epilogue = args[@"epilogue"] ?: @"bias-relu";
    NSString *prefix = args[@"class-prefix"] ?: @"QMP";
    if (![@[@"compile", @"encode"] containsObject:stage] || ![@[@"f16", @"f32"] containsObject:dtype] ||
        ![@[@"nhwc", @"nchw"] containsObject:layout] || ![@[@"none", @"bias", @"relu", @"bias-relu"] containsObject:epilogue])
        fail(@"Invalid stage, dtype, layout, or epilogue", 2);
    NSUInteger rank = number(args, @"rank", 3, 3);
    if (rank != 2 && rank != 3) fail(@"--rank must be 2 or 3", 2);
    NSUInteger w = number(args, @"width", 64, 2048), h = number(args, @"height", 64, 2048);
    NSUInteger d = number(args, @"depth", 1, 64), c = number(args, @"channels", 32, 1024);
    if ((rank == 2 && d != 1) || (uint64_t)w * h * d * c > (64u << 20))
        fail(@"Shape exceeds the probe limit (or rank=2 was given depth != 1)", 2);
    NSString *output = canonical(args[@"output"]);
    for (NSArray *entry in @[@[@"NVMTL_AIRCACHE", @"aircache"], @[@"MESA_SHADER_CACHE_DIR", @"mesa-cache"],
        @[@"NVMTL_LINK_DUMP", @"linked"], @[@"NVMTL_FC_DUMP_ALL", @"fc-all"],
        @[@"NVMTL_FC_FAIL_DUMP", @"fc-fail"], @[@"NVMTL_DUMP_FAILED_DIR", @"failed"]])
        captureFolder(output, entry[0], entry[1]);
    setenv("NVMTL_LOG_PATH", [output stringByAppendingPathComponent:@"nvmtl.log"].fileSystemRepresentation, 1);
    setenv("NVMTL_ENCODE_ONLY", "1", 1);
    setenv("NVMTL_DIRECT_SASS", "0", 1); // Ask the translator for the actual MPS AIR, even if an artifact exists.
    setenv("NVMTL_LINKCACHE_OFF", "1", 1);
    setenv("NVMTL_FCCACHE_OFF", "1", 1);
    setenv("NVMTL_ZPROBE", "1", 1);
    NSMutableDictionary *saved = [NSMutableDictionary new];
    for (NSString *key in @[@"NVMTL_AIRCACHE", @"MESA_SHADER_CACHE_DIR", @"NVMTL_LINK_DUMP", @"NVMTL_FC_DUMP_ALL",
        @"NVMTL_FC_FAIL_DUMP", @"NVMTL_DUMP_FAILED_DIR", @"NVMTL_LOG_PATH", @"NVMTL_ENCODE_ONLY",
        @"NVMTL_DIRECT_SASS", @"NVMTL_LINKCACHE_OFF", @"NVMTL_FCCACHE_OFF", @"NVMTL_ZPROBE"])
        saved[key] = @(getenv(key.UTF8String));
    captureEnvironment = [saved copy];
    emit(@{@"event": @"start", @"stage": stage, @"rank": @(rank), @"dtype": dtype, @"layout": layout,
        @"epilogue": epilogue, @"shape": @[@(d), @(h), @(w), @(c)], @"output": output,
        @"plugin": canonical(args[@"plugin"]), @"gpu_submission_guard": @YES});

    NSString *plugin = canonical(args[@"plugin"]);
    id<MTLDevice> device = localDevice(plugin, prefix);
    restoreCapture();
    emit(@{@"event": @"device", @"name": device.name ?: @"", @"class": NSStringFromClass([(id)device class])});
    MPSGraphDevice *graphDevice = [MPSGraphDevice deviceWithMTLDevice:device];
    MPSGraph *graph = [MPSGraph new]; graph.options = MPSGraphOptionsNone;
    restoreCapture();
    if (graphDevice.metalDevice != device) fail(@"MPSGraphDevice replaced the local Metal device", 3);
    BOOL channelsLast = [layout isEqualToString:@"nhwc"];
    MPSDataType type = [dtype isEqualToString:@"f16"] ? MPSDataTypeFloat16 : MPSDataTypeFloat32;
    NSArray *sourceShape, *weightShape, *biasShape;
    if (rank == 3) {
        sourceShape = channelsLast ? @[@1, @(d), @(h), @(w), @(c)] : @[@1, @(c), @(d), @(h), @(w)];
        weightShape = channelsLast ? @[@1, @3, @3, @(c)] : @[@(c), @1, @3, @3];
        biasShape = channelsLast ? @[@(c)] : @[@1, @(c), @1, @1, @1];
    } else {
        sourceShape = channelsLast ? @[@1, @(h), @(w), @(c)] : @[@1, @(c), @(h), @(w)];
        weightShape = @[@3, @3, @(c), @1]; // HWIO: O is the channel multiplier.
        biasShape = channelsLast ? @[@(c)] : @[@1, @(c), @1, @1];
    }
    MPSGraphTensor *source = [graph placeholderWithShape:sourceShape dataType:type name:@"source"];
    MPSGraphTensor *weights = [graph placeholderWithShape:weightShape dataType:type name:@"weights"];
    MPSGraphTensor *result = nil;
    if (rank == 3) {
        MPSGraphDepthwiseConvolution3DOpDescriptor *desc = [MPSGraphDepthwiseConvolution3DOpDescriptor descriptorWithPaddingStyle:MPSGraphPaddingStyleExplicit];
        desc.channelDimensionIndex = channelsLast ? -1 : -4;
        desc.paddingValues = @[@0, @0, @1, @1, @1, @1];
        result = [graph depthwiseConvolution3DWithSourceTensor:source weightsTensor:weights descriptor:desc name:@"depthwise3x3"];
    } else {
        MPSGraphDepthwiseConvolution2DOpDescriptor *desc = [MPSGraphDepthwiseConvolution2DOpDescriptor
            descriptorWithDataLayout:channelsLast ? MPSGraphTensorNamedDataLayoutNHWC : MPSGraphTensorNamedDataLayoutNCHW
            weightsLayout:MPSGraphTensorNamedDataLayoutHWIO];
        [desc setExplicitPaddingWithPaddingLeft:1 paddingRight:1 paddingTop:1 paddingBottom:1];
        result = [graph depthwiseConvolution2DWithSourceTensor:source weightsTensor:weights descriptor:desc name:@"depthwise3x3"];
    }
    NSMutableDictionary<MPSGraphTensor *, MPSGraphShapedType *> *types = [NSMutableDictionary new];
    types[source] = [[MPSGraphShapedType alloc] initWithShape:sourceShape dataType:type];
    types[weights] = [[MPSGraphShapedType alloc] initWithShape:weightShape dataType:type];
    if ([epilogue containsString:@"bias"]) {
        MPSGraphTensor *bias = [graph placeholderWithShape:biasShape dataType:type name:@"bias"];
        types[bias] = [[MPSGraphShapedType alloc] initWithShape:biasShape dataType:type];
        result = [graph additionWithPrimaryTensor:result secondaryTensor:bias name:@"bias-add"];
    }
    if ([epilogue containsString:@"relu"]) result = [graph reLUWithTensor:result name:@"relu"];
    MPSGraphCompilationDescriptor *compilation = [MPSGraphCompilationDescriptor new];
    compilation.waitForCompilationCompletion = YES;
    restoreCapture();
    emit(@{@"event": @"compile", @"source_shape": sourceShape, @"weight_shape": weightShape});
    MPSGraphExecutable *executable = [graph compileWithDevice:graphDevice feeds:types targetTensors:@[result]
        targetOperations:nil compilationDescriptor:compilation];
    if (!executable) fail(@"Graph compilation returned nil", 4);
    restoreCapture();
    requireImage(device, @"device after graph compilation", plugin);
    executable.options = MPSGraphOptionsNone;
    emit(@{@"event": @"compiled"});
    if ([stage isEqualToString:@"encode"]) {
        NSMutableArray<MPSGraphTensorData *> *inputs = [NSMutableArray new];
        for (MPSGraphTensor *tensor in executable.feedTensors) {
            MPSGraphShapedType *feed = types[tensor];
            if (!feed) fail(@"Executable requested an unknown feed", 4);
            NSUInteger count = 1; for (NSNumber *extent in feed.shape) count *= extent.unsignedIntegerValue;
            id<MTLBuffer> buffer = [device newBufferWithLength:count * (type == MPSDataTypeFloat16 ? 2 : 4) options:MTLResourceStorageModeShared];
            if (!buffer || !buffer.contents) fail(@"Unable to create a host-visible input buffer", 4);
            requireImage(buffer, @"feed buffer", plugin);
            if (buffer.device != device) fail(@"Input buffer belongs to another Metal device", 3);
            memset(buffer.contents, 0, buffer.length);
            MPSGraphTensorData *data = [[MPSGraphTensorData alloc] initWithMTLBuffer:buffer shape:feed.shape dataType:type];
            if (!data) fail(@"Unable to wrap the input buffer", 4);
            if (data.device.metalDevice != device) fail(@"TensorData replaced the local Metal device", 3);
            [inputs addObject:data];
        }
        restoreCapture();
        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLCommandBuffer> raw = [queue commandBuffer];
        if (!raw) fail(@"Unable to create the unsubmitted command buffer", 4);
        requireImage(queue, @"command queue", plugin);
        requireImage(raw, @"command buffer", plugin);
        if (queue.device != device || raw.device != device) fail(@"Queue/buffer belongs to another Metal device", 3);
        MPSCommandBuffer *commands = [MPSCommandBuffer commandBufferWithCommandBuffer:raw];
        MPSGraphExecutableExecutionDescriptor *execution = [MPSGraphExecutableExecutionDescriptor new];
        execution.waitUntilCompleted = NO;
        restoreCapture();
        requireImage(commands.rootCommandBuffer, @"MPS root command buffer", plugin);
        emit(@{@"event": @"encode"});
        NSArray *results = [executable encodeToCommandBuffer:commands inputsArray:inputs resultsArray:nil executionDescriptor:execution];
        emit(@{@"event": @"encoded", @"results": @(results.count), @"command_buffer_status": @(raw.status)});
        if (raw.status >= MTLCommandBufferStatusCommitted) fail(@"Unexpected committed command-buffer status", 5);
    }
    typedef void (*Counts)(uint64_t *, uint64_t *, uint64_t *);
    // class_getImageName already proved that this dlopen handle is the requested plugin.
    void *handle = dlopen(canonical(args[@"plugin"]).fileSystemRepresentation, RTLD_NOW | RTLD_LOCAL);
    Counts counts = (Counts)dlsym(handle, "nvmtl_vk_cmd_counts");
    if (!counts) fail(@"Plugin does not expose submission counters", 3);
    uint64_t begun = 0, submitted = 0, abandoned = 0; counts(&begun, &submitted, &abandoned);
    emit(@{@"event": @"complete", @"command_buffers_begun": @(begun), @"command_buffers_submitted": @(submitted),
        @"command_buffers_abandoned": @(abandoned), @"gpu_submitted": @(submitted != 0)});
    // Skip framework teardown; the OS discards the process's unsubmitted buffers.
    _Exit(submitted ? 5 : 0);
}}
