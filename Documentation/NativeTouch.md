# Native touch on macOS

Duo uses the public CoreHID API to create a virtual touchscreen. Native touch also depends on an undocumented binding between that device and the Mac display.

Regular Touch mode turns taps into mouse clicks. Native touch sends a three-finger swipe as three moving contacts; macOS and the app decide what that gesture does. Supported Mac controls can also expand when touched.

The phone tracks up to five fingers, each with a stable ID and a position within the Mac image. Touches that start outside the image are ignored. These updates use the session's existing TLS 1.3 connection, and the host accepts them only from an approved device with control access.

The [HID descriptor](../Sources/DuoHost/Input/NativeTouchDescriptor.swift) declares five finger slots. We report each finger's ID, position and down/up state. Movement updates can be combined, but presses and lifts are preserved so a quick tap doesn't disappear.

Each occupied slot also reports `TouchValid` (finger confidence), including the lift report after its tip switch clears. [Apple's HID driver uses this field to distinguish Finger from Hand contacts](https://github.com/apple-oss-distributions/IOHIDFamily/blob/777ccd9698845aadf711e32d843c8c9b777431d9/IOHIDFamily/IOHIDEventDriver.cpp#L4089). Declaring a Finger collection alone isn't sufficient. Without confidence, some app controls can still respond while the Dock ignores native taps. Confidence occupies a former padding bit; contact IDs, coordinates and the 32-byte input report remain unchanged.

Recognizing a HID digitizer isn't enough. macOS needs to know which display it belongs to. We pass the display's UUID as a `displayUUID` string and set `kIOHIDDisplayIntegratedKey` when creating the device, before activation. This works for the existing display in Control Mac and the virtual display in Use as Display. The association is undocumented and can change with macOS updates.

Keyboard input sends key events. The toolbar remembers the last touch position, so Right-click and the lower trackpad continue from where you touched.

Touch changes are sent as they happen, with another update every 100 ms while the mode is active. If updates stop for more than 0.7 seconds, the host lifts all fingers. Folding, resizing, switching modes, opening a panel or disconnecting also clears held contacts. After a resize, old packets are rejected and fingers must lift before a new gesture starts.

## Running it

Duo enables Native touch on macOS 27.3 or later. It needs screen-recording and Accessibility permission, plus the `com.apple.developer.hid.virtual.device` entitlement. A self-signed declaration doesn't grant permission to create the device.

Our tests used macOS 27.3. Duo doesn't override the private `SupportExternalTouchscreen` flag. USB plug-and-play support remains unverified.

Our local self-build runs with SIP disabled and `amfi_get_out_of_my_way=0x1` in the boot arguments. Those settings affect the entire Mac and remain active after Duo quits. Duo doesn't change them for you. A normally secured Mac needs Apple's authorization for the entitlement.

Select **Session → Pointer mode → Native touch**. If the Mac can't create the device, Duo shows an error and falls back to Touch while the video keeps running.
