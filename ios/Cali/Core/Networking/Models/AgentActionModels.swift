//
//  AgentActionModels.swift
//  Cali
//
//  Created by Auto on 11/12/25.
//

import Foundation

struct AgentActionRequest {
    let query: String
    /// request_id of the proposal still on screen when this query changes it (a follow-up)
    let followUpOf: String?

    init(query: String, followUpOf: String? = nil) {
        self.query = query
        self.followUpOf = followUpOf
    }
}

struct TranscriptionRequest {
    let fileURL: URL
    
    init(fileURL: URL) {
        self.fileURL = fileURL
    }
    
    var filename: String {
        let name = fileURL.lastPathComponent
        return name.isEmpty ? "recording.wav" : name
    }
}

struct TranscriptionResponse: Codable {
    let text: String
}

struct TranscriptionResult {
    let statusCode: Int
    let text: String
}

struct AgentActionResult {
    let statusCode: Int
    let data: Data
    let agentResponse: AgentResponse
    /// The backend's id for this agent run (also its LangSmith trace id), sent with feedback
    var requestId: String? = nil

    var responseString: String? {
        String(data: data, encoding: .utf8)
    }
}

struct ServerError: Error {
    let statusCode: Int
    let message: String
}
