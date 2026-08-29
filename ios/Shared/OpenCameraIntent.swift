//
//  OpenCameraIntent.swift
//  Cali
//

import AppIntents

struct OpenCameraIntent: LiveActivityIntent {
    static var title: LocalizedStringResource = "Take Photo"
    static var description = IntentDescription("Opens the camera to take a photo for this event.")
    static var openAppWhenRun = true
    static var isDiscoverable = false

    @Parameter(title: "Event ID")
    var eventId: String

    init() {
        self.eventId = ""
    }

    init(eventId: String) {
        self.eventId = eventId
    }

    func perform() async throws -> some IntentResult {
        #if !WIDGET_EXTENSION
        let resolved = eventId.isEmpty ? nil : eventId
        await CameraLaunch.shared.open(eventId: resolved)
        #endif
        return .result()
    }
}
