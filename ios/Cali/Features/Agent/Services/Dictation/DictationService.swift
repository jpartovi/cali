//
//  DictationService.swift
//  Cali
//
//  The one seam between the mic button and whichever speech-to-text engine turns the
//  user's voice into a prompt. To try another engine, add a DictationProvider case: for a
//  streaming cloud API, that's a StreamingTranscriptionEngine (plus a token function in the
//  backend's domains/transcription/streaming.py); nothing else in the app changes.
//

import AVFoundation
import Combine
import Foundation

/// Live dictation: listen while the mic is held, show partial text, return the final text on release.
@MainActor
protocol DictationService: AnyObject {
    var isRecording: Bool { get }
    /// Best guess at what has been said so far, for the live transcript under the mic.
    var partialTranscriptPublisher: AnyPublisher<String, Never> { get }
    /// Cheap setup done ahead of the first press (audio session, permissions, tokens).
    func prewarm()
    func startRecording() async throws
    /// Stops listening and returns the final transcript, or nil when nothing was heard.
    func stopRecording() async throws -> String?
    /// Tears everything down and gives the audio session back to other apps.
    func cleanup()
}

/// Which engine dictation uses. Set `DictationProvider` in Info.plist or `DICTATION_PROVIDER`
/// in the Xcode scheme to override the compiled default.
enum DictationProvider: String, CaseIterable {
    /// Apple's Speech framework, on-device when available.
    case apple
    /// Deepgram's live streaming API, falling back to Apple when it can't be reached.
    case deepgram
    /// OpenAI's Realtime transcription API, falling back to Apple when it can't be reached.
    case openai

    static let compiledDefault: DictationProvider = .openai

    static var configured: DictationProvider {
        AppConfiguration.dictationProvider.flatMap { DictationProvider(rawValue: $0.lowercased()) }
            ?? compiledDefault
    }

    func makeService() -> DictationService {
        switch self {
        case .apple:
            return AppleDictationService()
        case .deepgram:
            return Self.streaming(DeepgramStreamingEngine())
        case .openai:
            return Self.streaming(OpenAIStreamingEngine())
        }
    }

    private static func streaming(_ engine: StreamingTranscriptionEngine) -> DictationService {
        FallbackDictationService(primary: StreamingDictationService(engine: engine), fallback: AppleDictationService())
    }
}

enum DictationError: Error {
    case microphonePermissionDenied
    case speechPermissionDenied
    case recognizerUnavailable
    case audioEngineError(Error)
    case connectionFailed(Error)

    var userFacingMessage: String {
        switch self {
        case .microphonePermissionDenied:
            return "Microphone access denied. Enable in Settings."
        case .speechPermissionDenied:
            return "Speech recognition access denied. Enable in Settings."
        case .recognizerUnavailable:
            return "Dictation isn't available right now. Try again."
        case .audioEngineError:
            return "Could not start microphone."
        case .connectionFailed:
            return "Couldn't reach dictation. Check your connection and try again."
        }
    }
}

/// Audio session and microphone permission handling shared by every dictation engine.
@MainActor
final class DictationAudioSession {
    private var isConfigured = false
    private var isActive = false

    /// Sets the category without activating, so opening the app never stops the user's music.
    func configure() throws {
        guard !isConfigured else { return }
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .measurement, options: [.defaultToSpeaker, .duckOthers])
        try session.setAllowHapticsAndSystemSoundsDuringRecording(true)
        isConfigured = true
    }

    func activate() throws {
        try configure()
        guard !isActive else { return }
        #if targetEnvironment(simulator)
        try? AVAudioSession.sharedInstance().setActive(true, options: .notifyOthersOnDeactivation)
        #else
        try AVAudioSession.sharedInstance().setActive(true, options: .notifyOthersOnDeactivation)
        #endif
        isActive = true
    }

    /// Releases the audio session so other apps' audio (e.g. music) resumes.
    func deactivate() {
        guard isActive else { return }
        do {
            try AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            isActive = false
        } catch {
            #if targetEnvironment(simulator)
            isActive = false
            #endif
        }
    }

    static var isMicrophonePermissionGranted: Bool {
        AVAudioApplication.shared.recordPermission == .granted
    }

    static func requestMicrophonePermissionIfNeeded() async throws {
        switch AVAudioApplication.shared.recordPermission {
        case .granted:
            return
        case .denied:
            throw DictationError.microphonePermissionDenied
        default:
            guard await AVAudioApplication.requestRecordPermission() else {
                throw DictationError.microphonePermissionDenied
            }
        }
    }
}
