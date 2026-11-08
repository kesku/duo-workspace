# Native touch on macOS

Duo uses CoreHID to create a virtual touchscreen.

Regular Touch mode turns taps into mouse clicks. Native touch sends a three-finger swipe as three moving contacts; macOS and the app decide what that gesture does. Supported Mac controls can also expand when touched.

The phone tracks up to five fingers, each with a stable ID and a position within the Mac image. Touches that start outside the image are ignored. These updates use the session's existing TLS 1.3 connection, and the host accepts them only from an approved device with control access.

The [HID descriptor](../Sources/DuoHost/Input/NativeTouchDescriptor.swift) declares five finger slots. We report each finger's ID, position and down/up state. Movement updates can be combined, but presses and lifts are preserved so a quick tap doesn't disappear.

Keyboard input sends key events. The toolbar remembers the last touch position, so Right-click and the lower trackpad continue from where you touched.

Touch changes are sent as they happen, with another update every 100 ms while the mode is active. If updates stop for more than 0.7 seconds, the host lifts all fingers. Folding, resizing, switching modes, opening a panel or disconnecting also clears held contacts. After a resize, old packets are rejected and fingers must lift before a new gesture starts.

## Display association

The host gets the captured display's UUID with `CGDisplayCreateUUIDFromDisplayID` and passes the UUID string to `HIDVirtualDevice` before activation.

```swift
extraProperties: [
    kIOHIDDisplayIntegratedKey: NSNumber(value: true),
    "displayUUID": displayUUID as NSString
]
```

`kIOHIDDisplayIntegratedKey` marks the touchscreen as integrated with a display. `displayUUID` identifies the existing display in Control Mac or the temporary `CGVirtualDisplay` in Use as Display.

CoreHID and the UUID function are public. The entitlement authorizes device creation. Routing depends on the undocumented `displayUUID` property and can change with macOS updates.

[Host implementation](../Sources/DuoHost/Input/SystemNativeTouchDevice.swift#L41)

## Running it

Native touch needs macOS 27.3 or later, screen capture and Accessibility permissions, and `com.apple.developer.hid.virtual.device`. A self-signed declaration can't grant this entitlement.

Our tests used macOS 27.3. Duo doesn't override the private `SupportExternalTouchscreen` flag. USB plug-and-play support remains unverified.

Enable **HID Virtual Device (development)** on the host App ID. Use an Apple Development identity and a Mac provisioning profile that authorizes the entitlement and includes your Mac. See the [signing setup](Development.md#signing-and-installation).

Select **Session → Pointer mode → Native touch**. If the Mac can't create the device, Duo shows an error and falls back to Touch while the video keeps running.
