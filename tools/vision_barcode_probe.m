// A standalone Vision barcode probe. Generated images contain no third-party data.
// Run with run_vision_barcode_probe.py to bound the process lifetime and keep logs.
#import <Foundation/Foundation.h>
#import <Vision/Vision.h>
#import <CoreML/CoreML.h>
#import <CoreImage/CoreImage.h>
#import <ImageIO/ImageIO.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#import <objc/message.h>
#import <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <stdio.h>

static NSString *const payload = @"NVMTL-VISION-BARCODE-REGRESSION-2026";

static void emit(NSDictionary *value) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:NSJSONWritingSortedKeys error:NULL];
    if (data) { fwrite(data.bytes, 1, data.length, stdout); fputc('\n', stdout); fflush(stdout); }
}

static CGImageRef makeFixture(void) {
    CIFilter *generator = [CIFilter filterWithName:@"CIQRCodeGenerator"];
    [generator setValue:[payload dataUsingEncoding:NSUTF8StringEncoding] forKey:@"inputMessage"];
    [generator setValue:@"M" forKey:@"inputCorrectionLevel"];
    CIImage *image = generator.outputImage;
    CIContext *software = [CIContext contextWithOptions:@{kCIContextUseSoftwareRenderer: @YES}];
    CGImageRef qr = [software createCGImage:image fromRect:image.extent];
    if (!qr) return NULL;
    CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
    CGContextRef canvas = CGBitmapContextCreate(NULL, 768, 768, 8, 0, space,
        kCGImageAlphaPremultipliedLast | kCGBitmapByteOrder32Big);
    CGColorSpaceRelease(space);
    if (!canvas) { CGImageRelease(qr); return NULL; }
    CGContextSetRGBFillColor(canvas, 1, 1, 1, 1);
    CGContextFillRect(canvas, CGRectMake(0, 0, 768, 768));
    CGContextSetInterpolationQuality(canvas, kCGInterpolationNone);
    // Integral module scaling, with a large quiet zone and screenshot-like margins.
    size_t side = CGImageGetWidth(qr) * 12;
    CGContextDrawImage(canvas, CGRectMake((768-side)/2, (768-side)/2, side, side), qr);
    CGImageRef result = CGBitmapContextCreateImage(canvas);
    CGContextRelease(canvas); CGImageRelease(qr);
    return result;
}

int main(int argc, const char **argv) { @autoreleasepool {
    NSMutableDictionary<NSString *, NSString *> *args = [NSMutableDictionary dictionary];
    for (int i = 1; i < argc; i += 2) {
        if (i + 1 == argc || strncmp(argv[i], "--", 2)) return 2;
        args[@(argv[i] + 2)] = @(argv[i + 1]);
    }
    NSString *mode = args[@"mode"] ?: @"cpu";
    if (![@[@"cpu", @"default", @"gpu"] containsObject:mode]) return 2;
    NSInteger iterations = [args[@"iterations"] ?: @"10" integerValue];
    NSUInteger revision = [args[@"revision"] ?: @"0" integerValue];
    if (iterations < 1 || iterations > 1000) return 2;
    id<MTLDevice> localDevice = nil;
    MLGPUComputeDevice *localGpu = nil;
    if (args[@"plugin"]) {
        if (![mode isEqualToString:@"gpu"]) {
            emit(@{@"event": @"error", @"detail": @"--plugin requires --mode gpu for explicit device selection"}); return 2;
        }
        NSString *wanted = args[@"plugin"].stringByResolvingSymlinksInPath;
        if (!dlopen(wanted.fileSystemRepresentation, RTLD_NOW | RTLD_LOCAL)) {
            emit(@{@"event": @"error", @"detail": @(dlerror() ?: "plugin dlopen failed")}); return 4;
        }
        Class deviceClass = NSClassFromString(@"NVMTLDevice");
        NSString *loaded = @(class_getImageName(deviceClass) ?: "");
        SEL initializer = NSSelectorFromString(@"initWithAcceleratorPort:");
        SEL wrapperFactory = NSSelectorFromString(@"deviceWithMetalDevice:");
        if (![wanted isEqualToString:loaded.stringByResolvingSymlinksInPath] ||
            ![deviceClass instancesRespondToSelector:initializer] || ![MLGPUComputeDevice respondsToSelector:wrapperFactory]) {
            emit(@{@"event": @"error", @"detail": @"local device class origin or private wrapper API could not be verified",
                @"requested_plugin": wanted, @"loaded_image": loaded}); return 4;
        }
        io_service_t service = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("NVAccel"));
        if (!service) { emit(@{@"event": @"error", @"detail": @"no NVAccel accelerator"}); return 4; }
        localDevice = ((id (*)(id, SEL, mach_port_t))objc_msgSend)([deviceClass alloc], initializer, service);
        IOObjectRelease(service);
        if (localDevice) localGpu = ((id (*)(id, SEL, id))objc_msgSend)(MLGPUComputeDevice.class, wrapperFactory, localDevice);
        if (!localDevice || ![localGpu isKindOfClass:MLGPUComputeDevice.class] || localGpu.metalDevice != localDevice) {
            emit(@{@"event": @"error", @"detail": @"failed to wrap the exact local Metal device"}); return 4;
        }
        emit(@{@"event": @"plugin", @"image": loaded, @"device": localDevice.name ?: @"",
            @"wrapper": NSStringFromClass(localGpu.class)});
    }
    if (args[@"capture-dir"]) {
        // The installed release plugin removes inherited NVMTL_* variables in its
        // constructor. Load it first, then configure this child's diagnostics.
        id<MTLDevice> bootstrap = localDevice ?: MTLCreateSystemDefaultDevice();
        if (!bootstrap) { emit(@{@"event": @"error", @"detail": @"no default Metal device"}); return 4; }
        NSString *root = args[@"capture-dir"];
        NSMutableDictionary *settings = [@{
            @"NVMTL_AIRCACHE": [root stringByAppendingPathComponent:@"aircache"],
            @"NVMTL_FC_FAIL_DUMP": [root stringByAppendingPathComponent:@"fc-fail"],
            @"NVMTL_DUMP_FAILED_DIR": [root stringByAppendingPathComponent:@"failed"],
            @"NVMTL_LINK_DUMP": [root stringByAppendingPathComponent:@"link"]
        } mutableCopy];
        if ([args[@"capture-all"] boolValue]) settings[@"NVMTL_FC_DUMP_ALL"] = [root stringByAppendingPathComponent:@"fc-all"];
        for (NSString *key in settings) {
            NSString *path = settings[key];
            [[NSFileManager defaultManager] createDirectoryAtPath:path withIntermediateDirectories:YES attributes:nil error:NULL];
            setenv(key.UTF8String, path.fileSystemRepresentation, 1);
        }
        setenv("NVMTL_LOG_PATH", [[root stringByAppendingPathComponent:@"nvmtl.log"] fileSystemRepresentation], 1);
        emit(@{@"event": @"capture", @"device": bootstrap.name ?: @"",
            @"driver_image": @(class_getImageName([bootstrap class]) ?: ""), @"directory": root,
            @"note": @"release plugin may suppress NVMTL_LOG_PATH; AIR captures remain enabled"});
    }
    NSString *expected = args[@"expected"] ?: (args[@"image"] ? nil : payload);
    CGImageRef image = NULL;
    if (args[@"image"]) {
        CGImageSourceRef source = CGImageSourceCreateWithURL((__bridge CFURLRef)[NSURL fileURLWithPath:args[@"image"]], NULL);
        if (source) { image = CGImageSourceCreateImageAtIndex(source, 0, NULL); CFRelease(source); }
    } else image = makeFixture();
    if (!image) { emit(@{@"event": @"error", @"detail": @"image creation failed"}); return 3; }
    if (args[@"fixture-out"]) {
        CGImageDestinationRef dest = CGImageDestinationCreateWithURL((__bridge CFURLRef)[NSURL fileURLWithPath:args[@"fixture-out"]], CFSTR("public.png"), 1, NULL);
        if (!dest) { CGImageRelease(image); return 3; }
        CGImageDestinationAddImage(dest, image, NULL);
        BOOL written = CGImageDestinationFinalize(dest); CFRelease(dest);
        if (!written) { CGImageRelease(image); return 3; }
    }
    emit(@{@"event": @"start", @"mode": mode, @"iterations": @(iterations),
        @"width": @(CGImageGetWidth(image)), @"height": @(CGImageGetHeight(image)),
        @"os": NSProcessInfo.processInfo.operatingSystemVersionString,
        @"supported_revisions": VNDetectBarcodesRequest.supportedRevisions.description});
    for (NSInteger i = 0; i < iterations; i++) { @autoreleasepool {
        VNDetectBarcodesRequest *request = [VNDetectBarcodesRequest new];
        if (revision) request.revision = revision;
        if ([mode isEqualToString:@"cpu"]) {
            // Preserves the CPU-only control across every supported Vision revision.
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
            request.usesCPUOnly = YES;
#pragma clang diagnostic pop
        } else if ([mode isEqualToString:@"gpu"]) {
            NSError *deviceError = nil;
            MLGPUComputeDevice *gpu = localGpu;
            if (!gpu) {
                NSDictionary *stages = [request supportedComputeStageDevicesAndReturnError:&deviceError];
                for (id device in stages[VNComputeStageMain])
                    if ([device isKindOfClass:MLGPUComputeDevice.class]) { gpu = device; break; }
            }
            if (!gpu) {
                emit(@{@"event": @"error", @"detail": @"no supported GPU for main stage", @"error": deviceError.description ?: @""});
                CGImageRelease(image); return 4;
            }
            [request setComputeDevice:gpu forComputeStage:VNComputeStageMain];
            if ([request computeDeviceForComputeStage:VNComputeStageMain] != gpu ||
                (localDevice && gpu.metalDevice != localDevice)) {
                emit(@{@"event": @"error", @"detail": @"request did not retain the exact selected GPU"});
                CGImageRelease(image); return 4;
            }
            if (i == 0) emit(@{@"event": @"gpu", @"name": gpu.metalDevice.name ?: @"",
                @"stage": VNComputeStageMain, @"explicit_assignment_verified": @YES,
                @"driver_image": @(class_getImageName([gpu.metalDevice class]) ?: "")});
        }
        // Recreate both objects each time: a successful first request does not cover a second one.
        VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:image options:@{}];
        emit(@{@"event": @"request", @"iteration": @(i + 1), @"revision": @(request.revision)});
        NSError *error = nil;
        BOOL ok = [handler performRequests:@[request] error:&error];
        NSMutableArray *codes = [NSMutableArray array]; BOOL match = expected == nil;
        for (VNBarcodeObservation *observation in request.results) {
            NSString *decoded = observation.payloadStringValue ?: @"";
            [codes addObject:@{@"symbology": observation.symbology, @"payload": decoded}];
            if ([decoded isEqualToString:expected]) match = YES;
        }
        emit(@{@"event": @"result", @"iteration": @(i + 1), @"ok": @(ok),
            @"expected_match": @(match), @"codes": codes, @"error": error.description ?: @""});
        if (!ok || !match || codes.count == 0) { CGImageRelease(image); return 5; }
    }}
    CGImageRelease(image);
    emit(@{@"event": @"complete", @"requests": @(iterations)});
    return 0;
}}
