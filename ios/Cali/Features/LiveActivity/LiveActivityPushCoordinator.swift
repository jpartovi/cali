//
//  LiveActivityPushCoordinator.swift
//  Cali
//

import ActivityKit
import Foundation

@MainActor
final class LiveActivityPushCoordinator {
    static let shared = LiveActivityPushCoordinator()

    private let service = LiveActivityPushService()
    private var didStart = false
    private var observerTasks: [Task<Void, Never>] = []
    private var activityTokenTasks: [String: Task<Void, Never>] = [:]
    private var pendingPushToStartToken: String?
    private var pendingActivityTokens: [String: String] = [:]

    private init() {}

    func watchActivity(_ activity: Activity<EventActivityAttributes>) {
        listenForActivityPushToken(activity)
    }

    func start() {
        guard !didStart else {
            Task { await flushPendingTokens() }
            return
        }
        didStart = true

        observerTasks.append(Task { [weak self] in
            await self?.observePushToStartToken()
        })
        observerTasks.append(Task { [weak self] in
            await self?.observeExistingAndNewActivities()
        })
        Task { await flushPendingTokens() }
    }

    func stopAndClearToken() {
        for task in observerTasks {
            task.cancel()
        }
        observerTasks.removeAll()
        for task in activityTokenTasks.values {
            task.cancel()
        }
        activityTokenTasks.removeAll()
        pendingPushToStartToken = nil
        pendingActivityTokens.removeAll()
        didStart = false

        Task {
            await service.deletePushToStartToken()
        }
    }

    private func observePushToStartToken() async {
        for await tokenData in Activity<EventActivityAttributes>.pushToStartTokenUpdates {
            let token = Self.hexString(from: tokenData)
            guard !token.isEmpty else { continue }
            pendingPushToStartToken = token
            await service.uploadPushToStartToken(token)
        }
    }

    private func observeExistingAndNewActivities() async {
        for activity in Activity<EventActivityAttributes>.activities {
            listenForActivityPushToken(activity)
        }

        for await activity in Activity<EventActivityAttributes>.activityUpdates {
            listenForActivityPushToken(activity)
        }
    }

    private func listenForActivityPushToken(_ activity: Activity<EventActivityAttributes>) {
        let eventId = activity.attributes.eventId
        activityTokenTasks[eventId]?.cancel()
        activityTokenTasks[eventId] = Task { [weak self] in
            for await tokenData in activity.pushTokenUpdates {
                let token = Self.hexString(from: tokenData)
                guard !token.isEmpty else { continue }
                self?.pendingActivityTokens[eventId] = token
                await self?.service.uploadActivityToken(eventId: eventId, token: token)
            }
        }
    }

    private func flushPendingTokens() async {
        if let token = pendingPushToStartToken {
            await service.uploadPushToStartToken(token)
        }
        for (eventId, token) in pendingActivityTokens {
            await service.uploadActivityToken(eventId: eventId, token: token)
        }
    }

    private static func hexString(from data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }
}
