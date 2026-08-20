//
//  OpenCameraIntent.swift
//  Cali
//

import AppIntents

struct OpenCameraIntent: LiveActivityIntent {
    static var title: LocalizedStringResource = "Open Camera"
    static var description = IntentDescription("Opens the camera.")
    static var openAppWhenRun = true
    static var isDiscoverable = false

    func perform() async throws -> some IntentResult {
        #if !WIDGET_EXTENSION
        await CameraLaunch.shared.open()
        #endif
        return .result()
    }
}
