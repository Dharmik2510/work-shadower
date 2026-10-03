import Foundation

/// Exponential backoff with "full jitter" around the exponential envelope.
/// delay(n) = random in [base*2^n * (1-jitter), base*2^n] capped at `cap`.
public struct Backoff {
    public var base: TimeInterval
    public var cap: TimeInterval
    public var jitter: Double

    public init(base: TimeInterval = 5, cap: TimeInterval = 30 * 60, jitter: Double = 0.5) {
        self.base = base
        self.cap = cap
        self.jitter = max(0, min(1, jitter))
    }

    /// `attempt` is the number of failures so far (1 = first failure).
    public func delay(attempt: Int, random: Double = Double.random(in: 0...1)) -> TimeInterval {
        let n = max(0, min(attempt - 1, 30))
        let envelope = min(cap, base * pow(2, Double(n)))
        let r = max(0, min(1, random))
        return envelope * (1 - jitter) + envelope * jitter * r
    }
}
