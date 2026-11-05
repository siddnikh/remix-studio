// Writes one grayscale subject mask per PNG in a folder, using the system's Vision framework (macOS 14+).
// Build: clang -fobjc-arc -O2 -framework Foundation -framework Vision -framework CoreImage -framework CoreVideo -framework CoreGraphics matte.m -o matte
// A frame with no detected subject gets an all-black mask, which draws type over the whole picture.
#import <CoreImage/CoreImage.h>
#import <Foundation/Foundation.h>
#import <Vision/Vision.h>

int main(int argc, const char **argv) {
    if (argc != 3) { fprintf(stderr, "usage: matte <input_dir> <output_dir>\n"); return 2; }
    NSString *inDir = @(argv[1]), *outDir = @(argv[2]);
    NSFileManager *fm = NSFileManager.defaultManager;
    [fm createDirectoryAtPath:outDir withIntermediateDirectories:YES attributes:nil error:nil];
    NSArray *names = [[[fm contentsOfDirectoryAtPath:inDir error:nil]
        filteredArrayUsingPredicate:[NSPredicate predicateWithFormat:@"self ENDSWITH '.png'"]]
        sortedArrayUsingSelector:@selector(compare:)];
    CIContext *ctx = [CIContext contextWithOptions:nil];
    CGColorSpaceRef gray = CGColorSpaceCreateWithName(kCGColorSpaceLinearGray);
    int empty = 0;
    for (NSString *name in names) {
        @autoreleasepool {
            NSURL *url = [NSURL fileURLWithPath:[inDir stringByAppendingPathComponent:name]];
            CIImage *input = [CIImage imageWithContentsOfURL:url];
            if (!input) { fprintf(stderr, "cannot read %s\n", name.UTF8String); return 1; }
            NSError *err = nil;
            VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithURL:url options:@{}];
            VNGenerateForegroundInstanceMaskRequest *req = [VNGenerateForegroundInstanceMaskRequest new];
            if (![handler performRequests:@[req] error:&err]) { fprintf(stderr, "%s\n", err.description.UTF8String); return 1; }
            CIImage *mask = [[CIImage imageWithColor:CIColor.blackColor] imageByCroppingToRect:input.extent];
            VNInstanceMaskObservation *obs = req.results.firstObject;
            if (obs) {
                CVPixelBufferRef buf = [obs generateScaledMaskForImageForInstances:obs.allInstances fromRequestHandler:handler error:&err];
                if (!buf) { fprintf(stderr, "%s\n", err.description.UTF8String); return 1; }
                mask = [CIImage imageWithCVPixelBuffer:buf];
            } else {
                empty++;
            }
            NSURL *dest = [NSURL fileURLWithPath:[outDir stringByAppendingPathComponent:name]];
            if (![ctx writePNGRepresentationOfImage:mask toURL:dest format:kCIFormatL8 colorSpace:gray options:@{} error:&err]) {
                fprintf(stderr, "%s\n", err.description.UTF8String); return 1;
            }
        }
    }
    printf("{\"frames\": %lu, \"no_subject\": %d}\n", (unsigned long)names.count, empty);
    return 0;
}
