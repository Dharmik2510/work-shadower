#if os(macOS)
import AppKit
import ApplicationServices
import DotCore

/// Thin, defensive wrappers over the AXUIElement C API. The recorder and the
/// replayer both go through `AX.attributes(of:)` / `AX.label(...)` /
/// `AX.pathComponent(...)` so recorded targets and live nodes are described
/// identically.
enum AX {
    static let systemWide: AXUIElement = AXUIElementCreateSystemWide()

    /// Ancestors kept in an element path (plus the element itself).
    static let maxAncestors = 6
    static let maxLabelLength = 120

    static let textRoles: Set<String> = ["AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"]

    static func configureTimeouts() {
        // Applies to every element unless overridden; keeps a hung app from freezing us.
        AXUIElementSetMessagingTimeout(systemWide, 0.5)
    }

    // MARK: Attribute access

    static func attr(_ el: AXUIElement, _ name: String) -> CFTypeRef? {
        var value: CFTypeRef?
        let err = AXUIElementCopyAttributeValue(el, name as CFString, &value)
        return err == .success ? value : nil
    }

    static func string(_ el: AXUIElement, _ name: String) -> String? {
        guard let v = attr(el, name) else { return nil }
        if let s = v as? String { return s }
        if CFGetTypeID(v) == CFURLGetTypeID() { return (v as! URL).absoluteString }
        return nil
    }

    static func bool(_ el: AXUIElement, _ name: String) -> Bool? {
        guard let v = attr(el, name) else { return nil }
        if let n = v as? NSNumber { return n.boolValue }
        return nil
    }

    static func element(_ el: AXUIElement, _ name: String) -> AXUIElement? {
        guard let v = attr(el, name), CFGetTypeID(v) == AXUIElementGetTypeID() else { return nil }
        return (v as! AXUIElement)
    }

    static func elements(_ el: AXUIElement, _ name: String) -> [AXUIElement] {
        guard let v = attr(el, name) else { return [] }
        return (v as? [AXUIElement]) ?? []
    }

    static func pid(_ el: AXUIElement) -> pid_t? {
        var p: pid_t = 0
        return AXUIElementGetPid(el, &p) == .success ? p : nil
    }

    static func frame(_ el: AXUIElement) -> CGRect? {
        guard let pv = attr(el, "AXPosition"), let sv = attr(el, "AXSize"),
              CFGetTypeID(pv) == AXValueGetTypeID(), CFGetTypeID(sv) == AXValueGetTypeID() else { return nil }
        var pt = CGPoint.zero
        var size = CGSize.zero
        guard AXValueGetValue(pv as! AXValue, .cgPoint, &pt),
              AXValueGetValue(sv as! AXValue, .cgSize, &size) else { return nil }
        return CGRect(origin: pt, size: size)
    }

    // MARK: Batched node description

    struct Attrs {
        var role: String?
        var subrole: String?
        var title: String?
        var desc: String?
        var identifier: String?
        var placeholder: String?
        var children: [AXUIElement]
    }

    private static let batchNames = ["AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXIdentifier",
                                     "AXPlaceholderValue", "AXChildren"]

    /// One IPC round trip for the attributes we need per node.
    static func attributes(of el: AXUIElement, withChildren: Bool = true) -> Attrs {
        var out = Attrs(role: nil, subrole: nil, title: nil, desc: nil, identifier: nil, placeholder: nil, children: [])
        let names = withChildren ? batchNames : Array(batchNames.dropLast())
        var values: CFArray?
        let err = AXUIElementCopyMultipleAttributeValues(el, names as CFArray, AXCopyMultipleAttributeOptions(rawValue: 0), &values)
        guard err == .success, let arr = values as? [AnyObject], arr.count == names.count else {
            // Fallback: one call per attribute.
            out.role = string(el, "AXRole")
            out.subrole = string(el, "AXSubrole")
            out.title = string(el, "AXTitle")
            out.desc = string(el, "AXDescription")
            out.identifier = string(el, "AXIdentifier")
            out.placeholder = string(el, "AXPlaceholderValue")
            if withChildren { out.children = elements(el, "AXChildren") }
            return out
        }
        out.role = arr[0] as? String
        out.subrole = arr[1] as? String
        out.title = arr[2] as? String
        out.desc = arr[3] as? String
        out.identifier = arr[4] as? String
        out.placeholder = arr[5] as? String
        if withChildren { out.children = (arr[6] as? [AXUIElement]) ?? [] }
        return out
    }

    /// Human label: title > description > linked title element > placeholder >
    /// value (static text only — never the value of an editable field).
    static func label(_ el: AXUIElement, _ a: Attrs) -> String? {
        func clean(_ s: String?) -> String? {
            guard let s = s?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else { return nil }
            return String(s.prefix(maxLabelLength))
        }
        if let t = clean(a.title) { return t }
        if let d = clean(a.desc) { return d }
        if let role = a.role, textRoles.contains(role) || role == "AXPopUpButton" || role == "AXCheckBox" {
            if let titleEl = element(el, "AXTitleUIElement"), let t = clean(string(titleEl, "AXValue") ?? string(titleEl, "AXTitle")) {
                return t
            }
        }
        if let p = clean(a.placeholder) { return p }
        if a.role == "AXStaticText" || a.role == "AXHeading" {
            return clean(string(el, "AXValue"))
        }
        return nil
    }

    static func pathComponent(role: String?, label: String?) -> String {
        let r = role ?? "AXUnknown"
        if let l = label, !l.isEmpty { return "\(r):\(String(l.prefix(60)))" }
        return r
    }

    static func valueKind(role: String?, subrole: String?) -> ValueKind {
        if subrole == "AXSecureTextField" { return .secure }
        if let r = role, textRoles.contains(r) { return .text }
        return .none
    }

    /// Full description of a single element (used by the recorder on click/focus).
    static func describe(_ el: AXUIElement) -> ElementInfo {
        let a = attributes(of: el, withChildren: false)
        let lbl = label(el, a)
        var path = [pathComponent(role: a.role, label: lbl)]
        var cur = element(el, "AXParent")
        var hops = 0
        while let p = cur, hops < maxAncestors {
            let pa = attributes(of: p, withChildren: false)
            if pa.role == "AXApplication" { break }
            // Ancestors use title/description only (cheap, and never field values).
            let pl = (pa.title?.isEmpty == false ? pa.title : nil) ?? (pa.desc?.isEmpty == false ? pa.desc : nil)
            path.insert(pathComponent(role: pa.role, label: pl), at: 0)
            if pa.role == "AXWindow" { break }
            cur = element(p, "AXParent")
            hops += 1
        }
        return ElementInfo(role: a.role, subrole: a.subrole, label: lbl,
                           identifier: (a.identifier?.isEmpty == false) ? a.identifier : nil,
                           path: path, valueKind: valueKind(role: a.role, subrole: a.subrole))
    }

    static func windowTitle(containing el: AXUIElement) -> String? {
        if let w = element(el, "AXWindow") { return string(w, "AXTitle") }
        return nil
    }

    // MARK: App / window

    static func app(pid: pid_t) -> AXUIElement {
        return AXUIElementCreateApplication(pid)
    }

    static func focusedWindow(pid: pid_t) -> AXUIElement? {
        let a = app(pid: pid)
        return element(a, "AXFocusedWindow") ?? element(a, "AXMainWindow") ?? elements(a, "AXWindows").first
    }

    static func focusedElement() -> AXUIElement? {
        return element(systemWide, "AXFocusedUIElement")
    }

    static func element(at point: CGPoint) -> AXUIElement? {
        var el: AXUIElement?
        let err = AXUIElementCopyElementAtPosition(systemWide, Float(point.x), Float(point.y), &el)
        return err == .success ? el : nil
    }

    /// Chromium/Electron only build a full AX tree when asked.
    static func enableManualAccessibility(pid: pid_t) {
        let a = app(pid: pid)
        _ = AXUIElementSetAttributeValue(a, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    }

    // MARK: Actions

    static func press(_ el: AXUIElement) -> Bool {
        return AXUIElementPerformAction(el, "AXPress" as CFString) == .success
    }

    static func focus(_ el: AXUIElement) -> Bool {
        return AXUIElementSetAttributeValue(el, "AXFocused" as CFString, kCFBooleanTrue) == .success
    }

    static func setValue(_ el: AXUIElement, _ text: String) -> Bool {
        return AXUIElementSetAttributeValue(el, "AXValue" as CFString, text as CFTypeRef) == .success
    }

    static func raise(_ window: AXUIElement) {
        _ = AXUIElementPerformAction(window, "AXRaise" as CFString)
        _ = AXUIElementSetAttributeValue(window, "AXMain" as CFString, kCFBooleanTrue)
    }

    // MARK: Tree snapshot (bounded BFS)

    struct Node {
        let element: AXUIElement
        let ui: UINode
        let subrole: String?
    }

    /// Breadth-first walk from `root`, at most `maxNodes` nodes or until
    /// `timeout` elapses. Paths are root-relative and trimmed to the same
    /// shape the recorder produces (last `maxAncestors + 1` components).
    static func snapshot(root: AXUIElement, maxNodes: Int = 2000, timeout: TimeInterval = 1.5) -> [Node] {
        let deadline = Date().addingTimeInterval(timeout)
        var out: [Node] = []
        var queue: [(AXUIElement, [String])] = [(root, [])]
        var head = 0
        while head < queue.count && out.count < maxNodes && Date() < deadline {
            let (el, parentPath) = queue[head]
            head += 1
            let a = attributes(of: el)
            let lbl = label(el, a)
            // Ancestor components use title/description only, matching `describe`.
            let ancestorLabel = (a.title?.isEmpty == false ? a.title : nil) ?? (a.desc?.isEmpty == false ? a.desc : nil)
            let fullPath = parentPath + [pathComponent(role: a.role, label: lbl)]
            let path = Array(fullPath.suffix(maxAncestors + 1))
            out.append(Node(element: el,
                            ui: UINode(role: a.role, label: lbl,
                                       identifier: (a.identifier?.isEmpty == false) ? a.identifier : nil, path: path),
                            subrole: a.subrole))
            let childPrefix = Array((parentPath + [pathComponent(role: a.role, label: ancestorLabel)]).suffix(maxAncestors))
            for c in a.children where queue.count < maxNodes * 3 {
                queue.append((c, childPrefix))
            }
        }
        return out
    }

    static let actionableRoles: Set<String> = [
        "AXButton", "AXLink", "AXTextField", "AXTextArea", "AXComboBox", "AXSearchField", "AXCheckBox",
        "AXRadioButton", "AXPopUpButton", "AXMenuButton", "AXMenuItem", "AXTab", "AXTabGroup", "AXCell",
        "AXRow", "AXDisclosureTriangle", "AXSlider", "AXIncrementor", "AXStaticText", "AXHeading", "AXImage",
    ]

    /// Compact tree for `/replay/repair`: actionable/labelled nodes only, ≤ limit.
    static func compactTree(_ nodes: [Node], limit: Int = 300) -> [UINode] {
        var out: [UINode] = []
        for n in nodes {
            guard let role = n.ui.role, actionableRoles.contains(role) else { continue }
            if n.subrole == "AXSecureTextField" { continue }
            if (n.ui.label ?? "").isEmpty && (n.ui.identifier ?? "").isEmpty { continue }
            out.append(n.ui)
            if out.count >= limit { break }
        }
        return out
    }

    // MARK: Browsers

    static let browserBundleIDs: Set<String> = [
        "com.apple.Safari", "com.apple.SafariTechnologyPreview", "com.google.Chrome", "com.google.Chrome.canary",
        "com.microsoft.edgemac", "com.brave.Browser", "company.thebrowser.Browser", "com.vivaldi.Vivaldi",
        "org.chromium.Chromium", "com.operasoftware.Opera",
    ]

    static let chromiumBundleIDs: Set<String> = [
        "com.google.Chrome", "com.google.Chrome.canary", "com.microsoft.edgemac", "com.brave.Browser",
        "company.thebrowser.Browser", "com.vivaldi.Vivaldi", "org.chromium.Chromium", "com.operasoftware.Opera",
    ]

    /// Current page URL of a browser window (raw — caller strips/redacts).
    static func browserURL(window: AXUIElement) -> String? {
        // 1) Window AXDocument (Safari, some Chromium builds).
        if let doc = string(window, "AXDocument"), doc.hasPrefix("http") { return doc }
        // 2) AXURL on the first AXWebArea (bounded search).
        let nodes = snapshotForWebArea(root: window)
        if let web = nodes, let url = string(web, "AXURL"), url.hasPrefix("http") { return url }
        return nil
    }

    private static func snapshotForWebArea(root: AXUIElement) -> AXUIElement? {
        var queue: [AXUIElement] = [root]
        var head = 0
        let deadline = Date().addingTimeInterval(0.4)
        while head < queue.count && head < 400 && Date() < deadline {
            let el = queue[head]
            head += 1
            let role = string(el, "AXRole")
            if role == "AXWebArea" { return el }
            queue.append(contentsOf: elements(el, "AXChildren"))
        }
        return nil
    }
}
#endif
