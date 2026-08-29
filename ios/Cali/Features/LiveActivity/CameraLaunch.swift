//
//  CameraLaunch.swift
//  Cali
//

import Combine
import SwiftUI
import UIKit

@MainActor
final class CameraLaunch: ObservableObject {
    static let shared = CameraLaunch()

    @Published var showPicker = false
    @Published var showUnavailable = false

    private var pendingEventId: String?

    private init() {}

    func handle(_ url: URL) {
        guard url.scheme?.caseInsensitiveCompare("cali") == .orderedSame,
              url.host?.caseInsensitiveCompare("camera") == .orderedSame else {
            return
        }
        let eventId = URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems?
            .first(where: { $0.name.caseInsensitiveCompare("eventId") == .orderedSame })?
            .value
        open(eventId: eventId)
    }

    func open(eventId: String? = nil) {
        pendingEventId = eventId
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(350))
            guard UIImagePickerController.isSourceTypeAvailable(.camera) else {
                pendingEventId = nil
                showUnavailable = true
                return
            }
            showPicker = true
        }
    }

    func didCapturePhoto() {
        showPicker = false
        let eventId = pendingEventId
        pendingEventId = nil
        if let eventId, !eventId.isEmpty {
            EventLiveActivityManager.shared.dismiss(eventId: eventId)
        }
    }

    func didCancel() {
        showPicker = false
        pendingEventId = nil
    }
}
