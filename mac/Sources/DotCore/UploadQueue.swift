import Foundation
#if canImport(SQLite3)
import SQLite3
#else
import CSQLite
#endif

// SQLITE_TRANSIENT is a C macro and isn't imported into Swift.
private let SQLITE_TRANSIENT_DESTRUCTOR = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

public enum QueueError: Error, CustomStringConvertible {
    case open(String)
    case sql(String)

    public var description: String {
        switch self {
        case .open(let m): return "queue open failed: \(m)"
        case .sql(let m): return "queue sql failed: \(m)"
        }
    }
}

public struct QueuedAsset: Equatable {
    public var sha256: String
    public var path: String
    public var contentType: String
    public var bytes: Int

    public init(sha256: String, path: String, contentType: String, bytes: Int) {
        self.sha256 = sha256
        self.path = path
        self.contentType = contentType
        self.bytes = bytes
    }
}

public struct PendingRecording: Equatable {
    public var idempotencyKey: String
    public var payload: Data
    public var attempts: Int
    public var createdAt: Date
}

/// Local-first, crash-safe upload queue backed by SQLite.
///
/// A recording row holds the already-redacted JSON body for `POST /recordings`
/// and its idempotency key (generated once, reused on every retry). Asset rows
/// point at local JPEG files referenced by that recording. Thread-safe: every
/// call is serialized on a private queue.
public final class UploadQueue {
    private var db: OpaquePointer?
    private let lock = DispatchQueue(label: "workshadower.uploadqueue")
    public let path: String

    public init(path: String) throws {
        self.path = path
        let dir = (path as NSString).deletingLastPathComponent
        if !dir.isEmpty {
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true, attributes: nil)
        }
        var handle: OpaquePointer?
        let flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX
        if sqlite3_open_v2(path, &handle, flags, nil) != SQLITE_OK {
            let msg = handle.map { String(cString: sqlite3_errmsg($0)) } ?? "unknown"
            sqlite3_close(handle)
            throw QueueError.open(msg)
        }
        db = handle
        sqlite3_busy_timeout(handle, 3000)
        try exec("PRAGMA journal_mode=WAL;")
        try exec("PRAGMA foreign_keys=ON;")
        try exec("""
            CREATE TABLE IF NOT EXISTS recordings (
              idem_key TEXT PRIMARY KEY,
              payload BLOB NOT NULL,
              created_at REAL NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              next_attempt_at REAL NOT NULL DEFAULT 0,
              last_error TEXT,
              state TEXT NOT NULL DEFAULT 'pending'
            );
            """)
        try exec("""
            CREATE TABLE IF NOT EXISTS assets (
              sha256 TEXT NOT NULL,
              recording_key TEXT NOT NULL REFERENCES recordings(idem_key) ON DELETE CASCADE,
              path TEXT NOT NULL,
              content_type TEXT NOT NULL,
              bytes INTEGER NOT NULL,
              uploaded INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (sha256, recording_key)
            );
            """)
    }

    deinit {
        sqlite3_close(db)
    }

    // MARK: Public API

    public func enqueueRecording(idempotencyKey: String, payload: Data, assets: [QueuedAsset], now: Date = Date()) throws {
        try lock.sync {
            try execUnlocked("BEGIN IMMEDIATE;")
            do {
                try run("INSERT OR IGNORE INTO recordings (idem_key, payload, created_at) VALUES (?, ?, ?);",
                        [.text(idempotencyKey), .blob(payload), .double(now.timeIntervalSince1970)])
                for a in assets {
                    try run("INSERT OR IGNORE INTO assets (sha256, recording_key, path, content_type, bytes) VALUES (?, ?, ?, ?, ?);",
                            [.text(a.sha256), .text(idempotencyKey), .text(a.path), .text(a.contentType), .int(a.bytes)])
                }
                try execUnlocked("COMMIT;")
            } catch {
                try? execUnlocked("ROLLBACK;")
                throw error
            }
        }
    }

    /// Oldest pending recording whose backoff has elapsed.
    public func nextDueRecording(now: Date = Date()) throws -> PendingRecording? {
        return try lock.sync {
            let rows = try query("""
                SELECT idem_key, payload, attempts, created_at FROM recordings
                WHERE state = 'pending' AND next_attempt_at <= ?
                ORDER BY created_at ASC LIMIT 1;
                """, [.double(now.timeIntervalSince1970)]) { stmt in
                PendingRecording(idempotencyKey: columnText(stmt, 0),
                                 payload: columnBlob(stmt, 1),
                                 attempts: Int(sqlite3_column_int64(stmt, 2)),
                                 createdAt: Date(timeIntervalSince1970: sqlite3_column_double(stmt, 3)))
            }
            return rows.first
        }
    }

    public func pendingAssets(for idempotencyKey: String) throws -> [QueuedAsset] {
        return try lock.sync {
            try query("SELECT sha256, path, content_type, bytes FROM assets WHERE recording_key = ? AND uploaded = 0 ORDER BY sha256;",
                      [.text(idempotencyKey)]) { stmt in
                QueuedAsset(sha256: columnText(stmt, 0), path: columnText(stmt, 1),
                            contentType: columnText(stmt, 2), bytes: Int(sqlite3_column_int64(stmt, 3)))
            }
        }
    }

    public func markAssetUploaded(sha256: String, idempotencyKey: String) throws {
        try lock.sync {
            try run("UPDATE assets SET uploaded = 1 WHERE sha256 = ? AND recording_key = ?;",
                    [.text(sha256), .text(idempotencyKey)])
        }
    }

    /// Records a failed attempt. `dead` parks the item (permanent client error).
    public func recordFailure(idempotencyKey: String, error: String, nextAttemptAt: Date, dead: Bool = false) throws {
        try lock.sync {
            try run("""
                UPDATE recordings SET attempts = attempts + 1, next_attempt_at = ?, last_error = ?, state = ?
                WHERE idem_key = ?;
                """, [.double(nextAttemptAt.timeIntervalSince1970), .text(String(error.prefix(500))),
                      .text(dead ? "dead" : "pending"), .text(idempotencyKey)])
        }
    }

    /// Removes a finished recording; returns local file paths that are no
    /// longer referenced by any other queued recording (safe to delete).
    @discardableResult
    public func completeRecording(idempotencyKey: String) throws -> [String] {
        return try lock.sync {
            let paths = try query("SELECT path, sha256 FROM assets WHERE recording_key = ?;", [.text(idempotencyKey)]) { stmt in
                (columnText(stmt, 0), columnText(stmt, 1))
            }
            try execUnlocked("BEGIN IMMEDIATE;")
            do {
                try run("DELETE FROM assets WHERE recording_key = ?;", [.text(idempotencyKey)])
                try run("DELETE FROM recordings WHERE idem_key = ?;", [.text(idempotencyKey)])
                try execUnlocked("COMMIT;")
            } catch {
                try? execUnlocked("ROLLBACK;")
                throw error
            }
            var orphaned: [String] = []
            for (path, sha) in paths {
                let refs = try query("SELECT COUNT(*) FROM assets WHERE sha256 = ? OR path = ?;", [.text(sha), .text(path)]) { stmt in
                    Int(sqlite3_column_int64(stmt, 0))
                }
                if (refs.first ?? 0) == 0 && !orphaned.contains(path) { orphaned.append(path) }
            }
            return orphaned
        }
    }

    public func pendingCount() throws -> Int {
        return try lock.sync {
            try query("SELECT COUNT(*) FROM recordings WHERE state = 'pending';", []) { stmt in
                Int(sqlite3_column_int64(stmt, 0))
            }.first ?? 0
        }
    }

    public func deadCount() throws -> Int {
        return try lock.sync {
            try query("SELECT COUNT(*) FROM recordings WHERE state = 'dead';", []) { stmt in
                Int(sqlite3_column_int64(stmt, 0))
            }.first ?? 0
        }
    }

    public func earliestNextAttempt() throws -> Date? {
        return try lock.sync {
            try query("SELECT MIN(next_attempt_at) FROM recordings WHERE state = 'pending';", []) { stmt -> Date? in
                if sqlite3_column_type(stmt, 0) == SQLITE_NULL { return nil }
                return Date(timeIntervalSince1970: sqlite3_column_double(stmt, 0))
            }.first ?? nil
        }
    }

    /// Makes every pending item (and parked ones if `includeDead`) due now.
    public func retryNow(includeDead: Bool = false) throws {
        try lock.sync {
            if includeDead {
                try run("UPDATE recordings SET next_attempt_at = 0, state = 'pending';", [])
            } else {
                try run("UPDATE recordings SET next_attempt_at = 0 WHERE state = 'pending';", [])
            }
        }
    }

    /// Deletes everything (used by "discard pending uploads"). Returns asset paths.
    public func purgeAll() throws -> [String] {
        return try lock.sync {
            let paths = try query("SELECT DISTINCT path FROM assets;", []) { stmt in columnText(stmt, 0) }
            try execUnlocked("DELETE FROM assets;")
            try execUnlocked("DELETE FROM recordings;")
            return paths
        }
    }

    // MARK: SQLite plumbing (call only inside `lock`)

    enum Bind {
        case text(String)
        case blob(Data)
        case int(Int)
        case double(Double)
    }

    private func exec(_ sql: String) throws {
        try lock.sync { try execUnlocked(sql) }
    }

    private func execUnlocked(_ sql: String) throws {
        var err: UnsafeMutablePointer<CChar>?
        if sqlite3_exec(db, sql, nil, nil, &err) != SQLITE_OK {
            let msg = err.map { String(cString: $0) } ?? "unknown"
            sqlite3_free(err)
            throw QueueError.sql(msg)
        }
    }

    private func prepare(_ sql: String, _ binds: [Bind]) throws -> OpaquePointer? {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw QueueError.sql(String(cString: sqlite3_errmsg(db)))
        }
        for (i, b) in binds.enumerated() {
            let idx = Int32(i + 1)
            let rc: Int32
            switch b {
            case .text(let s):
                rc = sqlite3_bind_text(stmt, idx, s, -1, SQLITE_TRANSIENT_DESTRUCTOR)
            case .blob(let d):
                rc = d.withUnsafeBytes { raw -> Int32 in
                    // A zero-length blob still needs a non-NULL pointer to stay a blob.
                    if raw.count == 0 { return sqlite3_bind_zeroblob(stmt, idx, 0) }
                    return sqlite3_bind_blob(stmt, idx, raw.baseAddress, Int32(raw.count), SQLITE_TRANSIENT_DESTRUCTOR)
                }
            case .int(let n):
                rc = sqlite3_bind_int64(stmt, idx, sqlite3_int64(n))
            case .double(let d):
                rc = sqlite3_bind_double(stmt, idx, d)
            }
            if rc != SQLITE_OK {
                sqlite3_finalize(stmt)
                throw QueueError.sql(String(cString: sqlite3_errmsg(db)))
            }
        }
        return stmt
    }

    private func run(_ sql: String, _ binds: [Bind]) throws {
        let stmt = try prepare(sql, binds)
        defer { sqlite3_finalize(stmt) }
        let rc = sqlite3_step(stmt)
        if rc != SQLITE_DONE && rc != SQLITE_ROW {
            throw QueueError.sql(String(cString: sqlite3_errmsg(db)))
        }
    }

    private func query<T>(_ sql: String, _ binds: [Bind], _ map: (OpaquePointer?) -> T) throws -> [T] {
        let stmt = try prepare(sql, binds)
        defer { sqlite3_finalize(stmt) }
        var out: [T] = []
        while true {
            let rc = sqlite3_step(stmt)
            if rc == SQLITE_ROW {
                out.append(map(stmt))
            } else if rc == SQLITE_DONE {
                break
            } else {
                throw QueueError.sql(String(cString: sqlite3_errmsg(db)))
            }
        }
        return out
    }
}

private func columnText(_ stmt: OpaquePointer?, _ i: Int32) -> String {
    guard let c = sqlite3_column_text(stmt, i) else { return "" }
    return String(cString: c)
}

private func columnBlob(_ stmt: OpaquePointer?, _ i: Int32) -> Data {
    let n = Int(sqlite3_column_bytes(stmt, i))
    guard n > 0, let p = sqlite3_column_blob(stmt, i) else { return Data() }
    return Data(bytes: p, count: n)
}
