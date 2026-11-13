import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public struct SSEMessage: Sendable, Equatable {
    public let id: String?
    public let event: String
    public let data: String
}

/// Incremental text/event-stream parser. Feed raw bytes as they arrive.
public struct SSEParser: Sendable {
    private var buffer = Data()
    private var id: String?
    private var event: String?
    private var dataLines: [String] = []

    public init() {}

    public mutating func feed(_ chunk: Data) -> [SSEMessage] {
        buffer.append(chunk)
        var out: [SSEMessage] = []
        while let newline = buffer.firstIndex(of: 0x0A) {
            var lineData = buffer[buffer.startIndex..<newline]
            buffer.removeSubrange(buffer.startIndex...newline)
            if lineData.last == 0x0D {
                lineData = lineData.dropLast()
            }
            let line = String(decoding: lineData, as: UTF8.self)
            if let message = handle(line) {
                out.append(message)
            }
        }
        return out
    }

    private mutating func handle(_ line: String) -> SSEMessage? {
        if line.isEmpty {
            return dispatch()
        }
        if line.hasPrefix(":") {
            return nil
        }
        let field: String
        var value: String
        if let colon = line.firstIndex(of: ":") {
            field = String(line[line.startIndex..<colon])
            value = String(line[line.index(after: colon)...])
            if value.hasPrefix(" ") {
                value.removeFirst()
            }
        } else {
            field = line
            value = ""
        }
        switch field {
        case "id":
            id = value
        case "event":
            event = value
        case "data":
            dataLines.append(value)
        default:
            break
        }
        return nil
    }

    private mutating func dispatch() -> SSEMessage? {
        defer {
            event = nil
            dataLines = []
        }
        if dataLines.isEmpty {
            return nil
        }
        return SSEMessage(id: id, event: event ?? "message", data: dataLines.joined(separator: "\n"))
    }
}

/// One SSE connection. Messages arrive on `messages`; the stream finishes when the connection closes.
final class SSEConnection: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    let messages: AsyncThrowingStream<SSEMessage, Error>
    private let continuation: AsyncThrowingStream<SSEMessage, Error>.Continuation
    private var parser = SSEParser()
    private var session: URLSession?
    private var task: URLSessionDataTask?
    private let lock = NSLock()

    init(request: URLRequest) {
        var cont: AsyncThrowingStream<SSEMessage, Error>.Continuation!
        messages = AsyncThrowingStream { cont = $0 }
        continuation = cont
        super.init()
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 3600
        configuration.timeoutIntervalForResource = 3600
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        self.session = session
        let task = session.dataTask(with: request)
        self.task = task
        continuation.onTermination = { [weak self] _ in
            self?.cancel()
        }
        task.resume()
    }

    func cancel() {
        task?.cancel()
        session?.invalidateAndCancel()
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        if status != 200 {
            continuation.finish(throwing: APIError.http(status: status, code: "stream_rejected"))
            completionHandler(.cancel)
            return
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        lock.lock()
        let messages = parser.feed(data)
        lock.unlock()
        for message in messages {
            continuation.yield(message)
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error {
            continuation.finish(throwing: error)
        } else {
            continuation.finish()
        }
    }
}
