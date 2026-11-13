//
//  FallbackDictationService.swift
//  Cali
//
//  Uses one dictation engine and switches to another for a press when the first can't
//  start (e.g. Deepgram unreachable), so the mic keeps working offline.
//

import Combine
import Foundation

@MainActor
final class FallbackDictationService: DictationService {
    private let primary: DictationService
    private let fallback: DictationService
    private var active: DictationService
    /// Set when the mic is released while a start is still in progress.
    private var stopRequested = false

    @Published private var partialTranscript = ""
    private var cancellables: Set<AnyCancellable> = []

    var partialTranscriptPublisher: AnyPublisher<String, Never> {
        $partialTranscript.eraseToAnyPublisher()
    }

    var isRecording: Bool { active.isRecording }

    init(primary: DictationService, fallback: DictationService) {
        self.primary = primary
        self.fallback = fallback
        self.active = primary
        forwardPartials(from: primary)
        forwardPartials(from: fallback)
    }

    func prewarm() {
        primary.prewarm()
        fallback.prewarm()
    }

    func startRecording() async throws {
        stopRequested = false
        active = primary
        do {
            try await primary.startRecording()
        } catch DictationError.microphonePermissionDenied {
            throw DictationError.microphonePermissionDenied
        } catch {
            print("Dictation: \(type(of: primary)) failed to start (\(error)); using \(type(of: fallback))")
            primary.cleanup()
            // A quick tap may already be over; don't open the mic again after it.
            guard !stopRequested else { throw error }
            active = fallback
            try await fallback.startRecording()
        }
    }

    func stopRecording() async throws -> String? {
        stopRequested = true
        return try await active.stopRecording()
    }

    func cleanup() {
        stopRequested = true
        primary.cleanup()
        fallback.cleanup()
    }

    private func forwardPartials(from service: DictationService) {
        service.partialTranscriptPublisher
            .sink { [weak self, weak service] text in
                guard let self, let service, service === self.active else { return }
                self.partialTranscript = text
            }
            .store(in: &cancellables)
    }
}
