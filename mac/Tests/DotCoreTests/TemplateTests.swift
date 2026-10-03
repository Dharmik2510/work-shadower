import XCTest
@testable import DotCore

final class TemplateTests: XCTestCase {
    func testPlaceholders() {
        XCTAssertEqual(Template.placeholders(in: "File {{policy_number}} for {{ claimant }} ({{policy_number}})"),
                       ["policy_number", "claimant"])
        XCTAssertEqual(Template.placeholders(in: "no vars {single}"), [])
        XCTAssertEqual(Template.placeholders(in: nil), [])
    }

    func testFill() {
        let r = Template.fill("Policy {{ policy_number }} / {{unknown}}", with: ["policy_number": "P-1"])
        XCTAssertEqual(r.text, "Policy P-1 / {{unknown}}")
        XCTAssertEqual(r.missing, ["unknown"])
    }

    func testFillDoesNotReinterpretValues() {
        // A value that itself looks like a placeholder must not be expanded again.
        let r = Template.fill("{{a}} {{b}}", with: ["a": "{{b}}", "b": "x"])
        XCTAssertEqual(r.text, "{{b}} x")
    }

    func testFillUnicode() {
        let r = Template.fill("Bonjour {{nom}} — ça va? {{nom}}", with: ["nom": "Zoë"])
        XCTAssertEqual(r.text, "Bonjour Zoë — ça va? Zoë")
    }

    func testRequiredInputsIncludesUndeclared() {
        let content = SkillContent(title: "t", inputs: [SkillInput(name: "policy_number")], steps: [
            SkillStep(index: 1, title: "Type", instruction: "Type {{policy_number}}",
                      action: StepAction(type: .type, text: "{{policy_number}}")),
            SkillStep(index: 2, title: "Open", instruction: "Open {{claim_url}}",
                      action: StepAction(type: .openURL, url: "{{claim_url}}")),
        ])
        XCTAssertEqual(Template.requiredInputs(for: content).map { $0.name }, ["policy_number", "claim_url"])
    }

    func testFillStep() {
        let step = SkillStep(index: 1, title: "Search {{q}}", instruction: "Type {{q}}",
                             action: StepAction(type: .click, target: Target(role: "AXLink", label: "{{q}} result")),
                             expect: StepExpect(windowTitleContains: "{{q}}"))
        let f = Template.fill(step: step, with: ["q": "P-9"])
        XCTAssertEqual(f.title, "Search P-9")
        XCTAssertEqual(f.action?.target?.label, "P-9 result")
        XCTAssertEqual(f.expect?.windowTitleContains, "P-9")
    }

    func testDecodeContractSkillContent() throws {
        let json = """
        {
          "title": "Create a new auto claim",
          "goal": "One sentence",
          "apps": ["Google Chrome", "Outlook"],
          "prerequisites": ["Access to ClaimCenter"],
          "inputs": [{"name":"policy_number","description":"Policy to file against","example":"P-123456"}],
          "steps": [
            {
              "index": 1,
              "title": "Open ClaimCenter",
              "instruction": "Human-readable instruction, may reference {{policy_number}}",
              "app": "Google Chrome",
              "action": {
                "type": "click",
                "target": {"role":"AXButton","label":"Submit claim","identifier":null,"path":[],"window_title":"Claims"},
                "text": "{{policy_number}}",
                "key": "cmd+s",
                "url": "https://example.com"
              },
              "expect": {"window_title_contains": "Claim #", "element_present": {"role":"AXStaticText","label":"Saved"}},
              "screenshot_sha256": null,
              "irreversible": false
            },
            {"index": 2, "title": "Mystery", "instruction": "x", "action": {"type": "teleport"}, "irreversible": true}
          ],
          "tags": ["claims","onboarding"]
        }
        """
        let c = try DotJSON.decoder().decode(SkillContent.self, from: Data(json.utf8))
        XCTAssertEqual(c.steps.count, 2)
        XCTAssertEqual(c.steps[0].action?.target?.windowTitle, "Claims")
        XCTAssertNil(c.steps[0].action?.target?.identifier)
        XCTAssertEqual(c.steps[0].expect?.elementPresent?.label, "Saved")
        XCTAssertEqual(c.steps[1].action?.type, .unknown)
        XCTAssertTrue(c.steps[1].irreversible)
    }

    func testDecodeSparseContent() throws {
        let c = try DotJSON.decoder().decode(SkillContent.self, from: Data(#"{"steps":[{"title":"a"},{"title":"b"}]}"#.utf8))
        XCTAssertEqual(c.title, "Untitled skill")
        XCTAssertEqual(c.steps.map { $0.index }, [1, 2])
    }

    func testServerConfigFailsClosed() throws {
        let c = try DotJSON.decoder().decode(ServerConfig.self, from: Data(#"{"max_recording_minutes": 9999, "screenshot_policy": "everything"}"#.utf8))
        XCTAssertFalse(c.recordingEnabled)
        XCTAssertFalse(c.replayEnabled)
        XCTAssertEqual(c.maxRecordingMinutes, 240)
        XCTAssertEqual(c.screenshotPolicy, ScreenshotPolicy.none)
    }

    func testRunCreateKeepsInputKeys() throws {
        let body = RunCreate(skillID: "s", version: 2, mode: .guided, inputs: ["policyNumber": "x"])
        let obj = try JSONSerialization.jsonObject(with: try DotJSON.encoder().encode(body)) as? [String: Any]
        XCTAssertEqual(obj?["skill_id"] as? String, "s")
        XCTAssertEqual((obj?["inputs"] as? [String: String])?["policyNumber"], "x")
    }
}
