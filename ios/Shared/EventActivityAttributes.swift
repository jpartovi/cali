//
//  EventActivityAttributes.swift
//  Cali
//

import ActivityKit
import Foundation

struct EventActivityAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable, Sendable {
        var title: String
        var endDate: Date
    }

    var eventId: String
}
