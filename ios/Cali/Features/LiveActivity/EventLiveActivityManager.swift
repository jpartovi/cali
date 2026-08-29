//
//  EventLiveActivityManager.swift
//  Cali
//

import ActivityKit
import Foundation

@MainActor
final class EventLiveActivityManager {
    static let shared = EventLiveActivityManager()

    private let maxConcurrentActivities = 8
    private let upcomingWindow: TimeInterval = 24 * 60 * 60
    private let dismissedDefaultsKey = "liveActivity.dismissedEventEndDates"
    private var upcomingStartTasks: [String: Task<Void, Never>] = [:]

    private init() {}

    func sync(events: [DisplayEvent], now: Date = Date()) {
        guard ActivityAuthorizationInfo().areActivitiesEnabled else {
            endAll()
            return
        }

        pruneDismissed(now: now)
        let dismissed = dismissedEventIDs()
        let timed = events.compactMap(TimedEventSnapshot.init(displayEvent:))
        let active = timed
            .filter { $0.isActive(at: now) && !dismissed.contains($0.id) }
            .sorted { $0.start < $1.start }
        let activeToShow = Array(active.prefix(maxConcurrentActivities))
        let activeIDs = Set(activeToShow.map(\.id))

        let existing = Dictionary(
            Activity<EventActivityAttributes>.activities.map { activity in
                (activity.attributes.eventId, activity)
            },
            uniquingKeysWith: { first, _ in first }
        )

        for activity in existing.values where !activeIDs.contains(activity.attributes.eventId) {
            let activityToEnd = activity
            Task {
                await activityToEnd.end(nil, dismissalPolicy: .immediate)
            }
        }

        for snapshot in activeToShow {
            if let activity = existing[snapshot.id] {
                updateIfNeeded(activity, snapshot: snapshot)
            } else {
                start(snapshot)
            }
        }

        scheduleUpcomingStarts(timed: timed, dismissed: dismissed, now: now)
    }

    func dismiss(eventId: String) {
        guard !eventId.isEmpty else { return }

        let endDate = Activity<EventActivityAttributes>.activities
            .first(where: { $0.attributes.eventId == eventId })?
            .content.state.endDate
            ?? Date().addingTimeInterval(upcomingWindow)
        rememberDismissed(eventId: eventId, until: endDate)
        upcomingStartTasks[eventId]?.cancel()
        upcomingStartTasks[eventId] = nil

        for activity in Activity<EventActivityAttributes>.activities where activity.attributes.eventId == eventId {
            let activityToEnd = activity
            Task {
                await activityToEnd.end(nil, dismissalPolicy: .immediate)
            }
        }

        Task {
            await LiveActivityPushService().reportDismissed(eventId: eventId)
        }
    }

    func endAll() {
        cancelUpcomingTasks()
        for activity in Activity<EventActivityAttributes>.activities {
            let activityToEnd = activity
            Task {
                await activityToEnd.end(nil, dismissalPolicy: .immediate)
            }
        }
    }

    private func start(_ snapshot: TimedEventSnapshot) {
        guard snapshot.isActive(at: Date()) else { return }
        if dismissedEventIDs().contains(snapshot.id) {
            return
        }
        if Activity<EventActivityAttributes>.activities.contains(where: { $0.attributes.eventId == snapshot.id }) {
            return
        }

        let attributes = EventActivityAttributes(eventId: snapshot.id)
        let state = EventActivityAttributes.ContentState(title: snapshot.title, endDate: snapshot.end)
        let content = ActivityContent(
            state: state,
            staleDate: snapshot.end,
            relevanceScore: relevanceScore(for: snapshot)
        )

        do {
            let activity: Activity<EventActivityAttributes>
            if let pushed = try? Activity.request(attributes: attributes, content: content, pushType: .token) {
                activity = pushed
            } else {
                activity = try Activity.request(attributes: attributes, content: content)
            }
            LiveActivityPushCoordinator.shared.watchActivity(activity)
            Task {
                await LiveActivityPushService().reportStarted(
                    eventId: snapshot.id,
                    title: snapshot.title,
                    endAt: snapshot.end
                )
            }
            Task {
                await activity.end(content, dismissalPolicy: .after(snapshot.end))
            }
        } catch {
            // Request fails when Live Activities are disabled or the system cap is reached.
        }
    }

    private func updateIfNeeded(
        _ activity: Activity<EventActivityAttributes>,
        snapshot: TimedEventSnapshot
    ) {
        let current = activity.content.state
        guard current.title != snapshot.title || current.endDate != snapshot.end else { return }

        let state = EventActivityAttributes.ContentState(title: snapshot.title, endDate: snapshot.end)
        let content = ActivityContent(
            state: state,
            staleDate: snapshot.end,
            relevanceScore: relevanceScore(for: snapshot)
        )
        Task {
            await activity.update(content)
            await activity.end(content, dismissalPolicy: .after(snapshot.end))
        }
    }

    private func scheduleUpcomingStarts(
        timed: [TimedEventSnapshot],
        dismissed: Set<String>,
        now: Date
    ) {
        cancelUpcomingTasks()

        let deadline = now.addingTimeInterval(upcomingWindow)
        for snapshot in timed where snapshot.start > now && snapshot.start <= deadline {
            if dismissed.contains(snapshot.id) {
                continue
            }
            let delay = snapshot.start.timeIntervalSince(now)
            upcomingStartTasks[snapshot.id] = Task { [weak self] in
                do {
                    try await Task.sleep(for: .seconds(delay))
                } catch {
                    return
                }
                guard !Task.isCancelled else { return }
                self?.start(snapshot)
            }
        }
    }

    private func cancelUpcomingTasks() {
        for task in upcomingStartTasks.values {
            task.cancel()
        }
        upcomingStartTasks.removeAll()
    }

    private func relevanceScore(for snapshot: TimedEventSnapshot) -> Double {
        max(snapshot.end.timeIntervalSinceNow, 0)
    }

    private func dismissedEventIDs() -> Set<String> {
        Set(dismissedEndDates().keys)
    }

    private func rememberDismissed(eventId: String, until endDate: Date) {
        var stored = dismissedEndDates()
        stored[eventId] = endDate.timeIntervalSince1970
        UserDefaults.standard.set(stored, forKey: dismissedDefaultsKey)
    }

    private func pruneDismissed(now: Date) {
        let cutoff = now.timeIntervalSince1970
        let stored = dismissedEndDates().filter { $0.value > cutoff }
        UserDefaults.standard.set(stored, forKey: dismissedDefaultsKey)
    }

    private func dismissedEndDates() -> [String: Double] {
        UserDefaults.standard.dictionary(forKey: dismissedDefaultsKey) as? [String: Double] ?? [:]
    }
}

private struct TimedEventSnapshot {
    let id: String
    let title: String
    let start: Date
    let end: Date

    init?(displayEvent: DisplayEvent) {
        guard !displayEvent.isHidden,
              !displayEvent.event.isAllDay,
              let start = displayEvent.event.start?.dateTime,
              let end = displayEvent.event.end?.dateTime,
              start < end else {
            return nil
        }

        self.id = displayEvent.event.id
        let rawTitle = displayEvent.event.title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        self.title = rawTitle.isEmpty ? "Untitled Event" : rawTitle
        self.start = start
        self.end = end
    }

    func isActive(at now: Date) -> Bool {
        start <= now && now < end
    }
}
