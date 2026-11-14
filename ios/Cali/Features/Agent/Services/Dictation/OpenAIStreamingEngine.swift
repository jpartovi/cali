//
//  OpenAIStreamingEngine.swift
//  Cali
//
//  OpenAI's Realtime API in a transcription-only session (wss://api.openai.com/v1/realtime).
//  The backend's ephemeral key fixes the session: a live transcription model, 24 kHz PCM, no
//  turn detection. Audio goes in as base64 "input_audio_buffer.append" events, text streams
//  back as deltas while the user talks, and "input_audio_buffer.commit" on release yields
//  the completed transcript.
//

import Foundation

@MainActor
final class OpenAIStreamingEngine: StreamingTranscriptionEngine {
    let provider = "openai"
    let sampleRate: Double = 24_000

    private var text = ""

    func makeRequest(token: String) -> URLRequest {
        var request = URLRequest(url: URL(string: "wss://api.openai.com/v1/realtime")!)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return request
    }

    let audioFramer: @Sendable (Data) -> URLSessionWebSocketTask.Message = { pcm in
        .string(#"{"type":"input_audio_buffer.append","audio":""# + pcm.base64EncodedString() + #""}"#)
    }

    let finishMessages: [URLSessionWebSocketTask.Message] = [.string(#"{"type":"input_audio_buffer.commit"}"#)]
    let closeMessages: [URLSessionWebSocketTask.Message] = []

    func reset() {
        text = ""
    }

    private struct Event: Decodable {
        struct ErrorInfo: Decodable { let message: String? }
        let type: String
        let delta: String?
        let transcript: String?
        let error: ErrorInfo?
    }

    func handle(_ data: Data) -> Bool {
        guard let event = try? JSONDecoder().decode(Event.self, from: data) else { return false }
        switch event.type {
        case "conversation.item.input_audio_transcription.delta":
            text += event.delta ?? ""
            return false
        case "conversation.item.input_audio_transcription.completed":
            if let transcript = event.transcript { text = transcript }
            return true
        case "conversation.item.input_audio_transcription.failed", "error":
            // e.g. a release with no audio; keep whatever streamed in.
            print("OpenAI dictation \(event.type): \(event.error?.message ?? "")")
            return true
        default:
            return false
        }
    }

    var transcript: String { text }
}
