import XCTest
@testable import DotCore

final class EventCleanupTests: XCTestCase {
    let t0 = Date(timeIntervalSince1970: 1_790_000_000)
    let chrome = AppInfo(bundleID: "com.google.Chrome", name: "Google Chrome")
    let dot = AppInfo(bundleID: "com.workshadower.dot", name: "Dot")
    let field = ElementInfo(role: "AXTextField", label: "Policy number", path: ["AXWindow:Claims", "AXTextField:Policy number"], valueKind: .text)

    func ev(_ dt: Double, _ type: EventType, app: AppInfo? = nil, element: ElementInfo? = nil,
            text: String? = nil, key: String? = nil, url: String? = nil, title: String? = nil) -> RecordedEvent {
        return RecordedEvent(ts: t0.addingTimeInterval(dt), type: type, app: app ?? chrome,
                             window: title.map { WindowInfo(title: $0) }, element: element, text: text, key: key, url: url)
    }

    func testMergesTypingAndRenumbers() {
        let raw = [
            ev(0, .appActivate),
            ev(1, .type, element: field, text: "P-12"),
            ev(2, .type, element: field, text: "3456"),
            ev(3, .key, key: "Command+S"),
        ]
        let out = EventCleaner().clean(raw)
        XCTAssertEqual(out.map { $0.type }, [.appActivate, .type, .key])
        XCTAssertEqual(out[1].text, "P-123456")
        XCTAssertEqual(out[2].key, "cmd+s")
        XCTAssertEqual(out.map { $0.seq }, [1, 2, 3])
    }

    func testRedactsAfterMergeSoSplitPIIIsCaught() {
        let raw = [
            ev(1, .type, element: field, text: "bob@exa"),
            ev(2, .type, element: field, text: "mple.com"),
        ]
        let out = EventCleaner().clean(raw)
        XCTAssertEqual(out.count, 1)
        XCTAssertEqual(out[0].text, "[REDACTED:email]")
    }

    func testDropsSecureAndEmptyTyping() {
        let secure = ElementInfo(role: "AXTextField", subrole: "AXSecureTextField", label: "Password", valueKind: .secure)
        let raw = [
            ev(1, .type, element: secure, text: "hunter2"),
            ev(2, .type, element: field, text: ""),
            ev(3, .click, element: ElementInfo(role: "AXButton", label: "Sign in")),
        ]
        let out = EventCleaner().clean(raw)
        XCTAssertEqual(out.map { $0.type }, [.click])
        XCTAssertFalse(out.contains { $0.text == "hunter2" })
    }

    func testIgnoresDotAndDuplicateActivations() {
        let raw = [
            ev(0, .appActivate),
            ev(0.5, .appActivate),
            ev(1, .click, app: dot, element: ElementInfo(role: "AXButton", label: "Dot")),
            ev(2, .urlChange, url: "https://a.com/x?id=1"),
            ev(3, .urlChange, url: "https://a.com/x?id=2"),
            ev(4, .urlChange, url: "https://a.com/y"),
        ]
        let out = EventCleaner(ignoredBundleIDs: ["com.workshadower.dot"]).clean(raw)
        XCTAssertEqual(out.map { $0.type }, [.appActivate, .urlChange, .urlChange])
        XCTAssertEqual(out[1].url, "https://a.com/x")
        XCTAssertEqual(out[2].url, "https://a.com/y")
    }

    func testSortsByTimestampAndCaps() {
        var raw: [RecordedEvent] = []
        for i in (0..<10).reversed() {
            raw.append(ev(Double(i), .click, element: ElementInfo(role: "AXButton", label: "B\(i)")))
        }
        let out = EventCleaner(maxEvents: 5).clean(raw)
        XCTAssertEqual(out.count, 5)
        XCTAssertEqual(out.first?.element?.label, "B0")
        XCTAssertEqual(out.last?.seq, 5)
    }

    func testTypingBuffer() {
        var b = TypingBuffer()
        XCTAssertNil(b.focus(elementKey: "a", element: field))
        b.append("hel", at: t0)
        b.append("lx", at: t0)
        b.backspace()
        b.append("o", at: t0)
        let flushed = b.focus(elementKey: "b", element: field)
        XCTAssertEqual(flushed?.text, "hello")
        XCTAssertNil(b.flush())
    }

    func testTypingBufferNeverStoresSecure() {
        var b = TypingBuffer()
        let secure = ElementInfo(role: "AXTextField", subrole: "AXSecureTextField", valueKind: .secure)
        _ = b.focus(elementKey: "pw", element: secure)
        b.append("hunter2", at: t0)
        XCTAssertTrue(b.isEmpty)
        XCTAssertNil(b.flush())
    }

    func testKeyCombo() {
        XCTAssertEqual(KeyCombo.parse("cmd+s")?.string, "cmd+s")
        XCTAssertEqual(KeyCombo.parse("Shift+Command+S")?.string, "cmd+shift+s")
        XCTAssertEqual(KeyCombo.parse("⌘⇧K")?.string, "cmd+shift+k")
        XCTAssertEqual(KeyCombo.parse("ctrl+alt+Delete")?.string, "ctrl+alt+delete")
        XCTAssertEqual(KeyCombo.parse("Return")?.string, "enter")
        XCTAssertNil(KeyCombo.parse("cmd+shift"))
        XCTAssertEqual(KeyCodes.code(for: "s"), 0x01)
        XCTAssertEqual(KeyCodes.code(for: "Return"), 0x24)
        XCTAssertEqual(KeyCodes.namedKey(for: 0x30), "tab")
    }

    func testKeyMoments() {
        XCTAssertTrue(KeyMoments.shouldScreenshotBeforeClick(role: "AXButton", label: "Submit claim"))
        XCTAssertTrue(KeyMoments.shouldScreenshotBeforeClick(role: "AXButton", label: "Save"))
        XCTAssertTrue(KeyMoments.shouldScreenshotBeforeClick(role: "AXMenuItem", label: "Delete…"))
        XCTAssertFalse(KeyMoments.shouldScreenshotBeforeClick(role: "AXButton", label: "Saved searches tab"))
        XCTAssertFalse(KeyMoments.shouldScreenshotBeforeClick(role: "AXStaticText", label: "Submit"))
        XCTAssertFalse(KeyMoments.shouldScreenshotBeforeClick(role: "AXButton", label: "Unsubscribe"))
    }

    func testEventJSONShape() throws {
        let e = RecordedEvent(seq: 1, ts: Date(timeIntervalSince1970: 1_790_000_000.123), type: .click,
                              app: chrome, window: WindowInfo(title: "Claims"),
                              element: ElementInfo(role: "AXButton", label: "Submit", identifier: "submitBtn",
                                                   path: ["AXWindow:Claims"], valueKind: .none),
                              screenshotSHA256: "ab")
        let data = try DotJSON.encoder().encode(e)
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        XCTAssertEqual(obj?["type"] as? String, "click")
        XCTAssertEqual(obj?["screenshot_sha256"] as? String, "ab")
        XCTAssertEqual((obj?["app"] as? [String: Any])?["bundle_id"] as? String, "com.google.Chrome")
        XCTAssertEqual((obj?["element"] as? [String: Any])?["value_kind"] as? String, "none")
        XCTAssertEqual(obj?["ts"] as? String, "2026-09-21T14:13:20.123Z")
        XCTAssertNil(obj?["text"])
        let back = try DotJSON.decoder().decode(RecordedEvent.self, from: data)
        XCTAssertEqual(back.element?.identifier, "submitBtn")
    }
}
