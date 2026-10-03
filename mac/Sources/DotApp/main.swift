#if os(macOS)
import AppKit

let app = NSApplication.shared
let controller = AppController()
app.delegate = controller
app.setActivationPolicy(.accessory)   // no Dock icon
app.run()
#else
print("Dot is a macOS app; DotCore builds and tests on this platform.")
#endif
