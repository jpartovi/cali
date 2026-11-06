//
//  UserTimeZone.swift
//  Cali
//

import Foundation

/// The timezone saved on the user's Supabase profile. The app shows and reads every time in it, never the phone's,
/// so the schedule, event details and the agent always agree.
enum UserTimeZone {
    private static let defaultsKey = "userTimeZone"

    /// The saved timezone's identifier, remembered from the last fetch so it applies from launch
    static var savedIdentifier: String? {
        UserDefaults.standard.string(forKey: defaultsKey)
    }

    /// The saved timezone; the phone's only until the first fetch
    static var current: TimeZone {
        savedIdentifier.flatMap(TimeZone.init(identifier:)) ?? .autoupdatingCurrent
    }

    /// The user's calendar, in the saved timezone
    static var calendar: Calendar {
        var calendar = Calendar.autoupdatingCurrent
        calendar.timeZone = current
        return calendar
    }

    /// Remember the timezone fetched from Supabase
    static func save(_ identifier: String) {
        guard TimeZone(identifier: identifier) != nil else { return }
        UserDefaults.standard.set(identifier, forKey: defaultsKey)
    }
}
