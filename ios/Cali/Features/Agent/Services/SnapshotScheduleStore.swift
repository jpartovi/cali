//
//  SnapshotScheduleStore.swift
//  Cali
//

import Foundation
import os
import Supabase

private let snapshotLogger = Logger(subsystem: "com.cali.app", category: "SnapshotSchedule")

@MainActor
final class SnapshotScheduleStore {
    var onEventsChanged: (([DisplayEvent]) -> Void)?

    private let client: SupabaseClient
    private var snapshotsById: [UUID: EventSnapshotRow] = [:]
    private var calendarsById: [UUID: CalendarMetaRow] = [:]
    private var channel: RealtimeChannelV2?
    private var realtimeSubscriptions = Set<RealtimeSubscription>()
    private var isStarted = false
    private var startTask: Task<Void, Error>?

    init(client: SupabaseClient? = nil) {
        self.client = client ?? AuthTokenProvider.shared.client
    }

    func start() async throws {
        if let startTask {
            try await startTask.value
            return
        }
        let task = Task { @MainActor in
            try await self.refresh()
            if !self.isStarted {
                try await self.subscribe()
                self.isStarted = true
            }
        }
        startTask = task
        do {
            try await task.value
        } catch {
            startTask = nil
            isStarted = false
            throw error
        }
    }

    func refresh() async throws {
        let session = try await client.auth.session
        let userId = session.user.id.uuidString.lowercased()

        let calendars: [CalendarMetaRow] = try await client
            .from("calendars")
            .select("id,google_calendar_id,color,is_hidden")
            .eq("user_id", value: userId)
            .execute()
            .value

        let snapshots: [EventSnapshotRow] = try await client
            .from("calendar_event_snapshots")
            .select()
            .eq("user_id", value: userId)
            .execute()
            .value

        calendarsById = Dictionary(uniqueKeysWithValues: calendars.map { ($0.id, $0) })
        snapshotsById = Dictionary(uniqueKeysWithValues: snapshots.map { ($0.id, $0) })
        publish()
    }

    func stop() async {
        isStarted = false
        startTask = nil
        realtimeSubscriptions.removeAll()
        if let channel {
            await channel.unsubscribe()
        }
        channel = nil
    }

    private func subscribe() async throws {
        let session = try await client.auth.session
        let userId = session.user.id.uuidString.lowercased()
        await client.realtimeV2.setAuth(session.accessToken)
        let channel = client.channel("calendar-schedule-\(userId)")

        realtimeSubscriptions.removeAll()
        // Keep tokens alive: supabase-swift cancels callbacks when ObservationToken deinits.
        // Listen per event type: AnyAction (`*`) does not match server INSERT/UPDATE/DELETE.
        listenToPostgresChanges(channel, table: "calendar_event_snapshots") { [weak self] action in
            Task { @MainActor in
                self?.applySnapshotChange(action)
            }
        }
        listenToPostgresChanges(channel, table: "calendars") { [weak self] action in
            Task { @MainActor in
                self?.applyCalendarChange(action)
            }
        }

        // supabase-swift appends postgres filters on a later MainActor turn.
        await Task { @MainActor in }.value

        try await channel.subscribeWithError()
        snapshotLogger.info("Subscribed to calendar snapshots for \(userId, privacy: .public)")
        self.channel = channel
    }

    private func listenToPostgresChanges(
        _ channel: RealtimeChannelV2,
        table: String,
        onChange: @escaping @Sendable (AnyAction) -> Void
    ) {
        channel.onPostgresChange(InsertAction.self, schema: "public", table: table) { action in
            onChange(.insert(action))
        }
        .store(in: &realtimeSubscriptions)
        channel.onPostgresChange(UpdateAction.self, schema: "public", table: table) { action in
            onChange(.update(action))
        }
        .store(in: &realtimeSubscriptions)
        channel.onPostgresChange(DeleteAction.self, schema: "public", table: table) { action in
            onChange(.delete(action))
        }
        .store(in: &realtimeSubscriptions)
    }

    private func applySnapshotChange(_ change: AnyAction) {
        switch change {
        case .insert(let action):
            upsertSnapshot(from: action.record)
        case .update(let action):
            upsertSnapshot(from: action.record)
        case .delete(let action):
            if let row = try? EventSnapshotRow.decode(from: action.oldRecord) {
                snapshotsById.removeValue(forKey: row.id)
            } else if let id = EventSnapshotRow.id(from: action.oldRecord) {
                snapshotsById.removeValue(forKey: id)
            }
        }
        publish()
    }

    private func upsertSnapshot(from record: [String: AnyJSON]) {
        do {
            let row = try EventSnapshotRow.decode(from: record)
            snapshotsById[row.id] = row
        } catch {
            snapshotLogger.error("Dropped realtime snapshot: \(error.localizedDescription, privacy: .public)")
        }
    }

    private func applyCalendarChange(_ change: AnyAction) {
        switch change {
        case .insert(let action):
            upsertCalendar(from: action.record)
        case .update(let action):
            upsertCalendar(from: action.record)
        case .delete(let action):
            if let row = try? CalendarMetaRow.decode(from: action.oldRecord) {
                calendarsById.removeValue(forKey: row.id)
            } else if let id = CalendarMetaRow.id(from: action.oldRecord) {
                calendarsById.removeValue(forKey: id)
            }
        }
        publish()
    }

    private func upsertCalendar(from record: [String: AnyJSON]) {
        do {
            let row = try CalendarMetaRow.decode(from: record)
            calendarsById[row.id] = row
        } catch {
            snapshotLogger.error("Dropped realtime calendar: \(error.localizedDescription, privacy: .public)")
        }
    }

    private func publish() {
        let calendarByGoogleId = Dictionary(
            uniqueKeysWithValues: calendarsById.values.map { ($0.googleCalendarId, $0) }
        )
        let hiddenGoogleIds = Set(
            calendarByGoogleId.values.compactMap { $0.isHidden ? $0.googleCalendarId : nil }
        )

        let events = snapshotsById.values.compactMap { snapshot -> DisplayEvent? in
            guard snapshot.status != "cancelled" else { return nil }
            guard !hiddenGoogleIds.contains(snapshot.googleCalendarId) else { return nil }
            let event = snapshot.asCalendarEvent(
                calendarColor: calendarByGoogleId[snapshot.googleCalendarId]?.color
            )
            return DisplayEvent(event: event)
        }
        .sorted { lhs, rhs in
            SnapshotScheduleMapping.startDate(of: lhs) < SnapshotScheduleMapping.startDate(of: rhs)
        }

        onEventsChanged?(events)
    }
}

private struct CalendarMetaRow: Decodable {
    let id: UUID
    let googleCalendarId: String
    let color: String?
    let isHidden: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case googleCalendarId = "google_calendar_id"
        case color
        case isHidden = "is_hidden"
    }

    static func decode(from record: [String: AnyJSON]) throws -> CalendarMetaRow {
        try record.decode(as: CalendarMetaRow.self, decoder: SnapshotJSON.decoder)
    }

    static func id(from record: [String: AnyJSON]) -> UUID? {
        try? record.decode(as: IDOnly.self, decoder: SnapshotJSON.decoder).id
    }
}

private struct EventSnapshotRow: Decodable {
    let id: UUID
    let googleCalendarId: String
    let googleEventId: String
    let status: String
    let title: String?
    let location: String?
    let startAt: Date?
    let endAt: Date?
    let isAllDay: Bool
    let timezone: String?
    let organizerEmail: String?
    let attendees: [SnapshotAttendee]

    enum CodingKeys: String, CodingKey {
        case id
        case googleCalendarId = "google_calendar_id"
        case googleEventId = "google_event_id"
        case status
        case title
        case location
        case startAt = "start_at"
        case endAt = "end_at"
        case isAllDay = "is_all_day"
        case timezone
        case organizerEmail = "organizer_email"
        case attendees
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(UUID.self, forKey: .id)
        googleCalendarId = try container.decode(String.self, forKey: .googleCalendarId)
        googleEventId = try container.decode(String.self, forKey: .googleEventId)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "confirmed"
        title = try container.decodeIfPresent(String.self, forKey: .title)
        location = try container.decodeIfPresent(String.self, forKey: .location)
        startAt = try container.decodeIfPresent(Date.self, forKey: .startAt)
        endAt = try container.decodeIfPresent(Date.self, forKey: .endAt)
        isAllDay = try container.decodeIfPresent(Bool.self, forKey: .isAllDay) ?? false
        timezone = try container.decodeIfPresent(String.self, forKey: .timezone)
        organizerEmail = try container.decodeIfPresent(String.self, forKey: .organizerEmail)
        attendees = try container.decodeIfPresent([SnapshotAttendee].self, forKey: .attendees) ?? []
    }

    static func decode(from record: [String: AnyJSON]) throws -> EventSnapshotRow {
        try SnapshotJSON.normalized(record).decode(as: EventSnapshotRow.self, decoder: SnapshotJSON.decoder)
    }

    static func id(from record: [String: AnyJSON]) -> UUID? {
        try? record.decode(as: IDOnly.self, decoder: SnapshotJSON.decoder).id
    }

    func asCalendarEvent(calendarColor: String?) -> CalendarEvent {
        let start = SnapshotScheduleMapping.eventDateTime(
            at: startAt,
            isAllDay: isAllDay,
            timeZone: timezone
        )
        let end = SnapshotScheduleMapping.eventDateTime(
            at: endAt,
            isAllDay: isAllDay,
            timeZone: timezone
        )
        return CalendarEvent(
            id: googleEventId,
            title: title,
            description: nil,
            start: start,
            end: end,
            attendees: attendees.map {
                CalendarEvent.Attendee(
                    email: $0.email,
                    displayName: nil,
                    responseStatus: $0.responseStatus
                )
            },
            createdBy: organizerEmail.map { CalendarEvent.Person(email: $0, displayName: nil) },
            calendarId: googleCalendarId,
            calendarColor: calendarColor,
            location: location,
            conference: nil
        )
    }
}

private struct SnapshotAttendee: Decodable {
    let email: String?
    let responseStatus: String?

    enum CodingKeys: String, CodingKey {
        case email
        case responseStatus
        case response_status
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        email = try container.decodeIfPresent(String.self, forKey: .email)
        responseStatus = try container.decodeIfPresent(String.self, forKey: .responseStatus)
            ?? container.decodeIfPresent(String.self, forKey: .response_status)
    }
}

private struct IDOnly: Decodable {
    let id: UUID
}

private enum SnapshotJSON {
    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let value = try container.decode(String.self)
            if let date = parseDate(value) {
                return date
            }
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unrecognized date: \(value)"
            )
        }
        return decoder
    }()

    static func normalized(_ record: [String: AnyJSON]) -> [String: AnyJSON] {
        var record = record
        if case .string(let raw) = record["attendees"],
           let data = raw.data(using: .utf8),
           let parsed = try? JSONDecoder().decode(AnyJSON.self, from: data) {
            record["attendees"] = parsed
        }
        if case .string(let raw) = record["is_all_day"] {
            record["is_all_day"] = .bool(["true", "t", "1"].contains(raw.lowercased()))
        }
        if case .string(let raw) = record["organizer_self"] {
            record["organizer_self"] = .bool(["true", "t", "1"].contains(raw.lowercased()))
        }
        return record
    }

    static func parseDate(_ value: String) -> Date? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        let withT = trimmed.replacingOccurrences(of: " ", with: "T")
        let candidates = [trimmed, withT, padTimezone(withT)]
        for candidate in candidates {
            if let date = fractional.date(from: candidate) {
                return date
            }
            if let date = iso.date(from: candidate) {
                return date
            }
        }
        return postgresFormatter.date(from: trimmed) ?? postgresFractionalFormatter.date(from: trimmed)
    }

    private static func padTimezone(_ value: String) -> String {
        if value.hasSuffix("+00") || value.hasSuffix("-00") {
            return value + ":00"
        }
        return value
    }

    private static let fractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let iso: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private static let postgresFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ssxxxxx"
        return formatter
    }()

    private static let postgresFractionalFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss.SSSSSSxxxxx"
        return formatter
    }()
}

private enum SnapshotScheduleMapping {
    static func eventDateTime(
        at date: Date?,
        isAllDay: Bool,
        timeZone: String?
    ) -> CalendarEvent.EventDateTime? {
        guard let date else { return nil }
        if isAllDay {
            return CalendarEvent.EventDateTime(
                eventTime: .allDay(date: dayString(from: date, timeZoneName: timeZone))
            )
        }
        return CalendarEvent.EventDateTime(
            eventTime: .timed(dateTime: date, timeZone: timeZone)
        )
    }

    static func dayString(from date: Date, timeZoneName: String?) -> String {
        let timeZone = timeZoneName.flatMap { TimeZone(identifier: $0) } ?? TimeZone(secondsFromGMT: 0) ?? TimeZone(identifier: "UTC")!
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }

    static func startDate(of displayEvent: DisplayEvent) -> Date {
        if let dateTime = displayEvent.event.start?.dateTime {
            return dateTime
        }
        if let dateString = displayEvent.event.start?.date {
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy-MM-dd"
            formatter.timeZone = .current
            return formatter.date(from: dateString) ?? .distantFuture
        }
        return .distantFuture
    }
}
