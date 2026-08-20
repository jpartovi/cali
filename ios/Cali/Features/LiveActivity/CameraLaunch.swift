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

    private init() {}

    func handle(_ url: URL) {
        guard url.scheme?.caseInsensitiveCompare("cali") == .orderedSame,
              url.host?.caseInsensitiveCompare("camera") == .orderedSame else {
            return
        }
        open()
    }

    func open() {
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(350))
            guard UIImagePickerController.isSourceTypeAvailable(.camera) else {
                showUnavailable = true
                return
            }
            showPicker = true
        }
    }
}
