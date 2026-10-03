import XCTest
@testable import DotCore

final class TargetMatcherTests: XCTestCase {
    func node(_ role: String, _ label: String?, id: String? = nil, path: [String] = []) -> UINode {
        return UINode(role: role, label: label, identifier: id, path: path)
    }

    func testIdentifierBeatsLabel() {
        let target = Target(role: "AXButton", label: "Submit claim", identifier: "submitBtn",
                            path: ["AXWindow:Claims", "AXGroup", "AXButton:Submit claim"])
        let nodes = [
            node("AXButton", "Submit claim", path: ["AXWindow:Claims", "AXGroup", "AXButton:Submit claim"]),
            node("AXButton", "Send", id: "submitBtn", path: ["AXWindow:Claims", "AXGroup", "AXButton:Send"]),
        ]
        XCTAssertEqual(TargetMatcher.best(target: target, nodes: nodes)?.index, 1)
    }

    func testRoleLabelExactBeatsFuzzy() {
        let target = Target(role: "AXButton", label: "Save")
        let nodes = [node("AXButton", "Save draft"), node("AXButton", "save"), node("AXStaticText", "Save")]
        let best = TargetMatcher.best(target: target, nodes: nodes)
        XCTAssertEqual(best?.index, 1)
        XCTAssertGreaterThanOrEqual(best?.score ?? 0, 0.8)
    }

    func testFuzzyLabelMatches() {
        let target = Target(role: "AXButton", label: "Submit claim")
        let nodes = [node("AXButton", "Cancel"), node("AXButton", "Submit Claim…"), node("AXButton", "Submit a claim")]
        // "Submit Claim…" normalizes to an exact match.
        XCTAssertEqual(TargetMatcher.best(target: target, nodes: nodes)?.index, 1)

        let renamed = [node("AXButton", "Cancel"), node("AXButton", "Submit new claim")]
        let m = TargetMatcher.best(target: target, nodes: renamed)
        XCTAssertEqual(m?.index, 1)
        XCTAssertLessThan(m?.score ?? 1, 0.8)
    }

    func testPathDisambiguatesDuplicateLabels() {
        let target = Target(role: "AXButton", label: "OK", path: ["AXWindow:Claims", "AXSheet", "AXButton:OK"])
        let nodes = [
            node("AXButton", "OK", path: ["AXWindow:Claims", "AXGroup", "AXButton:OK"]),
            node("AXButton", "OK", path: ["AXWindow:Claims", "AXSheet", "AXButton:OK"]),
        ]
        XCTAssertEqual(TargetMatcher.best(target: target, nodes: nodes)?.index, 1)
    }

    func testAmbiguousTieReturnsNil() {
        let target = Target(role: "AXButton", label: "OK")
        let nodes = [
            node("AXButton", "OK", path: ["AXWindow:A", "AXGroup:1", "AXButton:OK"]),
            node("AXButton", "OK", path: ["AXWindow:A", "AXGroup:2", "AXButton:OK"]),
        ]
        XCTAssertNil(TargetMatcher.best(target: target, nodes: nodes))
    }

    func testNothingCloseEnough() {
        let target = Target(role: "AXButton", label: "Submit claim")
        let nodes = [node("AXButton", "Cancel"), node("AXTextField", "Search")]
        XCTAssertNil(TargetMatcher.best(target: target, nodes: nodes))
        XCTAssertNil(TargetMatcher.best(target: Target(), nodes: nodes))
    }

    func testSimilarityHelpers() {
        XCTAssertEqual(TargetMatcher.labelSimilarity("Submit", "submit"), 1)
        XCTAssertEqual(TargetMatcher.pathSimilarity(["AXWindow:A", "AXButton:OK"], ["AXWindow:A", "AXButton:OK"]), 1)
        XCTAssertEqual(TargetMatcher.pathSimilarity([], ["x"]), 0)
        XCTAssertEqual(TargetMatcher.levenshtein(Array("kitten"), Array("sitting")), 3)
    }
}
