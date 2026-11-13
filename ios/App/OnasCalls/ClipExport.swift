import AVFoundation
import Photos
import SwiftUI
import UIKit

/// Turns a call's clip into an .mp4 the family can keep: Save to Photos, or the share sheet (Files, AirDrop,
/// Messages). A real alert's frames are encoded once and cached; the staged clip is copied as is.
enum ClipExport {
    enum Failure: Error { case noClip, encode }

    nonisolated static func cachedURL(_ id: String) -> URL {
        let dir = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("clip-exports", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("\(id).mp4")
    }

    /// The share sheet shows the file name, so make it readable: "Fedya check-in 2026-11-14 09-02.mp4".
    private static func namedURL(_ record: CallRecord, name: String) -> URL {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_GB")
        f.dateFormat = "yyyy-MM-dd HH-mm"
        let safeName = name.components(separatedBy: CharacterSet(charactersIn: "/\\:")).joined()
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("share-\(record.id)", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("\(safeName) check-in \(f.string(from: record.date)).mp4")
    }

    static func hasClip(_ record: CallRecord) -> Bool {
        switch record.clip {
        case .staged: LocalAssets.clipURL != nil
        case .frames: true
        case nil: false
        }
    }

    /// A ready .mp4 for this call, named for sharing.
    static func export(_ record: CallRecord, name: String) async throws -> URL {
        let source: URL
        switch record.clip {
        case .staged:
            guard let url = LocalAssets.clipURL else { throw Failure.noClip }
            source = url
        case .frames:
            let cached = cachedURL(record.id)
            if !FileManager.default.fileExists(atPath: cached.path) {
                try await Task.detached(priority: .userInitiated) {
                    guard let clip = CallHistory.loadFrames(record) else { throw Failure.noClip }
                    try await writeMP4(frames: clip.frames, periodMs: clip.periodMs, to: cached)
                }.value
            }
            source = cached
        case nil:
            throw Failure.noClip
        }
        let named = namedURL(record, name: name)
        try? FileManager.default.removeItem(at: named)
        try FileManager.default.copyItem(at: source, to: named)
        return named
    }

    /// Adds the video to the photo library. Asks for add-only access the first time.
    static func saveToPhotos(_ url: URL) async -> Bool {
        let status = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
        guard status == .authorized || status == .limited else { return false }
        do {
            try await PHPhotoLibrary.shared().performChanges {
                PHAssetCreationRequest.creationRequestForAssetFromVideo(atFileURL: url)
            }
            return true
        } catch {
            return false
        }
    }

    /// H.264 at the rate the frames were taken; the last frame is held for one period.
    private static func writeMP4(frames: [UIImage], periodMs: Int, to url: URL) async throws {
        try? FileManager.default.removeItem(at: url)
        guard let first = frames.first else { throw Failure.noClip }
        let pixelSize = CGSize(width: first.size.width * first.scale, height: first.size.height * first.scale)
        let shrink = min(1, 1280 / max(pixelSize.width, pixelSize.height))
        let width = Int(pixelSize.width * shrink) & ~1
        let height = Int(pixelSize.height * shrink) & ~1
        guard width > 0, height > 0 else { throw Failure.encode }

        let writer = try AVAssetWriter(outputURL: url, fileType: .mp4)
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
        ])
        input.expectsMediaDataInRealTime = false
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey as String: width,
            kCVPixelBufferHeightKey as String: height,
        ])
        guard writer.canAdd(input) else { throw Failure.encode }
        writer.add(input)
        guard writer.startWriting() else { throw writer.error ?? Failure.encode }
        writer.startSession(atSourceTime: .zero)

        let period = Int64(max(periodMs, 1))
        for (i, image) in frames.enumerated() {
            while !input.isReadyForMoreMediaData { try await Task.sleep(for: .milliseconds(5)) }
            guard let buffer = pixelBuffer(image, width: width, height: height, pool: adaptor.pixelBufferPool),
                  adaptor.append(buffer, withPresentationTime: CMTime(value: Int64(i) * period, timescale: 1000)) else {
                writer.cancelWriting()
                throw writer.error ?? Failure.encode
            }
        }
        input.markAsFinished()
        writer.endSession(atSourceTime: CMTime(value: Int64(frames.count) * period, timescale: 1000))
        await writer.finishWriting()
        guard writer.status == .completed else { throw writer.error ?? Failure.encode }
    }

    private static func pixelBuffer(_ image: UIImage, width: Int, height: Int, pool: CVPixelBufferPool?) -> CVPixelBuffer? {
        var buffer: CVPixelBuffer?
        if let pool {
            CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer)
        } else {
            CVPixelBufferCreate(nil, width, height, kCVPixelFormatType_32BGRA, nil, &buffer)
        }
        guard let buffer else { return nil }
        CVPixelBufferLockBaseAddress(buffer, [])
        defer { CVPixelBufferUnlockBaseAddress(buffer, []) }
        guard let context = CGContext(
            data: CVPixelBufferGetBaseAddress(buffer), width: width, height: height, bitsPerComponent: 8,
            bytesPerRow: CVPixelBufferGetBytesPerRow(buffer), space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.noneSkipFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue
        ) else { return nil }
        // UIKit drawing is top-down and honours the image's orientation.
        context.translateBy(x: 0, y: CGFloat(height))
        context.scaleBy(x: 1, y: -1)
        UIGraphicsPushContext(context)
        image.draw(in: CGRect(x: 0, y: 0, width: width, height: height))
        UIGraphicsPopContext()
        return buffer
    }
}

/// Under a call's clip: prepares the .mp4, then "Save to Photos" and a share button (Files, AirDrop, Messages).
struct ClipSaveBar: View {
    let record: CallRecord
    let name: String

    private enum Prep: Equatable { case preparing, ready(URL), failed }
    @State private var state: Prep = .preparing
    @State private var saving = false
    @State private var saved: Bool?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            switch state {
            case .preparing:
                HStack(spacing: 10) {
                    ProgressView()
                    Text("Preparing the clip…").foregroundStyle(Palette.secondary)
                }
                .frame(maxWidth: .infinity, minHeight: 50)
            case .failed:
                Text("The clip could not be prepared.")
                    .foregroundStyle(Palette.secondary)
                    .frame(maxWidth: .infinity, minHeight: 50)
            case .ready(let url):
                HStack(spacing: 10) {
                    Button {
                        Task { await save(url) }
                    } label: {
                        HStack(spacing: 8) {
                            if saving { ProgressView().tint(.white) } else { Image(systemName: "arrow.down.to.line") }
                            Text("Save to Photos")
                        }
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity)
                        .frame(height: 50)
                        .background(Palette.ink, in: RoundedRectangle(cornerRadius: 16))
                    }
                    .disabled(saving)
                    ShareLink(item: url) {
                        HStack(spacing: 8) {
                            Image(systemName: "square.and.arrow.up")
                            Text("Share")
                        }
                        .foregroundStyle(Palette.ink)
                        .frame(maxWidth: .infinity)
                        .frame(height: 50)
                        .background(Palette.grey, in: RoundedRectangle(cornerRadius: 16))
                    }
                }
                .font(.system(size: 17, weight: .semibold))
                .buttonStyle(PressStyle())
                if let saved {
                    Text(saved ? "Saved to Photos." : "Could not save. Allow adding to Photos in Settings.")
                        .font(.system(size: 15))
                        .foregroundStyle(saved ? Palette.usualInk : Palette.red)
                }
            }
        }
        .task(id: record.id) {
            do {
                state = .ready(try await ClipExport.export(record, name: name))
            } catch {
                state = .failed
            }
        }
    }

    private func save(_ url: URL) async {
        saving = true
        saved = await ClipExport.saveToPhotos(url)
        saving = false
    }
}
