//
//  DeepgramStreamingEngine.swift
//  Cali
//
//  Deepgram's live API (wss://api.deepgram.com/v1/listen): raw PCM frames in, interim and
//  final results out. "Finalize" flushes the last words; "CloseStream" ends the session.
//

import Foundation

@MainActor
final class DeepgramStreamingEngine: StreamingTranscriptionEngine {
    var model = "nova-3"
    var language = "en-US"

    let provider = "deepgram"
    let sampleRate: Double = 16_000

    /// Text Deepgram has committed to, in order, plus its guess for what's being said now.
    private var finalizedSegments: [String] = []
    private var interimSegment = ""

    func makeRequest(token: String) -> URLRequest {
        var components = URLComponents(string: "wss://api.deepgram.com/v1/listen")!
        components.queryItems = [
            URLQueryItem(name: "model", value: model),
            URLQueryItem(name: "language", value: language),
            URLQueryItem(name: "encoding", value: "linear16"),
            URLQueryItem(name: "sample_rate", value: String(Int(sampleRate))),
            URLQueryItem(name: "channels", value: "1"),
            URLQueryItem(name: "interim_results", value: "true"),
            URLQueryItem(name: "smart_format", value: "true"),
            URLQueryItem(name: "punctuate", value: "true"),
        ]
        var request = URLRequest(url: components.url!)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return request
    }

    let audioFramer: @Sendable (Data) -> URLSessionWebSocketTask.Message = { .data($0) }

    let finishMessages: [URLSessionWebSocketTask.Message] = [.string(#"{"type":"Finalize"}"#)]
    let closeMessages: [URLSessionWebSocketTask.Message] = [.string(#"{"type":"CloseStream"}"#)]

    func reset() {
        finalizedSegments = []
        interimSegment = ""
    }

    private struct Message: Decodable {
        struct Channel: Decodable {
            struct Alternative: Decodable { let transcript: String }
            let alternatives: [Alternative]
        }
        let type: String?
        let channel: Channel?
        let is_final: Bool?
        let from_finalize: Bool?
    }

    func handle(_ data: Data) -> Bool {
        guard let message = try? JSONDecoder().decode(Message.self, from: data),
              message.type == "Results",
              let text = message.channel?.alternatives.first?.transcript else { return false }

        if message.is_final == true {
            if !text.isEmpty { finalizedSegments.append(text) }
            interimSegment = ""
        } else {
            interimSegment = text
        }
        return message.from_finalize == true
    }

    var transcript: String {
        (finalizedSegments + [interimSegment])
            .filter { !$0.isEmpty }
            .joined(separator: " ")
    }
}
