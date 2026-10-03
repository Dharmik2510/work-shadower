#if os(macOS)
import AppKit
import CoreGraphics
import CryptoKit
import ScreenCaptureKit
import DotCore

func sha256Hex(_ data: Data) -> String {
    return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

struct CapturedShot {
    let sha256: String
    let path: String
    let bytes: Int
}

/// Captures the frontmost window of a given app as a JPEG (q=0.6, ≤1600px),
/// writes it to Application Support/WorkShadower/assets/<sha256>.jpg.
/// ScreenCaptureKit on macOS 14+, CGWindowListCreateImage on 13.
final class Screenshotter {
    static let maxDimension: CGFloat = 1600
    static let quality: CGFloat = 0.6
    static let maxBytes = 2 * 1024 * 1024

    private let ourPID = ProcessInfo.processInfo.processIdentifier

    var isPermitted: Bool { return CGPreflightScreenCaptureAccess() }

    /// `preferredFrame` is the AX frame (global, top-left origin) of the window
    /// we want; when several windows of the app are on screen we pick the closest.
    func capture(pid: pid_t, preferredFrame: CGRect?, completion: @escaping (CapturedShot?) -> Void) {
        guard pid != ourPID, isPermitted else { completion(nil); return }
        if #available(macOS 14.0, *) {
            Task {
                let image = await self.captureSCK(pid: pid, preferredFrame: preferredFrame)
                completion(image.flatMap { self.store($0) })
            }
        } else {
            DispatchQueue.global(qos: .userInitiated).async {
                let image = self.captureLegacy(pid: pid)
                completion(image.flatMap { self.store($0) })
            }
        }
    }

    // MARK: macOS 14+

    @available(macOS 14.0, *)
    private func captureSCK(pid: pid_t, preferredFrame: CGRect?) async -> CGImage? {
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
            let candidates = content.windows.filter {
                $0.owningApplication?.processID == pid && $0.windowLayer == 0 && $0.isOnScreen
                    && $0.frame.width > 50 && $0.frame.height > 50
            }
            let window: SCWindow?
            if let pf = preferredFrame {
                window = candidates.min(by: { Screenshotter.distance($0.frame, pf) < Screenshotter.distance($1.frame, pf) })
            } else {
                window = candidates.first
            }
            guard let window = window else { return nil }
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let config = SCStreamConfiguration()
            let size = Screenshotter.scaledSize(window.frame.size, scale: 2)
            config.width = Int(size.width)
            config.height = Int(size.height)
            config.showsCursor = false
            return try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        } catch {
            Log.recorder.error("screenshot failed (sck)")
            return nil
        }
    }

    // MARK: macOS 13 fallback

    private typealias CreateImageFn = @convention(c) (CGRect, UInt32, UInt32, UInt32) -> Unmanaged<CGImage>?

    /// CGWindowListCreateImage is deprecated (14) / obsoleted (15 SDK), so it
    /// is looked up at runtime instead of referenced at compile time.
    private static let createImage: CreateImageFn? = {
        guard let handle = dlopen(nil, RTLD_NOW), let sym = dlsym(handle, "CGWindowListCreateImage") else { return nil }
        return unsafeBitCast(sym, to: CreateImageFn.self)
    }()

    private func captureLegacy(pid: pid_t) -> CGImage? {
        guard let fn = Screenshotter.createImage else { return nil }
        let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
        guard let info = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] else { return nil }
        // Front-to-back order: first normal-layer window owned by pid.
        var windowID: CGWindowID?
        for w in info {
            let owner = (w[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value
            let layer = (w[kCGWindowLayer as String] as? NSNumber)?.intValue
            if owner == pid && layer == 0, let n = (w[kCGWindowNumber as String] as? NSNumber)?.uint32Value {
                windowID = n
                break
            }
        }
        guard let wid = windowID else { return nil }
        // kCGWindowListOptionIncludingWindow = 1 << 3; kCGWindowImageBoundsIgnoreFraming = 1 << 0,
        // kCGWindowImageNominalResolution = 1 << 4.
        let listOption: UInt32 = 1 << 3
        let imageOption: UInt32 = (1 << 0) | (1 << 4)
        return fn(CGRect.null, listOption, wid, imageOption)?.takeRetainedValue()
    }

    // MARK: Encoding / storage

    static func distance(_ a: CGRect, _ b: CGRect) -> CGFloat {
        return abs(a.minX - b.minX) + abs(a.minY - b.minY) + abs(a.width - b.width) + abs(a.height - b.height)
    }

    static func scaledSize(_ size: CGSize, scale: CGFloat) -> CGSize {
        let w = max(1, size.width * scale), h = max(1, size.height * scale)
        let longest = max(w, h)
        let f = longest > maxDimension ? maxDimension / longest : 1
        return CGSize(width: floor(w * f), height: floor(h * f))
    }

    private func resized(_ image: CGImage) -> CGImage {
        let target = Screenshotter.scaledSize(CGSize(width: image.width, height: image.height), scale: 1)
        if Int(target.width) == image.width && Int(target.height) == image.height { return image }
        guard let ctx = CGContext(data: nil, width: Int(target.width), height: Int(target.height), bitsPerComponent: 8,
                                  bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return image }
        ctx.interpolationQuality = .high
        ctx.draw(image, in: CGRect(origin: .zero, size: target))
        return ctx.makeImage() ?? image
    }

    private func jpeg(_ image: CGImage, quality: CGFloat) -> Data? {
        let rep = NSBitmapImageRep(cgImage: image)
        return rep.representation(using: .jpeg, properties: [.compressionFactor: quality])
    }

    private func store(_ raw: CGImage) -> CapturedShot? {
        let image = resized(raw)
        guard var data = jpeg(image, quality: Screenshotter.quality) else { return nil }
        if data.count > Screenshotter.maxBytes, let smaller = jpeg(image, quality: 0.35) { data = smaller }
        guard data.count <= Screenshotter.maxBytes else { return nil }
        let hash = sha256Hex(data)
        let url = SettingsStore.assetsDirectory.appendingPathComponent("\(hash).jpg")
        do {
            if !FileManager.default.fileExists(atPath: url.path) {
                try data.write(to: url, options: [.atomic])
            }
        } catch {
            Log.recorder.error("screenshot write failed")
            return nil
        }
        return CapturedShot(sha256: hash, path: url.path, bytes: data.count)
    }
}
#endif
