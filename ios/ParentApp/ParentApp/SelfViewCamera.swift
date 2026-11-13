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
    /// Session state below is touched only on sessionQueue.
    private var input: AVCaptureDeviceInput?
    private var output: AVCaptureVideoDataOutput?
    private var previewConnection: AVCaptureConnection?
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
                        self.stats = FrameStats()
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

    // MARK: self-view preview

    /// Connects a preview layer with its own connection: portrait (90 degrees) and mirrored, set before the
    /// connection is added. Left to AVFoundation, the layer's connection appears later and can keep the
    /// sensor's landscape angle, which showed the self-view turned 90 degrees.
    func attachPreview(_ layer: AVCaptureVideoPreviewLayer) {
        layer.setSessionWithNoConnection(session)
        sessionQueue.async {
            try? self.configure()
            guard let input = self.input,
                  let port = input.ports(for: .video, sourceDeviceType: input.device.deviceType,
                                         sourceDevicePosition: .front).first
            else { return }
            self.session.beginConfiguration()
            defer { self.session.commitConfiguration() }
            if let old = self.previewConnection {
                self.session.removeConnection(old)
                self.previewConnection = nil
            }
            let connection = AVCaptureConnection(inputPort: port, videoPreviewLayer: layer)
            if connection.isVideoRotationAngleSupported(90) {
                connection.videoRotationAngle = 90
            }
            if connection.isVideoMirroringSupported {
                connection.automaticallyAdjustsVideoMirroring = false
                connection.isVideoMirrored = true
            }
            if self.session.canAddConnection(connection) {
                self.session.addConnection(connection)
                self.previewConnection = connection
            }
        }
    }

    func detachPreview(_ layer: AVCaptureVideoPreviewLayer) {
        sessionQueue.async {
            guard let connection = self.previewConnection, connection.videoPreviewLayer === layer else { return }
            self.session.removeConnection(connection)
            self.previewConnection = nil
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
            self.input = input
        }
        let output = AVCaptureVideoDataOutput()
        output.alwaysDiscardsLateVideoFrames = true
        output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
        output.setSampleBufferDelegate(self, queue: frameQueue)
        if session.canAddOutput(output) {
            session.addOutput(output)
            self.output = output
        }
        configured = true
    }

    /// What the camera delivered during the current capture, for the camera debug screen.
    struct FrameStats: Equatable, Sendable {
        var rawWidth = 0
        var rawHeight = 0
        var mirrored = false
        var angle: Double = 0
        /// Landscape buffers, turned upright and mirrored: the one case checked on the phone.
        var fixed = 0
        /// Portrait buffers, sent as they came: not expected; if these appear, frames are not all alike.
        var asIs = 0
    }

    private var stats = FrameStats()

    func frameStats() -> FrameStats {
        lock.withLock { stats }
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        let now = CACurrentMediaTime()
        let wanted = lock.withLock { capturing && now - lastEncode >= minEncodeInterval }
        guard wanted, let pixels = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        var image = CIImage(cvPixelBuffer: pixels)
        // The frame output's connection keeps the front camera's default (landscape, mirrored): its rotation and
        // mirroring settings did not take on the iPhone, and setting them again at capture start risked frames that
        // differ between captures. So every frame is fixed here, one way: turn 90 degrees counter-clockwise, then
        // mirror, which gives the un-mirrored upright picture the analysis expects (UW_MIRRORED off). Checked on an
        // iPhone 18 Pro Max with the camera debug screen: clockwise gave her upside down, and unmirrored swapped hands.
        let landscape = image.extent.width > image.extent.height
        if landscape {
            image = image.oriented(.left).oriented(.upMirrored)
        }
        lock.withLock {
            stats.rawWidth = CVPixelBufferGetWidth(pixels)
            stats.rawHeight = CVPixelBufferGetHeight(pixels)
            stats.mirrored = connection.isVideoMirrored
            stats.angle = connection.videoRotationAngle
            if landscape { stats.fixed += 1 } else { stats.asIs += 1 }
        }
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

/// The self-view: a live AVCaptureVideoPreviewLayer on the shared session, upright and mirrored.
struct CameraPreview: UIViewRepresentable {
    let camera: SelfViewCamera

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        view.camera = camera
        view.previewLayer.videoGravity = .resizeAspectFill
        // Clear until frames arrive, so the design's striped placeholder shows through.
        view.backgroundColor = .clear
        view.previewLayer.backgroundColor = UIColor.clear.cgColor
        camera.attachPreview(view.previewLayer)
        return view
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {}

    static func dismantleUIView(_ uiView: PreviewView, coordinator: ()) {
        uiView.camera?.detachPreview(uiView.previewLayer)
    }

    final class PreviewView: UIView {
        weak var camera: SelfViewCamera?

        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }

        var previewLayer: AVCaptureVideoPreviewLayer {
            // swiftlint:disable:next force_cast
            layer as! AVCaptureVideoPreviewLayer
        }
    }
}
