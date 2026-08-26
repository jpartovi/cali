//
//  LiveActivityPushService.swift
//  Cali
//

import Foundation
import os

private let liveActivityPushLogger = Logger(subsystem: "com.cali.app", category: "LiveActivityPush")

struct LiveActivityPushService {
    private let baseURL: URL
    private let urlSession: URLSession

    init(baseURL: URL = AppConfiguration.backendURL, urlSession: URLSession = NetworkSession.shared) {
        self.baseURL = baseURL
        self.urlSession = urlSession
    }

    func uploadPushToStartToken(_ token: String) async {
        await post(path: "/api/v1/live-activities/push-to-start-token", body: ["token": token])
    }

    func uploadActivityToken(eventId: String, token: String) async {
        await post(path: "/api/v1/live-activities/activity-token", body: ["eventId": eventId, "token": token])
    }

    func deletePushToStartToken() async {
        guard let accessToken = await AuthTokenProvider.shared.currentAccessToken() else {
            return
        }
        do {
            var request = try makeRequest(
                path: "/api/v1/live-activities/push-to-start-token",
                accessToken: accessToken,
                method: "DELETE"
            )
            let (_, response) = try await urlSession.data(for: request)
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                liveActivityPushLogger.error("Failed to delete push-to-start token: HTTP \(http.statusCode)")
            }
        } catch {
            liveActivityPushLogger.error("Failed to delete push-to-start token: \(String(describing: error))")
        }
    }

    private func post(path: String, body: [String: String]) async {
        guard let accessToken = await AuthTokenProvider.shared.currentAccessToken() else {
            return
        }
        do {
            var request = try makeRequest(path: path, accessToken: accessToken, method: "POST")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            let (_, response) = try await urlSession.data(for: request)
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                liveActivityPushLogger.error("Live Activity token upload failed: HTTP \(http.statusCode) \(path, privacy: .public)")
            }
        } catch {
            liveActivityPushLogger.error("Live Activity token upload failed: \(String(describing: error))")
        }
    }

    private func makeRequest(path: String, accessToken: String, method: String) throws -> URLRequest {
        guard let url = URL(string: path, relativeTo: baseURL) else {
            throw URLError(.badURL)
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        return request
    }
}
