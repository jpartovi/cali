//
//  AgentRatingService.swift
//  Cali
//

import Foundation

enum AgentRating: String {
    case good
    case bad
}

/// An agent request the user can rate, kept from its response.
struct RatableAgentRun: Equatable {
    let requestId: String
    let query: String
    let responseData: Data
}

enum AgentRatingServiceError: Error {
    case invalidURL
    case http(Int)
}

/// Saves a thumbs up/down on an agent result; the backend records the calendar alongside it.
struct AgentRatingService {
    func rate(_ run: RatableAgentRun, rating: AgentRating, note: String? = nil, accessToken: String) async throws {
        guard let endpoint = URL(string: "/api/v1/agent/ratings", relativeTo: AppConfiguration.backendURL) else {
            throw AgentRatingServiceError.invalidURL
        }

        var body: [String: Any] = [
            "request_id": run.requestId,
            "rating": rating.rawValue,
            "query": run.query,
            "app_version": Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "",
            "app_build": Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "",
        ]
        if let note, !note.isEmpty {
            body["note"] = note
        }
        if let response = try? JSONSerialization.jsonObject(with: run.responseData) {
            body["response"] = response
        }

        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.addValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        try await send(request)
    }

    /// Removes the rating (and its note) when the user deselects their thumb.
    func removeRating(for run: RatableAgentRun, accessToken: String) async throws {
        guard let endpoint = URL(string: "/api/v1/agent/ratings/\(run.requestId)", relativeTo: AppConfiguration.backendURL) else {
            throw AgentRatingServiceError.invalidURL
        }
        var request = URLRequest(url: endpoint)
        request.httpMethod = "DELETE"
        request.addValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        try await send(request)
    }

    private func send(_ request: URLRequest) async throws {
        let (_, response) = try await NetworkSession.shared.data(for: request)
        let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard 200..<300 ~= statusCode else {
            throw AgentRatingServiceError.http(statusCode)
        }
    }
}
