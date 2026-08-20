//
//  AgentActionModels.swift
//  Cali
//
//  Created by Auto on 11/12/25.
//

import Foundation

struct AgentActionRequest {
    let query: String
    
    init(query: String) {
        self.query = query
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

    var responseString: String? {
        String(data: data, encoding: .utf8)
    }
}

struct ServerError: Error {
    let statusCode: Int
    let message: String
}
