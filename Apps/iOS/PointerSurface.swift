import SwiftUI
import DuoCore

struct PointerSurface: UIViewRepresentable {
    let input: SessionInputController
    var enabled: Bool
    var mode: PointerMode = .trackpad
    var frameAspect: Double = 1
    var nativeTouch: SessionNativeTouch?
    var nativeTouchCancellationID: UInt64 = 0
    var doubleTapAction: TouchDoubleTapAction = .doubleClick
    var revealControls: (() -> Void)?
    var accessibleName: LocalizedStringResource = "Remote trackpad"
    @Environment(\.isEnabled) private var environmentEnabled

    func makeCoordinator() -> Coordinator { Coordinator(input: input) }

    func makeUIView(context: Context) -> PointerPadView {
        let view = PointerPadView()
        view.backgroundColor = .clear
        context.coordinator.installGestures(on: view)
        view.isAccessibilityElement = true
        view.onActivate = { [weak coordinator = context.coordinator] in coordinator?.accessibleClick() ?? false }
        configure(view, coordinator: context.coordinator)
        return view
    }

    func updateUIView(_ view: PointerPadView, context: Context) {
        context.coordinator.input = input
        configure(view, coordinator: context.coordinator)
    }

    private func configure(_ view: PointerPadView, coordinator: Coordinator) {
        let active = enabled && environmentEnabled && (mode != .nativeTouch || nativeTouch?.isReady == true)
        coordinator.configure(view: view, enabled: active, mode: mode, aspect: frameAspect,
                              doubleTapAction: doubleTapAction, revealControls: revealControls)
        view.configureNativeTouch(active && mode == .nativeTouch ? nativeTouch : nil,
                                  aspect: frameAspect, reservesEdges: revealControls != nil,
                                  cancellationID: nativeTouchCancellationID)
        view.isUserInteractionEnabled = active || revealControls != nil
        view.accessibilityLabel = String(localized: accessibleName)
        view.accessibilityHint = revealControls == nil
            ? String(localized: "Use actions to move the pointer, click, drag or scroll.")
            : String(localized: "Use Show controls, or swipe inward from either side.")
        view.accessibilityTraits = active || revealControls != nil ? [.button] : [.button, .notEnabled]
        view.accessibilityCustomActions = coordinator.accessibilityActions
        view.accessibilityValue = input.isDragging ? String(localized: "Dragging")
            : input.isScrolling ? String(localized: "Scroll mode") : String(localized: mode.title)
    }

    static func dismantleUIView(_ view: PointerPadView, coordinator: Coordinator) {
        view.cancelNativeTouches()
        coordinator.cancelInteraction()
        coordinator.isEnabled = false
        coordinator.revealControls = nil
        view.isUserInteractionEnabled = false
        view.accessibilityCustomActions = nil
    }

    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        var input: SessionInputController
        var isEnabled = true
        private(set) var mode: PointerMode = .trackpad
        private(set) var frameAspect: Double = 1
        private(set) var doubleTapAction: TouchDoubleTapAction = .doubleClick
        var revealControls: (() -> Void)?
        private var longDragging = false
        private var panScrolled = false
        private var panStartedOnImage = false
        private var scrollStartedOnImage = false
        private var lastTapTime: TimeInterval = 0
        private var lastTapPoint = CGPoint.zero
        private var lastTapPosition: CGPoint?
        private weak var doubleRightGesture: UITapGestureRecognizer?
        private weak var revealGesture: UIPanGestureRecognizer?
        private weak var pointerPanGesture: UIPanGestureRecognizer?
        private weak var scrollGesture: UIPanGestureRecognizer?
        private weak var bottomEdgeGesture: UIPanGestureRecognizer?
        private var edgeSwipeStart: CGPoint?
        private var revealStart: CGPoint?
        private var panOrigin: CGPoint?
        private var scrollOrigin: CGPoint?

        init(input: SessionInputController) { self.input = input }

        func installGestures(on view: UIView) {
            let pan = UIPanGestureRecognizer(target: self, action: #selector(pan(_:)))
            pan.maximumNumberOfTouches = 1
            pointerPanGesture = pan
            let scroll = UIPanGestureRecognizer(target: self, action: #selector(scroll(_:)))
            scroll.minimumNumberOfTouches = 2; scroll.maximumNumberOfTouches = 2
            scrollGesture = scroll
            let tap = UITapGestureRecognizer(target: self, action: #selector(tap(_:)))
            let right = UITapGestureRecognizer(target: self, action: #selector(rightTap(_:)))
            right.numberOfTouchesRequired = 2
            let doubleRight = UITapGestureRecognizer(target: self, action: #selector(doubleRightTap(_:)))
            doubleRight.numberOfTapsRequired = 2
            doubleRightGesture = doubleRight
            let hold = UILongPressGestureRecognizer(target: self, action: #selector(hold(_:)))
            hold.minimumPressDuration = 0.4
            let inputGestures = [pan, scroll, tap, right, doubleRight, hold]
            for gesture in inputGestures { gesture.delegate = self; view.addGestureRecognizer(gesture) }
            tap.require(toFail: right)
            tap.require(toFail: doubleRight)
            let reveal = UIPanGestureRecognizer(target: self, action: #selector(reveal(_:)))
            reveal.maximumNumberOfTouches = 1
            reveal.delegate = self
            revealGesture = reveal
            view.addGestureRecognizer(reveal)
            for gesture in inputGestures { gesture.require(toFail: reveal) }
            if let pad = view as? PointerPadView {
                let dock = pad.bottomEdgeGesture
                dock.addTarget(self, action: #selector(swipeFromBottom(_:)))
                dock.maximumNumberOfTouches = 1
                dock.delaysTouchesBegan = true
                dock.delegate = self
                bottomEdgeGesture = dock
                view.addGestureRecognizer(dock)
            }
        }

        func configure(view: UIView, enabled: Bool, mode: PointerMode, aspect: Double,
                       doubleTapAction: TouchDoubleTapAction, revealControls: (() -> Void)?) {
            if self.mode != mode || self.doubleTapAction != doubleTapAction || (isEnabled && !enabled)
                || (mode != .trackpad && frameAspect != aspect) {
                for gesture in view.gestureRecognizers ?? [] { gesture.isEnabled = false }
                cancelInteraction()
            }
            self.isEnabled = enabled; self.mode = mode; self.frameAspect = aspect
            self.doubleTapAction = doubleTapAction; self.revealControls = revealControls
            for gesture in view.gestureRecognizers ?? [] {
                if gesture === bottomEdgeGesture { gesture.isEnabled = enabled && mode == .nativeTouch }
                else if gesture === revealGesture { gesture.isEnabled = revealControls != nil }
                else if gesture === doubleRightGesture { gesture.isEnabled = enabled && mode == .direct && doubleTapAction == .rightClick }
                else { gesture.isEnabled = enabled && mode != .nativeTouch }
            }
        }

        func cancelInteraction() {
            (bottomEdgeGesture?.view as? PointerPadView)?.endEdgeSwipe()
            edgeSwipeStart = nil
            lastTapTime = 0; lastTapPosition = nil
            revealStart = nil; panOrigin = nil; scrollOrigin = nil
            longDragging = false; panScrolled = false; panStartedOnImage = false; scrollStartedOnImage = false
            input.releaseHeldInput()
        }

        @objc func pan(_ gesture: UIPanGestureRecognizer) {
            guard isEnabled, let view = gesture.view else { return }
            var translation = gesture.translation(in: view)
            let point = gesture.location(in: view)
            if gesture.state == .began {
                lastTapTime = 0; panScrolled = false
                let start = panOrigin ?? CGPoint(x: point.x - translation.x, y: point.y - translation.y)
                translation = CGPoint(x: point.x - start.x, y: point.y - start.y)
                panStartedOnImage = mode == .trackpad || position(start, in: view.bounds) != nil
                if mode == .direct, input.isScrolling, let target = position(start, in: view.bounds) {
                    input.move(to: target)
                }
            }
            if [.began, .changed, .ended].contains(gesture.state), panStartedOnImage {
                if input.isScrolling { panScrolled = true; input.scroll(translation) }
                else if mode == .direct {
                    if let target = position(point, in: view.bounds, clamped: true) { input.move(to: target) }
                } else { input.move(translation, in: view.bounds.size) }
            }
            gesture.setTranslation(.zero, in: view)
            if gesture.state == .ended {
                if panScrolled { input.finishScroll() }
                panScrolled = false; panStartedOnImage = false; panOrigin = nil
            }
            if [.cancelled, .failed].contains(gesture.state) { cancelInteraction() }
        }

        @objc func scroll(_ gesture: UIPanGestureRecognizer) {
            guard isEnabled, let view = gesture.view else { return }
            var delta = gesture.translation(in: view)
            if gesture.state == .began {
                lastTapTime = 0
                scrollStartedOnImage = mode == .trackpad
                let point = gesture.location(in: view)
                let start = scrollOrigin ?? CGPoint(x: point.x - delta.x, y: point.y - delta.y)
                delta = CGPoint(x: point.x - start.x, y: point.y - start.y)
                if mode == .direct, let target = position(start, in: view.bounds) {
                    input.move(to: target)
                    scrollStartedOnImage = true
                }
            }
            if [.began, .changed, .ended].contains(gesture.state), scrollStartedOnImage {
                gesture.setTranslation(.zero, in: view)
                input.scroll(delta)
            }
            if [.ended, .cancelled, .failed].contains(gesture.state) {
                input.finishScroll(); scrollStartedOnImage = false; scrollOrigin = nil
            }
        }

        @objc func tap(_ gesture: UITapGestureRecognizer) {
            guard gesture.state == .ended, let view = gesture.view else { return }
            click(at: gesture.location(in: view), in: view.bounds, time: ProcessInfo.processInfo.systemUptime)
        }

        func click(at point: CGPoint, in bounds: CGRect, time: TimeInterval) {
            guard isEnabled, mode != .nativeTouch else { return }
            let repeated = lastTapTime > 0 && time - lastTapTime < 0.35
                && hypot(point.x - lastTapPoint.x, point.y - lastTapPoint.y) < 24
            let double = repeated && (mode == .trackpad || doubleTapAction == .doubleClick)
            if mode == .direct {
                guard let target = position(point, in: bounds) else { lastTapTime = 0; return }
                let destination = double ? lastTapPosition ?? target : target
                input.move(to: destination)
                lastTapPosition = destination
            }
            input.click(count: double ? 2 : 1)
            lastTapTime = double ? 0 : time; lastTapPoint = point
        }

        @objc func rightTap(_ gesture: UITapGestureRecognizer) {
            guard gesture.state == .ended, let view = gesture.view else { return }
            rightClick(at: gesture.location(in: view), in: view.bounds)
        }

        @objc func doubleRightTap(_ gesture: UITapGestureRecognizer) {
            guard mode == .direct, doubleTapAction == .rightClick else { return }
            rightTap(gesture)
        }

        func rightClick(at point: CGPoint, in bounds: CGRect) {
            guard isEnabled, mode != .nativeTouch else { return }
            lastTapTime = 0
            if mode == .direct {
                guard let target = position(point, in: bounds) else { return }
                input.move(to: target)
            }
            input.click(right: true)
        }

        @objc func hold(_ gesture: UILongPressGestureRecognizer) {
            guard isEnabled, let view = gesture.view else { return }
            if gesture.state == .began {
                lastTapTime = 0
                if mode == .direct {
                    guard let target = position(gesture.location(in: view), in: view.bounds) else { return }
                    input.move(to: target)
                }
                longDragging = true; input.beginDrag()
            }
            if [.ended, .cancelled, .failed].contains(gesture.state), longDragging {
                input.endDrag(); longDragging = false
            }
        }

        @objc func reveal(_ gesture: UIPanGestureRecognizer) {
            guard let view = gesture.view else { return }
            if gesture.state == .ended, let revealStart {
                let point = gesture.location(in: view)
                let delta = CGPoint(x: point.x - revealStart.x, y: point.y - revealStart.y)
                if ControlsRevealGesture.isInward(start: revealStart, translation: delta, in: view.bounds, minimumDistance: 32) {
                    _ = accessibleShowControls()
                }
            }
            if [.ended, .cancelled, .failed].contains(gesture.state) { revealStart = nil }
        }

        @objc private func swipeFromBottom(_ gesture: UIPanGestureRecognizer) {
            guard let view = gesture.view as? PointerPadView else { return }
            if gesture.state == .began, let start = edgeSwipeStart { view.beginEdgeSwipe(at: start) }
            if [.began, .changed, .ended].contains(gesture.state) {
                view.moveEdgeSwipe(to: gesture.location(in: view))
            }
            if gesture.state == .cancelled { view.cancelNativeTouches() }
            if [.ended, .cancelled, .failed].contains(gesture.state) {
                view.endEdgeSwipe(); edgeSwipeStart = nil
            }
        }

        private func position(_ point: CGPoint, in bounds: CGRect, clamped: Bool = false) -> CGPoint? {
            DesktopTouchGeometry.normalized(point, in: bounds, aspect: frameAspect, clamped: clamped)
        }

        var accessibilityActions: [UIAccessibilityCustomAction] {
            var actions: [UIAccessibilityCustomAction] = []
            if revealControls != nil {
                actions.append(UIAccessibilityCustomAction(name: String(localized: "Show controls"), target: self, selector: #selector(accessibleShowControls)))
            }
            guard isEnabled else { return actions }
            return actions + [
                UIAccessibilityCustomAction(name: String(localized: "Move pointer left"), target: self, selector: #selector(accessibleMoveLeft)),
                UIAccessibilityCustomAction(name: String(localized: "Move pointer right"), target: self, selector: #selector(accessibleMoveRight)),
                UIAccessibilityCustomAction(name: String(localized: "Move pointer up"), target: self, selector: #selector(accessibleMoveUp)),
                UIAccessibilityCustomAction(name: String(localized: "Move pointer down"), target: self, selector: #selector(accessibleMoveDown)),
                UIAccessibilityCustomAction(name: String(localized: "Right-click"), target: self, selector: #selector(accessibleRightClick)),
                UIAccessibilityCustomAction(name: input.isDragging ? String(localized: "Drop dragged item") : String(localized: "Start dragging"), target: self, selector: #selector(accessibleDrag)),
                UIAccessibilityCustomAction(name: String(localized: "Scroll up"), target: self, selector: #selector(accessibleScrollUp)),
                UIAccessibilityCustomAction(name: String(localized: "Scroll down"), target: self, selector: #selector(accessibleScrollDown)),
                UIAccessibilityCustomAction(name: String(localized: "Scroll left"), target: self, selector: #selector(accessibleScrollLeft)),
                UIAccessibilityCustomAction(name: String(localized: "Scroll right"), target: self, selector: #selector(accessibleScrollRight)),
                UIAccessibilityCustomAction(name: String(localized: "Release all keys and buttons"), target: self, selector: #selector(accessibleRelease))
            ]
        }

        @objc func accessibleShowControls() -> Bool {
            guard let revealControls else { return false }
            cancelInteraction(); revealControls(); return true
        }
        @objc func accessibleClick() -> Bool { guard isEnabled else { return false }; input.click(); return true }
        @objc func accessibleRightClick() -> Bool { guard isEnabled else { return false }; input.click(right: true); return true }
        @objc func accessibleDrag() -> Bool { guard isEnabled else { return false }; input.toggleDrag(); return true }
        @objc func accessibleMoveLeft() -> Bool { movePointer(dx: -1, dy: 0) }
        @objc func accessibleMoveRight() -> Bool { movePointer(dx: 1, dy: 0) }
        @objc func accessibleMoveUp() -> Bool { movePointer(dx: 0, dy: -1) }
        @objc func accessibleMoveDown() -> Bool { movePointer(dx: 0, dy: 1) }
        @objc func accessibleScrollUp() -> Bool { scrollContent(dx: 0, dy: 60) }
        @objc func accessibleScrollDown() -> Bool { scrollContent(dx: 0, dy: -60) }
        @objc func accessibleScrollLeft() -> Bool { scrollContent(dx: 60, dy: 0) }
        @objc func accessibleScrollRight() -> Bool { scrollContent(dx: -60, dy: 0) }
        @objc func accessibleRelease() -> Bool { guard isEnabled else { return false }; input.releaseHeldInput(); return true }
        private func movePointer(dx: Double, dy: Double) -> Bool {
            guard isEnabled else { return false }; input.nudgePointer(dx: dx, dy: dy); return true
        }
        private func scrollContent(dx: Double, dy: Double) -> Bool {
            guard isEnabled else { return false }; input.scrollContent(dx: dx, dy: dy); return true
        }
        func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
            if gestureRecognizer === bottomEdgeGesture {
                guard let gesture = bottomEdgeGesture, let start = edgeSwipeStart else { return false }
                return prepareEdgeSwipe(gesture, startingAt: start)
            }
            if gestureRecognizer === revealGesture, let gesture = revealGesture { return prepareReveal(gesture) }
            if gestureRecognizer === pointerPanGesture, let gesture = pointerPanGesture { return preparePan(gesture) }
            if gestureRecognizer === scrollGesture, let gesture = scrollGesture { return preparePan(gesture, scrolling: true) }
            return isEnabled
        }
        func prepareEdgeSwipe(_ gesture: UIPanGestureRecognizer, startingAt start: CGPoint) -> Bool {
            guard isEnabled, mode == .nativeTouch, let view = gesture.view as? PointerPadView else { return false }
            let velocity = gesture.velocity(in: view)
            guard velocity.y < 0, -velocity.y > abs(velocity.x) * 1.5,
                  let candidate = view.edgeSwipeStart(at: start) else { return false }
            if candidate.gesture == .dock && velocity.y > -250 { return false }
            edgeSwipeStart = start
            return true
        }
        func prepareReveal(_ gesture: UIPanGestureRecognizer) -> Bool {
            guard revealControls != nil, let view = gesture.view else { return false }
            let point = gesture.location(in: view), delta = gesture.translation(in: view)
            let start = CGPoint(x: point.x - delta.x, y: point.y - delta.y)
            guard ControlsRevealGesture.isInward(start: start, translation: delta, in: view.bounds) else { return false }
            // UIKit resets translation at .began; save the original touch position.
            revealStart = start
            return true
        }
        func preparePan(_ gesture: UIPanGestureRecognizer, scrolling: Bool = false) -> Bool {
            guard isEnabled, let view = gesture.view else { return false }
            let point = gesture.location(in: view), delta = gesture.translation(in: view)
            let start = CGPoint(x: point.x - delta.x, y: point.y - delta.y)
            if scrolling { scrollOrigin = start } else { panOrigin = start }
            return mode == .trackpad || position(start, in: view.bounds) != nil
        }
        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
            if gestureRecognizer === bottomEdgeGesture, let view = gestureRecognizer.view as? PointerPadView {
                if [.began, .changed].contains(gestureRecognizer.state) { return true }
                let start = touch.location(in: view)
                guard isEnabled, mode == .nativeTouch, view.edgeSwipeCandidate(at: start) != nil else {
                    edgeSwipeStart = nil
                    return false
                }
                edgeSwipeStart = start
                return true
            }
            guard gestureRecognizer === revealGesture, let view = gestureRecognizer.view else { return isEnabled }
            return revealControls != nil && ControlsRevealGesture.startsAtEdge(touch.location(in: view), in: view.bounds)
        }
        func gestureRecognizer(_ gesture: UIGestureRecognizer, shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool {
            guard gesture !== revealGesture, other !== revealGesture else { return false }
            return (gesture is UILongPressGestureRecognizer && other is UIPanGestureRecognizer)
                || (other is UILongPressGestureRecognizer && gesture is UIPanGestureRecognizer)
        }
    }
}
