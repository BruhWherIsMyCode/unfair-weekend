import AVFoundation
import CoreImage
import ParentCallKit
import SwiftUI
import UIKit

/// The front camera, shared by the self-view and the task frames.
///
/// iOS lets only one capture session use the camera at a time, so the self-view preview and the
/// frames sent to the backend come from this one session. The app runs it for the whole call
/// (`startPreview` / `stopPreview`); CallClient's `start` / `stop` only switch frame capture on and off.
/// Frames are rotated upright (480 x 640) and not mirrored. The preview is mirrored, like a mirror.
final class SelfViewCamera: NSObject, CameraSource, AVCaptureVideoDataOutputSampleBufferDelegate, @unchecked Sendable {
    enum CameraError: Error {
        case noFrontCamera
    }

    let session = AVCaptureSession()
    private let sessionQueue = DispatchQueue(label: "parent.camera.session")
    private let frameQueue = DispatchQueue(label: "parent.camera.frames")
    private let context = CIContext()
    private let lock = NSLock()
    private var latest: Data?
    private var capturing = false
    private var previewWanted = false
    private var configured = false
    private var lastEncode: CFTimeInterval = 0
    private var onAvailability: (@Sendable (Bool, ResourceProblem?) -> Void)?

    /// Shortest gap between two encoded frames. The backend asks for one every 200 ms.
    private let minEncodeInterval: CFTimeInterval = 0.09

    override init() {
        super.init()
        let center = NotificationCenter.default
        center.addObserver(self, selector: #selector(interrupted), name: AVCaptureSession.wasInterruptedNotification,
                           object: session)
        center.addObserver(self, selector: #selector(resumed), name: AVCaptureSession.interruptionEndedNotification,
                           object: session)
        center.addObserver(self, selector: #selector(failed), name: AVCaptureSession.runtimeErrorNotification,
                           object: session)
    }

    /// Called with (available, problem) when the camera is lost or comes back during a call.
    func setAvailabilityHandler(_ handler: @escaping @Sendable (Bool, ResourceProblem?) -> Void) {
        lock.withLock { onAvailability = handler }
    }

    /// Configure the session ahead of the first call, so the preview has a connection to rotate.
    func prepare() {
        sessionQueue.async { try? self.configure() }
    }

    func startPreview() {
        lock.withLock { previewWanted = true }
        sessionQueue.async {
            do {
                try self.configure()
                if !self.session.isRunning {
                    self.session.startRunning()
                }
            } catch {
                self.report(false, .deviceMissing)
            }
        }
    }

    func stopPreview() {
        lock.withLock {
            previewWanted = false
            capturing = false
            latest = nil
        }
        sessionQueue.async {
            if self.session.isRunning {
                self.session.stopRunning()
            }
        }
    }

    // MARK: CameraSource (driven by CallClient during smile and arms)

    func start() async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            sessionQueue.async {
                do {
                    try self.configure()
                    self.lock.withLock {
                        self.latest = nil
                        self.capturing = true
                    }
                    if !self.session.isRunning {
                        self.session.startRunning()
                    }
                    continuation.resume()
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    func jpegFrame() async throws -> Data? {
        lock.withLock { latest }
    }

    func stop() async {
        let keepRunning = lock.withLock { () -> Bool in
            capturing = false
            latest = nil
            return previewWanted
        }
        if !keepRunning {
            sessionQueue.async {
                if self.session.isRunning {
                    self.session.stopRunning()
                }
            }
        }
    }

    // MARK: session

    /// Runs on sessionQueue.
    private func configure() throws {
        if configured {
            return
        }
        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .front) else {
            throw CameraError.noFrontCamera
        }
        let input = try AVCaptureDeviceInput(device: device)
        session.beginConfiguration()
        defer { session.commitConfiguration() }
        session.sessionPreset = .vga640x480
        if session.canAddInput(input) {
            session.addInput(input)
        }
        let output = AVCaptureVideoDataOutput()
        output.alwaysDiscardsLateVideoFrames = true
        output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
        output.setSampleBufferDelegate(self, queue: frameQueue)
        if session.canAddOutput(output) {
            session.addOutput(output)
        }
        if let connection = output.connection(with: .video) {
            // Portrait: 90 degrees makes the buffers 480 x 640 and upright for the front camera.
            if connection.isVideoRotationAngleSupported(90) {
                connection.videoRotationAngle = 90
            }
            if connection.isVideoMirroringSupported {
                connection.automaticallyAdjustsVideoMirroring = false
                connection.isVideoMirrored = false
            }
        }
        configured = true
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        let now = CACurrentMediaTime()
        let wanted = lock.withLock { capturing && now - lastEncode >= minEncodeInterval }
        guard wanted, let pixels = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let image = CIImage(cvPixelBuffer: pixels)
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let jpeg = context.jpegRepresentation(
                of: image, colorSpace: space,
                options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.7])
        else { return }
        lock.withLock {
            if capturing {
                latest = jpeg
                lastEncode = now
            }
        }
    }

    // MARK: interruptions

    private func report(_ available: Bool, _ problem: ResourceProblem?) {
        let handler = lock.withLock { previewWanted ? onAvailability : nil }
        handler?(available, problem)
    }

    @objc private func interrupted(_ note: Notification) {
        report(false, .trackEnded)
    }

    @objc private func resumed(_ note: Notification) {
        report(true, nil)
    }

    @objc private func failed(_ note: Notification) {
        report(false, .unknown)
    }
}

/// The self-view: a live AVCaptureVideoPreviewLayer on the shared session.
struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspectFill
        view.backgroundColor = .black
        return view
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {
        uiView.keepUpright()
    }

    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }

        var previewLayer: AVCaptureVideoPreviewLayer {
            // swiftlint:disable:next force_cast
            layer as! AVCaptureVideoPreviewLayer
        }

        override func layoutSubviews() {
            super.layoutSubviews()
            keepUpright()
        }

        override func didMoveToWindow() {
            super.didMoveToWindow()
            keepUpright()
        }

        func keepUpright() {
            guard let connection = previewLayer.connection, connection.videoRotationAngle != 90,
                  connection.isVideoRotationAngleSupported(90) else { return }
            connection.videoRotationAngle = 90
        }
    }
}
