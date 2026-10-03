import Foundation

/// `{{name}}` placeholder handling for skill inputs. Whitespace inside the
/// braces is allowed (`{{ policy_number }}`); names are `[A-Za-z_][A-Za-z0-9_.-]*`.
public enum Template {
    private static let regex: NSRegularExpression = {
        // Constant pattern; force-try is safe.
        return try! NSRegularExpression(pattern: #"\{\{\s*([A-Za-z_][A-Za-z0-9_.\-]*)\s*\}\}"#, options: [])
    }()

    /// Placeholder names in order of first appearance.
    public static func placeholders(in text: String?) -> [String] {
        guard let text = text, !text.isEmpty else { return [] }
        let ns = text as NSString
        var out: [String] = []
        for m in regex.matches(in: text, options: [], range: NSRange(location: 0, length: ns.length)) {
            let name = ns.substring(with: m.range(at: 1))
            if !out.contains(name) { out.append(name) }
        }
        return out
    }

    /// Fills placeholders. Unknown names are left untouched and reported.
    public static func fill(_ text: String, with values: [String: String]) -> (text: String, missing: [String]) {
        let ns = text as NSString
        let matches = regex.matches(in: text, options: [], range: NSRange(location: 0, length: ns.length))
        guard !matches.isEmpty else { return (text, []) }
        var result = ""
        var cursor = 0
        var missing: [String] = []
        for m in matches {
            result += ns.substring(with: NSRange(location: cursor, length: m.range.location - cursor))
            let name = ns.substring(with: m.range(at: 1))
            if let v = values[name] {
                result += v
            } else {
                result += ns.substring(with: m.range)
                if !missing.contains(name) { missing.append(name) }
            }
            cursor = m.range.location + m.range.length
        }
        result += ns.substring(from: cursor)
        return (result, missing)
    }

    public static func filled(_ text: String?, _ values: [String: String]) -> String? {
        guard let text = text else { return nil }
        return fill(text, with: values).text
    }

    /// All input names a skill needs: declared inputs first, then any
    /// undeclared placeholders found in steps.
    public static func requiredInputs(for content: SkillContent) -> [SkillInput] {
        var inputs = content.inputs
        var names = Set(inputs.map { $0.name })
        for step in content.steps {
            var texts: [String?] = [step.title, step.instruction, step.action?.text, step.action?.url,
                                    step.action?.key, step.action?.target?.label, step.expect?.windowTitleContains,
                                    step.expect?.elementPresent?.label]
            texts.append(step.action?.target?.windowTitle)
            for t in texts {
                for name in placeholders(in: t) where !names.contains(name) {
                    names.insert(name)
                    inputs.append(SkillInput(name: name, description: nil, example: nil))
                }
            }
        }
        return inputs
    }

    /// Returns a copy of the step with every text field filled.
    public static func fill(step: SkillStep, with values: [String: String]) -> SkillStep {
        var s = step
        s.title = fill(s.title, with: values).text
        s.instruction = fill(s.instruction, with: values).text
        if var a = s.action {
            a.text = filled(a.text, values)
            a.url = filled(a.url, values)
            a.key = filled(a.key, values)
            if var t = a.target {
                t.label = filled(t.label, values)
                t.windowTitle = filled(t.windowTitle, values)
                a.target = t
            }
            s.action = a
        }
        if var e = s.expect {
            e.windowTitleContains = filled(e.windowTitleContains, values)
            if var p = e.elementPresent {
                p.label = filled(p.label, values)
                e.elementPresent = p
            }
            s.expect = e
        }
        return s
    }
}
