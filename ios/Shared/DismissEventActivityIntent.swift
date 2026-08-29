//
//  DismissEventActivityIntent.swift
//  Cali
//

import AppIntents

struct DismissEventActivityIntent: LiveActivityIntent {
    static var title: LocalizedStringResource = "Dismiss"
    static var description = IntentDescription("Dismisses the photo reminder for this event.")
    static var openAppWhenRun = false
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
        guard !eventId.isEmpty else {
            return .result()
        }
        await EventLiveActivityManager.shared.dismiss(eventId: eventId)
        #endif
        return .result()
    }
}
