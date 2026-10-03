import Foundation

/// Scores how well a live UI node matches a recorded step target.
///
/// Priority (CONTRACT replay ladder): identifier > role+label exact >
/// label fuzzy > path similarity. Scores are in 0...1:
///
/// | match                                   | score                     |
/// |-----------------------------------------|---------------------------|
/// | identifier equal (+ role agrees)        | 0.95 ... 1.0              |
/// | role + normalized label equal           | 0.80 ... 0.90             |
/// | label fuzzy (similarity >= 0.6)         | 0.45 ... 0.85             |
/// | role equal, no label, path similar      | up to 0.50                |
///
/// `best` accepts a candidate only above `threshold` (default 0.55) and
/// refuses ambiguous ties between different nodes with near-identical scores
/// unless path similarity separates them.
public enum TargetMatcher {
    public static let defaultThreshold = 0.55

    public static func normalizeLabel(_ s: String?) -> String {
        guard let s = s else { return "" }
        var t = s.lowercased()
        t = t.replacingOccurrences(of: "…", with: "")
        t = t.replacingOccurrences(of: "...", with: "")
        let allowed = t.unicodeScalars.map { CharacterSet.alphanumerics.contains($0) ? Character($0) : " " }
        t = String(allowed)
        return t.split(separator: " ").joined(separator: " ")
    }

    /// Levenshtein-based similarity in 0...1 on normalized strings, blended
    /// with token overlap so word reordering still scores well.
    public static func labelSimilarity(_ a: String?, _ b: String?) -> Double {
        let x = normalizeLabel(a), y = normalizeLabel(b)
        if x.isEmpty || y.isEmpty { return 0 }
        if x == y { return 1 }
        let ax = Array(x), ay = Array(y)
        let dist = levenshtein(ax, ay)
        let lev = 1 - Double(dist) / Double(max(ax.count, ay.count))
        let tx = Set(x.split(separator: " ")), ty = Set(y.split(separator: " "))
        let inter = Double(tx.intersection(ty).count)
        let union = Double(tx.union(ty).count)
        let jaccard = union > 0 ? inter / union : 0
        // One containing the other ("Save" vs "Save draft") is a decent hint.
        let contains = (x.contains(y) || y.contains(x)) ? 0.75 : 0
        return max(lev, jaccard, contains)
    }

    static func levenshtein(_ a: [Character], _ b: [Character]) -> Int {
        if a.isEmpty { return b.count }
        if b.isEmpty { return a.count }
        var prev = Array(0...b.count)
        var cur = [Int](repeating: 0, count: b.count + 1)
        for i in 1...a.count {
            cur[0] = i
            for j in 1...b.count {
                let cost = a[i - 1] == b[j - 1] ? 0 : 1
                cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            }
            swap(&prev, &cur)
        }
        return prev[b.count]
    }

    /// Path similarity: longest common subsequence of components, compared on
    /// role and normalized label, aligned from the leaf end, in 0...1.
    public static func pathSimilarity(_ a: [String], _ b: [String]) -> Double {
        if a.isEmpty || b.isEmpty { return 0 }
        let x = a.map(normalizeComponent), y = b.map(normalizeComponent)
        var dp = [[Int]](repeating: [Int](repeating: 0, count: y.count + 1), count: x.count + 1)
        for i in 1...x.count {
            for j in 1...y.count {
                dp[i][j] = x[i - 1] == y[j - 1] ? dp[i - 1][j - 1] + 1 : max(dp[i - 1][j], dp[i][j - 1])
            }
        }
        return Double(dp[x.count][y.count]) / Double(max(x.count, y.count))
    }

    static func normalizeComponent(_ c: String) -> String {
        // "AXButton:Submit claim" -> "axbutton:submit claim"
        if let i = c.firstIndex(of: ":") {
            let role = c[c.startIndex..<i].lowercased()
            let label = normalizeLabel(String(c[c.index(after: i)...]))
            return label.isEmpty ? role : role + ":" + label
        }
        return c.lowercased()
    }

    public static func score(target: Target, node: UINode) -> Double {
        let roleKnown = !(target.role ?? "").isEmpty
        let roleEq = roleKnown && target.role == node.role
        let rolePenalty = (roleKnown && !roleEq) ? 0.1 : 0
        let pathSim = pathSimilarity(target.path, node.path)

        if let tid = target.identifier, !tid.isEmpty, let nid = node.identifier, tid == nid {
            return min(1, 0.95 + (roleEq ? 0.03 : 0) + 0.02 * pathSim - rolePenalty)
        }

        let tl = normalizeLabel(target.label)
        let nl = normalizeLabel(node.label)
        if !tl.isEmpty && tl == nl {
            if roleEq || !roleKnown { return 0.8 + 0.1 * pathSim }
            return 0.6 + 0.1 * pathSim   // same label, different role (e.g. AXLink vs AXButton)
        }

        if !tl.isEmpty && !nl.isEmpty {
            let sim = labelSimilarity(tl, nl)
            if sim >= 0.6 {
                return max(0, 0.45 + 0.3 * (sim - 0.6) / 0.4 + (roleEq ? 0.05 : 0) + 0.05 * pathSim - rolePenalty)
            }
        }

        if tl.isEmpty && roleEq {
            return 0.5 * pathSim
        }
        return 0.15 * pathSim * (roleEq ? 1 : 0.5)
    }

    public struct Match: Equatable {
        public var index: Int
        public var score: Double
    }

    /// Best matching node index, or nil if nothing clears the threshold or the
    /// top two are indistinguishable.
    public static func best(target: Target, nodes: [UINode], threshold: Double = defaultThreshold) -> Match? {
        if target.isEmpty { return nil }
        var scored: [Match] = []
        for (i, n) in nodes.enumerated() {
            let s = score(target: target, node: n)
            if s >= threshold { scored.append(Match(index: i, score: s)) }
        }
        scored.sort { $0.score > $1.score }
        guard let top = scored.first else { return nil }
        if scored.count > 1 {
            let second = scored[1]
            let a = nodes[top.index], b = nodes[second.index]
            let identical = a.role == b.role && normalizeLabel(a.label) == normalizeLabel(b.label)
                && a.identifier == b.identifier && a.path == b.path
            if top.score - second.score < 0.005 && !identical {
                return nil   // ambiguous: let the ladder escalate
            }
        }
        return top
    }
}
