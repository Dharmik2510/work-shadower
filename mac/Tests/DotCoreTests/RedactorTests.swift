import XCTest
@testable import DotCore

final class RedactorTests: XCTestCase {
    let r = Redactor()

    func testEmail() {
        let out = r.redact("Contact dharmik.s+test@intact.ca now")
        XCTAssertEqual(out.text, "Contact [REDACTED:email] now")
        XCTAssertEqual(out.kinds, [.email])
    }

    func testCardPlainAndGrouped() {
        XCTAssertEqual(r.redact("card 4111111111111111").text, "card [REDACTED:card]")
        XCTAssertEqual(r.redact("4111 1111 1111 1111 exp").text, "[REDACTED:card] exp")
        XCTAssertEqual(r.redact("x 4111-1111-1111-1111").text, "x [REDACTED:card]")
        // 19 digits still a card
        XCTAssertEqual(r.redact("6011000990139424123").text, "[REDACTED:card]")
    }

    func testSIN() {
        XCTAssertEqual(r.redact("SIN 046 454 286").text, "SIN [REDACTED:sin]")
        XCTAssertEqual(r.redact("SIN 046-454-286.").text, "SIN [REDACTED:sin].")
        XCTAssertEqual(r.redact("046454286").text, "[REDACTED:sin]")
    }

    func testPhone() {
        XCTAssertEqual(r.redact("call 416-555-1234").text, "call [REDACTED:phone]")
        XCTAssertEqual(r.redact("call (416) 555-1234").text, "call [REDACTED:phone]")
        XCTAssertEqual(r.redact("+1 416 555 1234").text, "[REDACTED:phone]")
        XCTAssertEqual(r.redact("+44 20 7946 0958 office").text, "[REDACTED:phone] office")
    }

    func testDoesNotTouchOrdinaryText() {
        let s = "Policy P-123456 renewed on 2026-10-03 for $1,200.50 (claim #42)"
        XCTAssertEqual(r.redact(s).text, s)
        XCTAssertFalse(r.containsPII(s))
    }

    func testSecureEventDropsText() {
        let el = ElementInfo(role: "AXTextField", subrole: "AXSecureTextField", label: "Password", valueKind: .text)
        let e = RecordedEvent(ts: Date(), type: .type, element: el, text: "hunter2")
        let out = r.redact(event: e)
        XCTAssertNil(out.text)
        XCTAssertEqual(out.element?.valueKind, .secure)
    }

    func testEventFieldsRedacted() {
        let el = ElementInfo(role: "AXStaticText", label: "Mail to bob@example.com",
                             path: ["AXWindow:Inbox - bob@example.com", "AXGroup"])
        let e = RecordedEvent(ts: Date(), type: .click, window: WindowInfo(title: "bob@example.com - Outlook"),
                              element: el, text: "my sin is 046 454 286",
                              url: "https://mail.example.com/u/bob@example.com?token=abc#x")
        let out = r.redact(event: e)
        XCTAssertEqual(out.text, "my sin is [REDACTED:sin]")
        XCTAssertEqual(out.window?.title, "[REDACTED:email] - Outlook")
        XCTAssertEqual(out.element?.label, "Mail to [REDACTED:email]")
        XCTAssertEqual(out.element?.path.first, "AXWindow:Inbox - [REDACTED:email]")
        XCTAssertEqual(out.url, "https://mail.example.com/u/[REDACTED:email]")
    }

    func testURLSanitizer() {
        XCTAssertEqual(URLSanitizer.strip("https://a.com/x/y?q=1&b=2#frag"), "https://a.com/x/y")
        XCTAssertEqual(URLSanitizer.strip("https://user:pw@a.com/p"), "https://a.com/p")
        XCTAssertEqual(URLSanitizer.strip("a.com/search?q=secret"), "a.com/search")
        XCTAssertTrue(URLSanitizer.isSafeToOpen("https://claims.intact.net/x"))
        XCTAssertFalse(URLSanitizer.isSafeToOpen("file:///etc/passwd"))
        XCTAssertFalse(URLSanitizer.isSafeToOpen("workshadower://run?skill_id=1"))
        XCTAssertFalse(URLSanitizer.isSafeToOpen("javascript:alert(1)"))
    }
}
