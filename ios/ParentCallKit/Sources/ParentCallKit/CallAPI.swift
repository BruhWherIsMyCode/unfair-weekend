import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public enum APIError: Error, Equatable {
    case http(status: Int, code: String)
    case badResponse
}

/// The call service's HTTP surface (backend/checkin_backend/app.py, contract/mvp.openapi.yaml).
public struct CallAPI: Sendable {
    public let baseURL: URL
    public let demoKey: String
    private let session: URLSession

    public init(baseURL: URL, demoKey: String, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.demoKey = demoKey
        self.session = session
    }

    func request(_ path: String, method: String = "GET") -> URLRequest {
        let url = URL(string: path, relativeTo: baseURL)!.absoluteURL
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue(demoKey, forHTTPHeaderField: "X-Demo-Key")
        request.timeoutInterval = 30
        return request
    }

    /// Cancelling the calling task cancels the transfer (hang-up, command cancel) and throws CancellationError.
    @discardableResult
    func send(_ request: URLRequest) async throws -> (Data, Int) {
        let inFlight = InFlightTask()
        let (data, status) = try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<(Data, Int), Error>) in
                let task = session.dataTask(with: request) { data, response, error in
                    if let error {
                        let cancelled = (error as? URLError)?.code == .cancelled
                        continuation.resume(throwing: cancelled ? CancellationError() : error)
                        return
                    }
                    guard let http = response as? HTTPURLResponse else {
                        continuation.resume(throwing: APIError.badResponse)
                        return
                    }
                    continuation.resume(returning: (data ?? Data(), http.statusCode))
                }
                inFlight.start(task)
            }
        } onCancel: {
            inFlight.cancel()
        }
        if status >= 300 {
            let code = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["code"] as? String
            throw APIError.http(status: status, code: code ?? "unknown")
        }
        return (data, status)
    }

    func postJSON(_ path: String, _ body: [String: Any?]) async throws -> Data {
        var req = request(path, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSON.encode(body)
        return try await send(req).0
    }

    public func register(deviceId: String, personId: String) async throws {
        _ = try await postJSON("/v1/devices", ["device_id": deviceId, "role": "parent", "person_id": personId])
    }

    func events(deviceId: String, lastEventId: String?) -> SSEConnection {
        var req = request("/v1/devices/\(deviceId)/events")
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        req.timeoutInterval = 3600
        if let lastEventId {
            req.setValue(lastEventId, forHTTPHeaderField: "Last-Event-ID")
        }
        return SSEConnection(request: req)
    }

    public func answer(callId: String) async throws -> AnswerResponse {
        let data = try await postJSON("/v1/calls/\(callId)/answer", [:])
        return try JSON.decoder.decode(AnswerResponse.self, from: data)
    }

    public func decline(callId: String) async throws {
        _ = try await postJSON("/v1/calls/\(callId)/decline", [:])
    }

    func postEvent(callId: String, _ event: [String: Any?]) async throws {
        _ = try await postJSON("/v1/calls/\(callId)/client-events", event)
    }

    func speech(path: String) async throws -> Data {
        try await send(request(path)).0
    }

    /// The prepared avatar clip for one say command (video/mp4).
    func video(path: String) async throws -> Data {
        var req = request(path)
        req.timeoutInterval = 60
        req.setValue("video/mp4", forHTTPHeaderField: "Accept")
        return try await send(req).0
    }

    /// A Simli live session for this answered call. 503 means live is off or unavailable: use clips and voice.
    func liveAvatar(callId: String) async throws -> LiveAvatarSession {
        let data = try await postJSON("/v1/calls/\(callId)/avatar/live", [:])
        return try JSON.decoder.decode(LiveAvatarSession.self, from: data)
    }

    func uploadAnswer(callId: String, commandId: String, audio: Data, contentType: String) async throws {
        var req = request("/v1/calls/\(callId)/captures/\(commandId)", method: "POST")
        req.setValue(contentType, forHTTPHeaderField: "Content-Type")
        req.httpBody = audio
        try await send(req)
    }

    func postSample(callId: String, attemptId: String, sequence: Int, capturedMs: Int, format: String, data: Data) async throws {
        _ = try await postJSON("/v1/calls/\(callId)/attempts/\(attemptId)/samples", [
            "sequence": sequence,
            "captured_ms": capturedMs,
            "format": format,
            "data_base64": data.base64EncodedString(),
        ])
    }

    func finish(callId: String, attemptId: String, lastSequence: Int, capturedUntilMs: Int) async throws {
        _ = try await postJSON("/v1/calls/\(callId)/attempts/\(attemptId)/finish", [
            "last_sequence": lastSequence,
            "captured_until_ms": capturedUntilMs,
        ])
    }
}

/// A URLSession task that may be cancelled before or after it starts.
final class InFlightTask: @unchecked Sendable {
    private let lock = NSLock()
    private var task: URLSessionDataTask?
    private var cancelled = false

    func start(_ task: URLSessionDataTask) {
        let alreadyCancelled = lock.withLock { () -> Bool in
            self.task = task
            return cancelled
        }
        task.resume()
        if alreadyCancelled {
            task.cancel()
        }
    }

    func cancel() {
        let task = lock.withLock { () -> URLSessionDataTask? in
            cancelled = true
            return self.task
        }
        task?.cancel()
    }
}
