import XCTest
@testable import DotCore
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

final class QueueAndUploaderTests: XCTestCase {
    var dir: String!

    override func setUp() {
        super.setUp()
        dir = NSTemporaryDirectory() + "dotcore-tests-" + UUID().uuidString
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true, attributes: nil)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(atPath: dir)
        super.tearDown()
    }

    func testQueueLifecycleAndPersistence() throws {
        let path = dir + "/queue.sqlite"
        let t0 = Date(timeIntervalSince1970: 1000)
        do {
            let q = try UploadQueue(path: path)
            try q.enqueueRecording(idempotencyKey: "k1", payload: Data("{\"a\":1}".utf8),
                                   assets: [QueuedAsset(sha256: "aa", path: dir + "/aa.jpg", contentType: "image/jpeg", bytes: 3)],
                                   now: t0)
            try q.enqueueRecording(idempotencyKey: "k2", payload: Data("{}".utf8),
                                   assets: [QueuedAsset(sha256: "aa", path: dir + "/aa.jpg", contentType: "image/jpeg", bytes: 3)],
                                   now: t0.addingTimeInterval(1))
            // Duplicate enqueue with the same key is ignored.
            try q.enqueueRecording(idempotencyKey: "k1", payload: Data("{\"a\":2}".utf8), assets: [], now: t0)
            XCTAssertEqual(try q.pendingCount(), 2)
        }
        // Reopen: survives "restart".
        let q = try UploadQueue(path: path)
        let first = try q.nextDueRecording(now: t0.addingTimeInterval(5))
        XCTAssertEqual(first?.idempotencyKey, "k1")
        XCTAssertEqual(first.map { String(decoding: $0.payload, as: UTF8.self) }, "{\"a\":1}")
        XCTAssertEqual(try q.pendingAssets(for: "k1").map { $0.sha256 }, ["aa"])

        try q.recordFailure(idempotencyKey: "k1", error: "boom", nextAttemptAt: t0.addingTimeInterval(100))
        XCTAssertEqual(try q.nextDueRecording(now: t0.addingTimeInterval(5))?.idempotencyKey, "k2")
        XCTAssertEqual(try q.earliestNextAttempt(), Date(timeIntervalSince1970: 0)) // k2 still at 0

        try q.markAssetUploaded(sha256: "aa", idempotencyKey: "k2")
        XCTAssertEqual(try q.pendingAssets(for: "k2").count, 0)
        // aa.jpg still referenced by k1 -> not orphaned.
        XCTAssertEqual(try q.completeRecording(idempotencyKey: "k2"), [])
        XCTAssertEqual(try q.completeRecording(idempotencyKey: "k1"), [dir + "/aa.jpg"])
        XCTAssertEqual(try q.pendingCount(), 0)
        XCTAssertNil(try q.earliestNextAttempt())
    }

    func testDeadAndRetry() throws {
        let q = try UploadQueue(path: dir + "/q2.sqlite")
        try q.enqueueRecording(idempotencyKey: "k", payload: Data(), assets: [])
        try q.recordFailure(idempotencyKey: "k", error: "bad", nextAttemptAt: Date(), dead: true)
        XCTAssertEqual(try q.pendingCount(), 0)
        XCTAssertEqual(try q.deadCount(), 1)
        try q.retryNow(includeDead: true)
        XCTAssertEqual(try q.pendingCount(), 1)
        XCTAssertEqual(try q.nextDueRecording()?.payload, Data())
    }

    func testBackoff() {
        let b = Backoff(base: 5, cap: 60, jitter: 0.5)
        XCTAssertEqual(b.delay(attempt: 1, random: 1), 5)
        XCTAssertEqual(b.delay(attempt: 1, random: 0), 2.5)
        XCTAssertEqual(b.delay(attempt: 3, random: 1), 20)
        XCTAssertEqual(b.delay(attempt: 50, random: 1), 60)
        let d = b.delay(attempt: 2)
        XCTAssertTrue(d >= 5 && d <= 10)
    }

    // MARK: Uploader with a fake server

    final class FakeTransport: HTTPTransport {
        var log: [String] = []
        var headers: [[String: String]] = []
        var failRecordingsWith: Int?
        var assetExists = false
        let lock = NSLock()

        func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
            let method = request.httpMethod ?? "GET"
            let path = request.url?.path ?? ""
            lock.lock()
            log.append("\(method) \(request.url?.absoluteString ?? "")")
            headers.append(request.allHTTPHeaderFields ?? [:])
            lock.unlock()
            func reply(_ status: Int, _ body: String) -> (Data, HTTPURLResponse) {
                let resp = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
                return (Data(body.utf8), resp)
            }
            switch (method, path) {
            case ("POST", "/api/v1/assets/presign"):
                if assetExists { return reply(200, #"{"asset_id":"a1","exists":true,"upload":null}"#) }
                return reply(200, #"{"asset_id":"a1","exists":false,"upload":{"method":"PUT","url":"/api/v1/assets/upload/a1","headers":{}}}"#)
            case ("PUT", "/api/v1/assets/upload/a1"):
                return reply(200, "{}")
            case ("POST", "/api/v1/recordings"):
                if let s = failRecordingsWith {
                    return reply(s, #"{"error":{"code":"oops","message":"nope"}}"#)
                }
                return reply(202, #"{"id":"r1","status":"received"}"#)
            default:
                return reply(404, #"{"error":{"code":"not_found","message":"x"}}"#)
            }
        }
    }

    func makeUploader(_ t: FakeTransport) throws -> (Uploader, UploadQueue, String) {
        let q = try UploadQueue(path: dir + "/u.sqlite")
        let img = dir + "/img.jpg"
        FileManager.default.createFile(atPath: img, contents: Data([1, 2, 3]), attributes: nil)
        try q.enqueueRecording(idempotencyKey: "idem-1", payload: Data(#"{"events":[]}"#.utf8),
                               assets: [QueuedAsset(sha256: "ff", path: img, contentType: "image/jpeg", bytes: 3)])
        let api = APIClient(baseURL: URL(string: "https://ws.example.com"), transport: t, tokenProvider: { "tok" })
        return (Uploader(queue: q, api: api, backoff: Backoff(base: 1, cap: 2, jitter: 0)), q, img)
    }

    func testUploaderHappyPath() async throws {
        let t = FakeTransport()
        let (u, q, img) = try makeUploader(t)
        let r = await u.drain()
        XCTAssertEqual(r.uploaded, 1)
        XCTAssertEqual(t.log, [
            "POST https://ws.example.com/api/v1/assets/presign",
            "PUT https://ws.example.com/api/v1/assets/upload/a1",
            "POST https://ws.example.com/api/v1/recordings",
        ])
        XCTAssertEqual(t.headers[2]["Idempotency-Key"], "idem-1")
        XCTAssertEqual(t.headers[1]["Authorization"], "Bearer tok") // same-origin upload gets bearer
        XCTAssertEqual(try q.pendingCount(), 0)
        XCTAssertFalse(FileManager.default.fileExists(atPath: img))
    }

    func testUploaderSkipsExistingAsset() async throws {
        let t = FakeTransport()
        t.assetExists = true
        let (u, _, _) = try makeUploader(t)
        _ = await u.drain()
        XCTAssertEqual(t.log.count, 2)
        XCTAssertFalse(t.log.contains { $0.hasPrefix("PUT") })
    }

    func testUploaderBacksOffOnServerError() async throws {
        let t = FakeTransport()
        t.failRecordingsWith = 503
        let (u, q, img) = try makeUploader(t)
        let r = await u.drain()
        XCTAssertEqual(r.failed, 1)
        XCTAssertEqual(try q.pendingCount(), 1)
        XCTAssertNil(try q.nextDueRecording()) // backing off
        XCTAssertTrue(FileManager.default.fileExists(atPath: img))
        // Asset was marked uploaded, so the retry only re-POSTs the recording with the same key.
        t.failRecordingsWith = nil
        try q.retryNow()
        _ = await u.drain()
        XCTAssertEqual(try q.pendingCount(), 0)
        XCTAssertEqual(t.log.filter { $0.hasPrefix("POST https://ws.example.com/api/v1/recordings") }.count, 2)
        XCTAssertEqual(Set(t.headers.compactMap { $0["Idempotency-Key"] }), ["idem-1"])
    }

    func testUploaderStopsOnAuthFailure() async throws {
        let t = FakeTransport()
        t.failRecordingsWith = 401
        let (u, q, _) = try makeUploader(t)
        _ = await u.drain()
        XCTAssertTrue(u.currentStatus.needsSignIn)
        XCTAssertEqual(try q.pendingCount(), 1)
    }

    func testUploaderParksPermanentFailures() async throws {
        let t = FakeTransport()
        t.failRecordingsWith = 422
        let (u, q, _) = try makeUploader(t)
        u.maxPermanentFailures = 2
        _ = await u.drain()
        try q.retryNow()
        _ = await u.drain()
        XCTAssertEqual(try q.pendingCount(), 0)
        XCTAssertEqual(try q.deadCount(), 1)
    }

    func testPresignedS3URLGetsNoBearer() async throws {
        let t = FakeTransport()
        let api = APIClient(baseURL: URL(string: "https://ws.example.com"), transport: t, tokenProvider: { "tok" })
        try? await api.upload(UploadInstruction(method: "PUT", url: "https://bucket.s3.amazonaws.com/x?sig=1",
                                                headers: ["x-amz-acl": "private"]),
                              data: Data([1]), contentType: "image/jpeg")
        XCTAssertNil(t.headers.last?["Authorization"])
        XCTAssertEqual(t.headers.last?["Content-Type"], "image/jpeg")
    }

    func testBaseURLNormalization() {
        XCTAssertEqual(APIClient.normalizeBaseURL("https://ws.example.com/api/v1/")?.absoluteString, "https://ws.example.com")
        XCTAssertEqual(APIClient.normalizeBaseURL(" http://localhost:8000 ")?.absoluteString, "http://localhost:8000")
        XCTAssertNil(APIClient.normalizeBaseURL("ftp://x"))
        XCTAssertNil(APIClient.normalizeBaseURL("not a url"))
    }
}
