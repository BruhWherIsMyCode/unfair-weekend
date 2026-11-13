import Foundation
import XCTest
@testable import ParentCallKit

/// Answers poll requests from a script: the first page has one event and cursor 7; later polls must send after=7.
final class PollStub: URLProtocol {
    nonisolated(unsafe) static var queries: [String] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let query = request.url?.query ?? ""
        PollStub.queries.append(query)
        let first = !query.contains("after=")
        let body = first
            ? #"{"events":[{"id":"ev_7","event":"call.ring","data":"{\"call_id\":\"c1\"}"}],"cursor":7}"#
            : #"{"events":[],"cursor":7}"#
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

final class EventPollTests: XCTestCase {
    func testQuickTunnelAddressesArePolled() {
        XCTAssertTrue(CallAPI.prefersPolling(URL(string: "https://some-words-here.trycloudflare.com")!))
        XCTAssertFalse(CallAPI.prefersPolling(URL(string: "http://192.168.0.96:8000")!))
        XCTAssertFalse(CallAPI.prefersPolling(URL(string: "https://trycloudflare.com.example.org")!))
    }

    func testPollConnectionYieldsEventsAndSendsTheCursorBack() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [PollStub.self]
        let api = CallAPI(baseURL: URL(string: "https://x.trycloudflare.com")!, demoKey: "k",
                          session: URLSession(configuration: configuration))
        PollStub.queries = []
        let connection = api.events(deviceId: "dev_1", lastEventId: nil)
        XCTAssertTrue(connection is PollConnection)
        var iterator = connection.messages.makeAsyncIterator()
        let message = try await iterator.next()
        XCTAssertEqual(message, SSEMessage(id: "ev_7", event: "call.ring", data: #"{"call_id":"c1"}"#))
        try await Task.sleep(nanoseconds: 100_000_000)
        connection.cancel()
        XCTAssertTrue(PollStub.queries.dropFirst().allSatisfy { $0.contains("after=7") }, "\(PollStub.queries)")
        XCTAssertGreaterThan(PollStub.queries.count, 1)
    }
}
