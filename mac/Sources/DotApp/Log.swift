#if os(macOS)
import Foundation
import os

/// os.Logger categories. Rule: never log PII — no typed text, labels, window
/// titles, URLs, emails or tokens. Log counts, status codes and state names only.
enum Log {
    static let subsystem = "com.workshadower.dot"
    static let app = Logger(subsystem: subsystem, category: "app")
    static let recorder = Logger(subsystem: subsystem, category: "recorder")
    static let upload = Logger(subsystem: subsystem, category: "upload")
    static let replay = Logger(subsystem: subsystem, category: "replay")
    static let net = Logger(subsystem: subsystem, category: "net")
}
#endif
