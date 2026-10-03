#if os(macOS)
import AppKit
import Carbon.HIToolbox
import Network

/// Wraps NWPathMonitor; calls back on the main queue.
final class NetworkMonitor {
    private let monitor = NWPathMonitor()
    private let queue = DispatchQueue(label: "workshadower.netmonitor")
    private(set) var isOnline = true

    func start(onChange: @escaping (Bool) -> Void) {
        monitor.pathUpdateHandler = { [weak self] path in
            let online = path.status == .satisfied
            DispatchQueue.main.async {
                guard let self = self else { return }
                let changed = online != self.isOnline
                self.isOnline = online
                if changed { Log.net.info("network \(online ? "online" : "offline", privacy: .public)") }
                onChange(online)
            }
        }
        monitor.start(queue: queue)
    }
}

/// Global hotkey via Carbon RegisterEventHotKey (no Accessibility needed).
/// Default ⌥⌘Space; note macOS binds ⌥⌘Space to "Finder search window" by
/// default, in which case the system shortcut wins — we then fall back to ⌃⌥Space.
final class HotKey {
    private var hotKeyRef: EventHotKeyRef?
    private var handlerRef: EventHandlerRef?
    fileprivate var action: () -> Void = {}
    private(set) var descriptionText = ""

    func register(action: @escaping () -> Void) {
        self.action = action
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let selfPtr = Unmanaged.passUnretained(self).toOpaque()
        let status = InstallEventHandler(GetApplicationEventTarget(), { _, _, userData -> OSStatus in
            if let userData = userData {
                let hk = Unmanaged<HotKey>.fromOpaque(userData).takeUnretainedValue()
                DispatchQueue.main.async { hk.action() }
            }
            return noErr
        }, 1, &spec, selfPtr, &handlerRef)
        guard status == noErr else {
            Log.app.error("hotkey handler install failed: \(status, privacy: .public)")
            return
        }
        let id = EventHotKeyID(signature: OSType(0x444F_5420), id: 1)   // 'DOT '
        var ref: EventHotKeyRef?
        var rc = RegisterEventHotKey(UInt32(kVK_Space), UInt32(cmdKey | optionKey), id, GetApplicationEventTarget(), 0, &ref)
        descriptionText = "⌥⌘Space"
        if rc != noErr {
            rc = RegisterEventHotKey(UInt32(kVK_Space), UInt32(controlKey | optionKey), id, GetApplicationEventTarget(), 0, &ref)
            descriptionText = "⌃⌥Space"
        }
        if rc == noErr {
            hotKeyRef = ref
            Log.app.info("hotkey registered")
        } else {
            descriptionText = ""
            Log.app.error("hotkey registration failed: \(rc, privacy: .public)")
        }
    }

    deinit {
        if let r = hotKeyRef { UnregisterEventHotKey(r) }
        if let h = handlerRef { RemoveEventHandler(h) }
    }
}
#endif
