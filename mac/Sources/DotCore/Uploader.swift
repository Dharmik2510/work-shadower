import Foundation

public struct UploaderStatus: Equatable {
    public var pending: Int
    public var dead: Int
    public var isUploading: Bool
    /// 0...1 progress of the item in flight (assets + final POST), nil when idle.
    public var progress: Double?
    public var needsSignIn: Bool
    public var lastError: String?

    public init(pending: Int = 0, dead: Int = 0, isUploading: Bool = false, progress: Double? = nil,
                needsSignIn: Bool = false, lastError: String? = nil) {
        self.pending = pending
        self.dead = dead
        self.isUploading = isUploading
        self.progress = progress
        self.needsSignIn = needsSignIn
        self.lastError = lastError
    }
}

/// Drains the `UploadQueue`: for each due recording, presign each asset →
/// PUT if the server doesn't have it → `POST /recordings` with the stored
/// Idempotency-Key → delete the row and local files. Failures back off
/// exponentially with jitter; everything survives restarts because state lives
/// in SQLite. Call `kick()` on launch, after enqueueing, and when the network
/// comes back.
public final class Uploader {
    public let queue: UploadQueue
    public let api: APIClient
    public var backoff: Backoff
    /// Permanent 4xx failures are retried this many times before parking.
    public var maxPermanentFailures = 3

    /// Called on an arbitrary thread whenever status changes.
    public var onStatus: ((UploaderStatus) -> Void)?

    private let stateLock = NSLock()
    private var running = false
    private var rerun = false
    private var online = true
    private var authBlocked = false
    private var wakeItem: DispatchWorkItem?
    private var status = UploaderStatus()

    private let readFile: (String) -> Data?
    private let removeFile: (String) -> Void

    public init(queue: UploadQueue, api: APIClient, backoff: Backoff = Backoff(),
                readFile: @escaping (String) -> Data? = { FileManager.default.contents(atPath: $0) },
                removeFile: @escaping (String) -> Void = { try? FileManager.default.removeItem(atPath: $0) }) {
        self.queue = queue
        self.api = api
        self.backoff = backoff
        self.readFile = readFile
        self.removeFile = removeFile
    }

    public var currentStatus: UploaderStatus {
        stateLock.lock()
        defer { stateLock.unlock() }
        return status
    }

    public func setOnline(_ value: Bool) {
        stateLock.lock()
        let changed = online != value
        online = value
        stateLock.unlock()
        if value && changed { kick() }
    }

    /// Clears the auth block (after sign-in) and retries.
    public func signedIn() {
        stateLock.lock()
        authBlocked = false
        status.needsSignIn = false
        stateLock.unlock()
        kick()
    }

    public func retryNow() {
        try? queue.retryNow(includeDead: true)
        kick()
    }

    /// Starts draining in the background unless already running.
    public func kick() {
        stateLock.lock()
        if running {
            rerun = true
            stateLock.unlock()
            return
        }
        running = true
        stateLock.unlock()
        Task.detached { [weak self] in
            guard let self = self else { return }
            while true {
                _ = await self.drain()
                if self.finishOrRerun() { continue }
                break
            }
            self.scheduleWake()
        }
    }

    /// Sync helpers so no lock is taken directly inside async code.
    /// Returns true if another drain was requested while this one ran.
    private func finishOrRerun() -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        if rerun {
            rerun = false
            return true
        }
        running = false
        return false
    }

    private func isBlocked() -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        return !online || authBlocked
    }

    public struct DrainResult: Equatable {
        public var uploaded = 0
        public var failed = 0
    }

    /// Processes every recording that is currently due. Exposed for tests.
    public func drain(now: () -> Date = { Date() }) async -> DrainResult {
        var result = DrainResult()
        publish()
        while true {
            if isBlocked() { break }

            let item: PendingRecording?
            do {
                item = try queue.nextDueRecording(now: now())
            } catch {
                setError("queue: \(error)")
                break
            }
            guard let rec = item else { break }

            update { $0.isUploading = true; $0.progress = 0 }
            do {
                try await process(rec)
                result.uploaded += 1
                update { $0.lastError = nil }
            } catch let err as APIError {
                result.failed += 1
                handle(err, for: rec, now: now())
                if err.isAuthFailure || (err.isTransient && isNetworkLevel(err)) { break }
            } catch {
                result.failed += 1
                handle(.network(String(describing: error)), for: rec, now: now())
                break
            }
        }
        update { $0.isUploading = false; $0.progress = nil }
        publish()
        return result
    }

    private func isNetworkLevel(_ e: APIError) -> Bool {
        if case .network = e { return true }
        return false
    }

    private func process(_ rec: PendingRecording) async throws {
        let assets = try queue.pendingAssets(for: rec.idempotencyKey)
        let total = Double(assets.count + 1)
        var done = 0.0
        for a in assets {
            guard let data = readFile(a.path) else {
                // File vanished (user cleared caches?): skip it, the event keeps its hash.
                try queue.markAssetUploaded(sha256: a.sha256, idempotencyKey: rec.idempotencyKey)
                continue
            }
            do {
                let presign = try await api.presign(PresignRequest(sha256: a.sha256, contentType: a.contentType, bytes: data.count))
                if !presign.exists, let upload = presign.upload {
                    try await api.upload(upload, data: data, contentType: a.contentType)
                }
            } catch let e as APIError where e.status == 413 || e.status == 422 || e.status == 400 {
                // Asset rejected (too large / bad type): drop the screenshot, keep the recording.
            }
            try queue.markAssetUploaded(sha256: a.sha256, idempotencyKey: rec.idempotencyKey)
            done += 1
            let fraction = done / total
            update { $0.progress = fraction }
        }
        _ = try await api.createRecording(payload: rec.payload, idempotencyKey: rec.idempotencyKey)
        let orphaned = try queue.completeRecording(idempotencyKey: rec.idempotencyKey)
        for p in orphaned { removeFile(p) }
        update { $0.progress = 1 }
        publish()
    }

    private func handle(_ err: APIError, for rec: PendingRecording, now: Date) {
        let attempt = rec.attempts + 1
        if err.isAuthFailure {
            stateLock.lock()
            authBlocked = true
            status.needsSignIn = true
            stateLock.unlock()
            try? queue.recordFailure(idempotencyKey: rec.idempotencyKey, error: "auth",
                                     nextAttemptAt: now.addingTimeInterval(backoff.base))
        } else if err.isTransient {
            try? queue.recordFailure(idempotencyKey: rec.idempotencyKey, error: err.description,
                                     nextAttemptAt: now.addingTimeInterval(backoff.delay(attempt: attempt)))
        } else {
            let dead = attempt >= maxPermanentFailures
            try? queue.recordFailure(idempotencyKey: rec.idempotencyKey, error: err.description,
                                     nextAttemptAt: now.addingTimeInterval(backoff.delay(attempt: attempt)), dead: dead)
        }
        setError(err.description)
    }

    private func scheduleWake() {
        guard let next = try? queue.earliestNextAttempt() else { return }
        let delay = max(1, next.timeIntervalSinceNow)
        let item = DispatchWorkItem { [weak self] in self?.kick() }
        stateLock.lock()
        wakeItem?.cancel()
        wakeItem = item
        stateLock.unlock()
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + min(delay, 3600), execute: item)
    }

    // MARK: status

    private func update(_ change: (inout UploaderStatus) -> Void) {
        stateLock.lock()
        change(&status)
        let s = status
        stateLock.unlock()
        onStatus?(s)
    }

    private func setError(_ msg: String) {
        update { $0.lastError = msg }
    }

    private func publish() {
        let pending = (try? queue.pendingCount()) ?? 0
        let dead = (try? queue.deadCount()) ?? 0
        update { $0.pending = pending; $0.dead = dead }
    }
}
