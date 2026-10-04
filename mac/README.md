# Dot — the Mac client

The floating dot in the top-right corner. Eyes closed when idle, open on hover.
Click it to record a workflow; right-click for search, library, uploads, settings.

## Build

Needs macOS 13+ and Xcode 15+ (or Command Line Tools).

```bash
cd mac
./build.sh          # runs core tests, builds, writes Dot.app (ad-hoc signed)
open Dot.app
```

For company rollout, sign with a Developer ID and notarize:
`SIGN_ID="Developer ID Application: Your Co (TEAMID)" ./build.sh`, then `xcrun notarytool submit …`.

First launch opens Settings: server URL → sign in → grant permissions.

## Permissions

| Permission | Why | Required |
|---|---|---|
| Accessibility | Read button/field names while recording; press them during replay | Yes |
| Input Monitoring | Notice keystrokes and shortcuts (never in password fields) | Yes |
| Screen Recording | A few screenshots of the front window at key moments | No |

With ad-hoc signing, macOS forgets these after each rebuild. A Developer ID build plus the
PPPC profile in `mdm/` avoids that on managed Macs.

## Layout

```
Sources/DotCore   platform-neutral logic, unit tested (also runs on Linux)
  EventModels     recorded events + upload payload (matches docs/CONTRACT.md)
  Redactor        emails, card/SIN/phone numbers → [REDACTED:kind]; secure fields never captured
  EventCleanup    merge typing, drop noise, de-dup, decide when a screenshot is worth it
  UploadQueue     SQLite queue (survives restarts/offline); a new recording is held while the intent question is open
  Uploader        presign → upload (dedup by sha256) → POST /recordings with Idempotency-Key, backoff + jitter
  TargetMatcher   scores AX nodes against a recorded target (identifier > role+label > fuzzy label > path)
  Template        {{input}} filling
  APIClient       typed client for the server
Sources/DotApp    AppKit/SwiftUI app
  AppController   wires everything; config refresh + kill switch; workshadower:// links
  DotView         the dot (states: sleeping, awake, recording, uploading, replaying, error)
  Recorder        listen-only event tap + AX lookups + key-moment screenshots
  Screenshotter   ScreenCaptureKit (14+) / CGWindowList (13), JPEG ≤1600px
  Replayer        replay ladder: deterministic AX → LLM repair → ask the human
  ReplayHUD       step HUD + inputs form
  SearchPanel     "How do I…?" (⌥⌘Space, or ⌃⌥Space if taken)
  IntentPrompt    "What did you just do?" after each recording (Skip / Esc / 2-minute timeout continue)
  SettingsWindow  onboarding, sign-in, permissions, privacy, launch at login
```

## How replay stays safe

- Every step is matched by accessibility role/label/identifier, not screen coordinates.
- Steps marked irreversible always wait for a confirm click, in every mode.
- Guided mode (default) confirms every step. Auto mode only stops when something needs you.
- If a control can't be found, the server's LLM proposes a match from a text-only UI tree (no screenshots).
  Used only at ≥ 0.7 confidence, and the fix is sent to the skill owner as a suggestion.
- Otherwise the dot pauses and asks you to do the step.
- Each step's result is reported to the server, which tracks skill health.
- Steps the reviewer left out (`excluded`) are never replayed.
- Admins can turn recording and replay off company-wide (checked at launch and every 10 min).

## "What did you just do?"

When recording stops, the recording is saved to the queue right away but held, and a small panel asks
for one line about the task. The answer is redacted on the Mac and added to the queued upload, which is
then released. Skip, Esc, closing the panel or the timeout upload it without a note; if the app quits
while the panel is open, the recording uploads on the next launch. Turn the question off in
Settings → Privacy & behaviour ("Ask what I did after each recording").

## Privacy guarantees

- No video. Structured events only, plus optional key-moment screenshots of the front window.
- Password fields (`AXSecureTextField`) are never read; keystrokes there are dropped.
- PII is redacted on the Mac before it is queued, and the server redacts again.
- URLs lose their query strings.
- Logs (os.Logger) contain counts and states only, never text, labels, titles or URLs.
- Nothing is recorded unless the red ring is showing.

## MDM deployment (`mdm/`)

`WorkShadower-PPPC.mobileconfig` pre-allows Accessibility, lets standard users approve
Input Monitoring and Screen Recording, and pre-sets the server URL.
Replace the bundle ID, `CodeRequirement` (`codesign -dr - Dot.app`) and Team ID first.
Use your MDM to install Dot.app and add it as a login item.

## Testing status

- `swift test`: DotCore tests (redaction, cleanup, matching, templates, queue, uploader, intent + held
  queue items, excluded steps). `IntentAndFilterTests` is new and has not been run yet: no Swift toolchain
  was available when it was written.
- DotApp is written against macOS 13+ APIs but was syntax-checked only, not compiled against
  the macOS SDK. Run `./build.sh` on a Mac first and fix any compiler complaints.
  Riskiest areas to check by hand: the new `IntentPrompt` panel (focus, Esc, timeout), the event tap + AX lookups in `Recorder`,
  ScreenCaptureKit in `Screenshotter`, and click/typing fallbacks in `Replayer`.

## Not done yet

- Desktop SSO (OIDC). The Mac app uses dev-login; SSO works in the web app.
- Vision fallback (step 3 of the ladder currently asks the human instead).
