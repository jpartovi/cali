//
//  SnapshotScheduleStore.swift
//  Cali
//

import Foundation
import os
import Supabase

private let snapshotLogger = Logger(subsystem: "com.cali.app", category: "SnapshotSchedule")

extension Notification.Name {
    /// Posted after the app shows or hides a calendar; userInfo has "googleCalendarId" and "isHidden"
    static let calendarVisibilityChanged = Notification.Name("calendarVisibilityChanged")
}

/// The schedule, read from `calendar_event_snapshots`, which the backend keeps in sync with Google.
///
/// The live window (yesterday through two weeks ahead) loads at start. Days around the visible
/// ones then load through the backend, which lists any it doesn't have from Google once and keeps
/// them in sync from then on.
/// Realtime applies changes row by row; after a reconnect or a return to the foreground the store
/// catches up on rows changed since the newest one it has seen, instead of reloading everything.
@MainActor
final class SnapshotScheduleStore {
    var onEventsChanged: (([DisplayEvent]) -> Void)?
    /// Days (start of day in `timeZone`) being loaded from the backend
    var onLoadingDaysChanged: ((Set<Date>) -> Void)?
    /// The user's timezone, which day boundaries follow (the backend uses the same one)
    var timeZone: TimeZone = .autoupdatingCurrent {
        didSet { if timeZone != oldValue { loadedDays.removeAll() } }
    }

    private let client: SupabaseClient
    private let calendarService: CalendarServicing
    private var snapshotsById: [UUID: EventSnapshotRow] = [:]
    private var calendarsById: [UUID: CalendarMetaRow] = [:]
    private var displayCache: [UUID: DisplayEvent] = [:]
    /// Days loaded through the backend this session; Realtime keeps them current
    private var loadedDays = Set<Date>()
    private var loadingDays = Set<Date>() {
        didSet { if loadingDays != oldValue { onLoadingDaysChanged?(loadingDays) } }
    }
    /// Newest `updated_at` seen, where a catch-up starts
    private var newestUpdate: Date?
    private var channel: RealtimeChannelV2?
    private var realtimeSubscriptions = Set<RealtimeSubscription>()
    private var isStarted = false
    private var hasLoadedWindow = false
    private var startTask: Task<Void, Error>?
    private var realtimeTask: Task<Void, Never>?
    private var publishTask: Task<Void, Never>?
    private var visibilityObserver: NSObjectProtocol?
    private var channelWasSubscribed = false

    /// Matches the backend's listener window (calendar_listener/snapshots.py HORIZON_*)
    private static let liveWindowPast: TimeInterval = 24 * 60 * 60
    private static let liveWindowFuture: TimeInterval = 14 * 24 * 60 * 60
    /// Days loaded on either side of the visible ones, so short swipes find them ready
    private static let prefetchDays = 7
    /// The backend loads at most this many days per request
    private static let maxFillDays = 62
    /// Rows written within this long before the newest seen are re-read on catch-up (commit lag)
    private static let catchUpOverlap: TimeInterval = 5

    init(client: SupabaseClient? = nil, calendarService: CalendarServicing? = nil) {
        self.client = client ?? AuthTokenProvider.shared.client
        self.calendarService = calendarService ?? CalendarService()
        visibilityObserver = NotificationCenter.default.addObserver(
            forName: .calendarVisibilityChanged,
            object: nil,
            queue: .main
        ) { [weak self] note in
            guard let googleCalendarId = note.userInfo?["googleCalendarId"] as? String,
                  let isHidden = note.userInfo?["isHidden"] as? Bool else { return }
            MainActor.assumeIsolated {
                self?.applyVisibility(googleCalendarId: googleCalendarId, isHidden: isHidden)
            }
        }
    }

    deinit {
        if let visibilityObserver {
            NotificationCenter.default.removeObserver(visibilityObserver)
        }
    }

    func start() async throws {
        if let startTask {
            try await startTask.value
            return
        }
        let task = Task { @MainActor in
            try await self.refresh()
            self.connectRealtimeInBackground()
        }
        startTask = task
        do {
            try await task.value
        } catch {
            startTask = nil
            throw error
        }
    }

    /// Realtime keeps the schedule live, but joining a channel can stall for a long time
    /// (e.g. during a Supabase Realtime incident), so loading never waits on it: the schedule
    /// comes from `refresh()`, and Realtime connects here in the background, retrying with
    /// backoff. Once connected it catches up on anything that changed meanwhile.
    private func connectRealtimeInBackground() {
        guard realtimeTask == nil, !isStarted else { return }
        realtimeTask = Task { @MainActor [weak self] in
            var delaySeconds: UInt64 = 2
            while !Task.isCancelled {
                guard let self, !self.isStarted else { break }
                do {
                    try await self.subscribe()
                    self.isStarted = true
                    try? await self.refresh()
                } catch {
                    snapshotLogger.error("Realtime subscribe failed, retrying in \(delaySeconds)s: \(String(describing: error), privacy: .public)")
                    try? await Task.sleep(nanoseconds: delaySeconds * 1_000_000_000)
                    delaySeconds = min(delaySeconds * 2, 60)
                }
            }
            self?.realtimeTask = nil
        }
    }

    /// Load the live window the first time; afterwards fetch only rows changed since the newest seen.
    func refresh() async throws {
        let userId = try await currentUserId()
        let calendars: [CalendarMetaRow] = try await client
            .from("calendars")
            .select("id,google_calendar_id,color,is_hidden")
            .eq("user_id", value: userId)
            .execute()
            .value
        replaceCalendars(calendars)

        if hasLoadedWindow, let newestUpdate {
            let since = newestUpdate.addingTimeInterval(-Self.catchUpOverlap)
            let changed: [EventSnapshotRow] = try await fetchAllRows { query in
                query.gt("updated_at", value: SnapshotJSON.string(from: since))
            }
            changed.forEach(store)
            snapshotLogger.debug("Caught up on \(changed.count, privacy: .public) changed snapshots")
        } else {
            let now = Date()
            let windowStart = now.addingTimeInterval(-Self.liveWindowPast)
            let windowEnd = now.addingTimeInterval(Self.liveWindowFuture)
            let rows: [EventSnapshotRow] = try await fetchAllRows { query in
                query
                    .gte("end_at", value: SnapshotJSON.string(from: windowStart))
                    .lt("start_at", value: SnapshotJSON.string(from: windowEnd))
            }
            rows.forEach(store)
            hasLoadedWindow = true
        }
        publish()
    }

    /// Make sure the days around the visible ones are loaded, fetching any that aren't.
    func ensureDays(firstVisible: Date, visibleCount: Int) async {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let first = calendar.startOfDay(for: firstVisible)
        let span = max(visibleCount, 1) + Self.prefetchDays * 2
        let days = (0..<span).compactMap {
            calendar.date(byAdding: .day, value: $0 - Self.prefetchDays, to: first)
        }
        // Live-window days too, once a session: they show at once from the initial load, but
        // calendars Google can't watch (e.g. holidays) only reach snapshots this way
        let missing = days.filter { !loadedDays.contains($0) && !loadingDays.contains($0) }
        guard let start = missing.min(), let end = missing.max() else { return }
        // Load the whole span between the missing days (already-loaded ones are a cheap read)
        let requested = days.filter { $0 >= start && $0 <= end }
        guard requested.count <= Self.maxFillDays else { return }
        await fill(days: requested, calendar: calendar, force: false)
    }

    private func fill(days: [Date], calendar: Calendar, force: Bool) async {
        guard let first = days.first, let last = days.last,
              let token = await AuthTokenProvider.shared.currentAccessToken() else { return }
        loadingDays.formUnion(days)
        defer { loadingDays.subtract(days) }
        let formatter = SnapshotJSON.dayFormatter(timeZone: calendar.timeZone)
        do {
            let result = try await calendarService.fillSnapshots(
                accessToken: token,
                startDate: formatter.string(from: first),
                endDate: formatter.string(from: last),
                force: force
            )
            for record in result.snapshots {
                if let row = decodeRow(record) { store(row) }
            }
            if result.complete {
                loadedDays.formUnion(days)
            }
            publish()
        } catch {
            snapshotLogger.error("Loading days failed: \(String(describing: error), privacy: .public)")
        }
    }

    /// Show a row the backend just stored for an edit made in the app, without waiting for Realtime.
    func apply(record: [String: AnyJSON]) {
        guard let row = decodeRow(record) else { return }
        store(row)
        publish()
    }

    /// Hide an event the app just deleted (and, for a series, its occurrences).
    func markDeleted(googleCalendarId: String, googleEventId: String) {
        let ids = snapshotsById.values
            .filter {
                $0.googleCalendarId == googleCalendarId
                    && ($0.googleEventId == googleEventId || $0.recurringEventId == googleEventId)
            }
            .map(\.id)
        for id in ids {
            snapshotsById[id]?.status = "cancelled"
            displayCache.removeValue(forKey: id)
        }
        publish()
    }

    func stop() async {
        realtimeTask?.cancel()
        realtimeTask = nil
        isStarted = false
        startTask = nil
        realtimeSubscriptions.removeAll()
        if let channel {
            await channel.unsubscribe()
        }
        channel = nil
    }

    private func currentUserId() async throws -> String {
        let session = try await client.auth.session
        return session.user.id.uuidString.lowercased()
    }

    /// Page through snapshot rows (PostgREST returns at most 1000 per request).
    private func fetchAllRows(
        _ filter: (PostgrestFilterBuilder) -> PostgrestFilterBuilder
    ) async throws -> [EventSnapshotRow] {
        let userId = try await currentUserId()
        let pageSize = 1000
        var rows: [EventSnapshotRow] = []
        while true {
            let page: [EventSnapshotRow] = try await filter(
                client.from("calendar_event_snapshots").select().eq("user_id", value: userId)
            )
            .order("id")
            .range(from: rows.count, to: rows.count + pageSize - 1)
            .execute()
            .value
            rows.append(contentsOf: page)
            if page.count < pageSize { return rows }
        }
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
            let store = self
            Task { @MainActor in
                store?.applySnapshotChange(action)
            }
        }
        listenToPostgresChanges(channel, table: "calendars") { [weak self] action in
            let store = self
            Task { @MainActor in
                store?.applyCalendarChange(action)
            }
        }

        // supabase-swift appends postgres filters on a later MainActor turn.
        await Task { @MainActor in }.value

        do {
            try await channel.subscribeWithError()
        } catch {
            // Drop the half-joined channel so the next attempt starts clean
            await client.removeChannel(channel)
            throw error
        }
        snapshotLogger.info("Subscribed to calendar snapshots for \(userId, privacy: .public)")
        self.channel = channel

        // The channel rejoins by itself after a dropped connection; changes made while it was
        // down never arrive as events, so catch up on them when it is back
        channelWasSubscribed = true
        channel.onStatusChange { [weak self] status in
            let store = self
            Task { @MainActor in
                store?.channelStatusChanged(status)
            }
        }
        .store(in: &realtimeSubscriptions)
    }

    private func channelStatusChanged(_ status: RealtimeChannelStatus) {
        let rejoined = status == .subscribed && !channelWasSubscribed
        channelWasSubscribed = status == .subscribed
        if rejoined {
            Task { try? await refresh() }
        }
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
            if let row = decodeRow(action.record) { store(row) }
        case .update(let action):
            if let row = decodeRow(action.record) { store(row) }
        case .delete(let action):
            if let id = (try? EventSnapshotRow.decode(from: action.oldRecord))?.id
                ?? EventSnapshotRow.id(from: action.oldRecord) {
                snapshotsById.removeValue(forKey: id)
                displayCache.removeValue(forKey: id)
            }
        }
        schedulePublish()
    }

    private func decodeRow(_ record: [String: AnyJSON]) -> EventSnapshotRow? {
        do {
            return try EventSnapshotRow.decode(from: record)
        } catch {
            snapshotLogger.error("Dropped snapshot row: \(error.localizedDescription, privacy: .public)")
            return nil
        }
    }

    /// Keep a row unless the store already has a newer version of it.
    private func store(_ row: EventSnapshotRow) {
        if let existing = snapshotsById[row.id],
           let existingUpdate = existing.updatedAt, let rowUpdate = row.updatedAt,
           existingUpdate > rowUpdate {
            return
        }
        snapshotsById[row.id] = row
        displayCache.removeValue(forKey: row.id)
        if let updated = row.updatedAt, newestUpdate.map({ updated > $0 }) ?? true {
            newestUpdate = updated
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
            displayCache.removeAll()
        }
        schedulePublish()
    }

    private func upsertCalendar(from record: [String: AnyJSON]) {
        do {
            upsertCalendar(try CalendarMetaRow.decode(from: record))
        } catch {
            snapshotLogger.error("Dropped realtime calendar: \(error.localizedDescription, privacy: .public)")
        }
    }

    private func replaceCalendars(_ calendars: [CalendarMetaRow]) {
        let next = Dictionary(uniqueKeysWithValues: calendars.map { ($0.id, $0) })
        let becameVisible = next.contains { id, row in
            !row.isHidden && (calendarsById[id]?.isHidden ?? true)
        }
        if !calendarsById.isEmpty, becameVisible { loadedDays.removeAll() }
        if next.mapValues(\.color) != calendarsById.mapValues(\.color) { displayCache.removeAll() }
        calendarsById = next
    }

    private func applyVisibility(googleCalendarId: String, isHidden: Bool) {
        guard let row = calendarsById.values.first(where: { $0.googleCalendarId == googleCalendarId }) else { return }
        upsertCalendar(row.with(isHidden: isHidden))
        publish()
    }

    private func upsertCalendar(_ row: CalendarMetaRow) {
        let previous = calendarsById[row.id]
        calendarsById[row.id] = row
        if previous?.color != row.color { displayCache.removeAll() }
        if previous == nil || (previous?.isHidden == true && !row.isHidden) {
            // Days loaded before don't include this calendar's events yet
            loadedDays.removeAll()
        }
    }

    /// Realtime changes arrive in bursts (a day loading, a series changing): publish once per burst.
    private func schedulePublish() {
        guard publishTask == nil else { return }
        publishTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 30_000_000)
            self?.publishTask = nil
            self?.publish()
        }
    }

    private func publish() {
        publishTask?.cancel()
        publishTask = nil
        let calendarByGoogleId = Dictionary(
            calendarsById.values.map { ($0.googleCalendarId, $0) },
            uniquingKeysWith: { first, _ in first }
        )

        var events: [DisplayEvent] = []
        events.reserveCapacity(snapshotsById.count)
        for (id, snapshot) in snapshotsById {
            guard snapshot.status != "cancelled" else { continue }
            guard calendarByGoogleId[snapshot.googleCalendarId]?.isHidden != true else { continue }
            if let cached = displayCache[id] {
                events.append(cached)
                continue
            }
            let event = snapshot.asCalendarEvent(
                calendarColor: calendarByGoogleId[snapshot.googleCalendarId]?.color
            )
            let display = DisplayEvent(event: event)
            displayCache[id] = display
            events.append(display)
        }
        events.sort { lhs, rhs in
            SnapshotScheduleMapping.startDate(of: lhs) < SnapshotScheduleMapping.startDate(of: rhs)
        }

        onEventsChanged?(events)
    }
}

private struct CalendarMetaRow: Decodable, Equatable {
    let id: UUID
    let googleCalendarId: String
    let color: String?
    let isHidden: Bool

    func with(isHidden: Bool) -> CalendarMetaRow {
        CalendarMetaRow(id: id, googleCalendarId: googleCalendarId, color: color, isHidden: isHidden)
    }

    init(id: UUID, googleCalendarId: String, color: String?, isHidden: Bool) {
        self.id = id
        self.googleCalendarId = googleCalendarId
        self.color = color
        self.isHidden = isHidden
    }

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
    let recurringEventId: String?
    var status: String
    let title: String?
    let location: String?
    let startAt: Date?
    let endAt: Date?
    let isAllDay: Bool
    let timezone: String?
    let organizerEmail: String?
    let attendees: [SnapshotAttendee]
    let isPhotoWorthy: Bool
    let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id
        case googleCalendarId = "google_calendar_id"
        case googleEventId = "google_event_id"
        case recurringEventId = "recurring_event_id"
        case status
        case title
        case location
        case startAt = "start_at"
        case endAt = "end_at"
        case isAllDay = "is_all_day"
        case timezone
        case organizerEmail = "organizer_email"
        case attendees
        case isPhotoWorthy = "is_photo_worthy"
        case updatedAt = "updated_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(UUID.self, forKey: .id)
        googleCalendarId = try container.decode(String.self, forKey: .googleCalendarId)
        googleEventId = try container.decode(String.self, forKey: .googleEventId)
        recurringEventId = try container.decodeIfPresent(String.self, forKey: .recurringEventId)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "confirmed"
        title = try container.decodeIfPresent(String.self, forKey: .title)
        location = try container.decodeIfPresent(String.self, forKey: .location)
        startAt = try container.decodeIfPresent(Date.self, forKey: .startAt)
        endAt = try container.decodeIfPresent(Date.self, forKey: .endAt)
        isAllDay = try container.decodeIfPresent(Bool.self, forKey: .isAllDay) ?? false
        timezone = try container.decodeIfPresent(String.self, forKey: .timezone)
        organizerEmail = try container.decodeIfPresent(String.self, forKey: .organizerEmail)
        attendees = try container.decodeIfPresent([SnapshotAttendee].self, forKey: .attendees) ?? []
        isPhotoWorthy = try container.decodeIfPresent(Bool.self, forKey: .isPhotoWorthy) ?? true
        updatedAt = try container.decodeIfPresent(Date.self, forKey: .updatedAt)
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
            conference: nil,
            isPhotoWorthy: isPhotoWorthy
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
        if case .string(let raw) = record["is_photo_worthy"] {
            record["is_photo_worthy"] = .bool(["true", "t", "1"].contains(raw.lowercased()))
        }
        if case .string(let raw) = record["organizer_self"] {
            record["organizer_self"] = .bool(["true", "t", "1"].contains(raw.lowercased()))
        }
        return record
    }

    static func string(from date: Date) -> String {
        fractional.string(from: date)
    }

    static func dayFormatter(timeZone: TimeZone) -> DateFormatter {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
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
