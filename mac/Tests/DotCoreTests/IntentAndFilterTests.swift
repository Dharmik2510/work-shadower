import XCTest
@testable import DotCore

/// "What did you just do?" plumbing (payload + held queue items) and excluded steps in skills.
final class IntentAndFilterTests: XCTestCase {
    var dir: String!

    override func setUp() {
        super.setUp()
        dir = NSTemporaryDirectory() + "dotcore-intent-" + UUID().uuidString
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true, attributes: nil)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(atPath: dir)
        super.tearDown()
    }

    func testCleanIntentTrimsCollapsesAndRedacts() {
        XCTAssertNil(RecordingPayload.cleanIntent(nil))
        XCTAssertNil(RecordingPayload.cleanIntent("   \n "))
        XCTAssertEqual(RecordingPayload.cleanIntent("  Added a driver \n to   policy "), "Added a driver to policy")
        XCTAssertEqual(RecordingPayload.cleanIntent("Emailed jane@corp.example.com the claim"),
                       "Emailed [REDACTED:email] the claim")
        XCTAssertEqual(RecordingPayload.cleanIntent(String(repeating: "a", count: 1500))?.count, 1000)
    }

    func testPayloadEncodesIntent() throws {
        let p = RecordingPayload(titleHint: "Workflow in Chrome", startedAt: Date(timeIntervalSince1970: 0),
                                 endedAt: Date(timeIntervalSince1970: 60),
                                 client: ClientInfo(appVersion: "1", osVersion: "15", deviceID: "d"), events: [],
                                 intent: "Booked a rental car")
        let json = try JSONSerialization.jsonObject(with: try DotJSON.encoder().encode(p)) as? [String: Any]
        XCTAssertEqual(json?["intent"] as? String, "Booked a rental car")
        let back = try DotJSON.decoder().decode(RecordingPayload.self, from: try DotJSON.encoder().encode(p))
        XCTAssertEqual(back.intent, "Booked a rental car")
    }

    func testHeldRecordingWaitsThenUpdatesAndReleases() throws {
        let q = try UploadQueue(path: dir + "/q.sqlite")
        let now = Date()
        try q.enqueueRecording(idempotencyKey: "k", payload: Data("{\"a\":1}".utf8), assets: [], now: now,
                               holdUntil: now.addingTimeInterval(150))
        XCTAssertEqual(try q.pendingCount(), 1)
        XCTAssertNil(try q.nextDueRecording(now: now.addingTimeInterval(10)))  // held
        XCTAssertNotNil(try q.nextDueRecording(now: now.addingTimeInterval(151)))  // hold expires on its own

        XCTAssertTrue(try q.updatePayload(idempotencyKey: "k", payload: Data("{\"a\":2}".utf8)))
        try q.release(idempotencyKey: "k")
        let due = try q.nextDueRecording(now: now)
        XCTAssertEqual(due.map { String(decoding: $0.payload, as: UTF8.self) }, "{\"a\":2}")

        // Once an upload attempt happened, the payload is frozen.
        try q.recordFailure(idempotencyKey: "k", error: "x", nextAttemptAt: now)
        XCTAssertFalse(try q.updatePayload(idempotencyKey: "k", payload: Data("{\"a\":3}".utf8)))
        XCTAssertFalse(try q.updatePayload(idempotencyKey: "missing", payload: Data()))
        XCTAssertEqual(try q.payload(idempotencyKey: "k").map { String(decoding: $0, as: UTF8.self) }, "{\"a\":2}")
    }

    func testExcludedStepsAreDecodedAndSkipped() throws {
        let json = """
        {"title": "T", "steps": [
          {"index": 1, "title": "A", "instruction": "a", "irreversible": false},
          {"index": 2, "title": "Slack detour", "instruction": "b", "excluded": true,
           "filter": {"decision": "drop", "reason": "detour", "p_drop": 0.95, "source": "jev"}, "source_seqs": [6]},
          {"index": 3, "title": "C", "instruction": "c"}
        ]}
        """
        let c = try DotJSON.decoder().decode(SkillContent.self, from: Data(json.utf8))
        XCTAssertEqual(c.steps.count, 3)
        XCTAssertEqual(c.steps.map { $0.excluded }, [false, true, false])
        XCTAssertEqual(c.runnableSteps.map { $0.title }, ["A", "C"])
    }
}
