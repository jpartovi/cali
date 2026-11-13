//
//  AppleDictationService.swift
//  Cali
//
//  Dictation with Apple's Speech framework, on-device when the recognizer supports it.
//

import AVFoundation
import Combine
import Foundation
import Speech

@MainActor
final class AppleDictationService: NSObject, DictationService {

    @Published private var partialTranscript: String = ""

    var partialTranscriptPublisher: AnyPublisher<String, Never> {
        $partialTranscript.eraseToAnyPublisher()
    }

    private(set) var isRecording: Bool = false

    private let audioSession = DictationAudioSession()
    private var speechRecognizer: SFSpeechRecognizer?
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var audioEngine: AVAudioEngine?

    private var finalTranscript: String?
    private var finalContinuation: CheckedContinuation<String?, Never>?

    /// Guards against the recognition callback resuming the continuation after cancel/timeout
    private var continuationResumed = false

    private var isSpeechPermissionGranted: Bool?

    override init() {
        super.init()
        speechRecognizer = SFSpeechRecognizer(locale: Locale.current) ?? SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
        speechRecognizer?.delegate = self
    }

    // MARK: - Prewarm

    func prewarm() {
        isSpeechPermissionGranted = SFSpeechRecognizer.authorizationStatus() == .authorized
        // Silently fail — retried when recording starts
        try? audioSession.configure()
    }

    // MARK: - Start Recording

    func startRecording() async throws {
        guard !isRecording else { return }

        try await requestSpeechPermissionIfNeeded()
        try await DictationAudioSession.requestMicrophonePermissionIfNeeded()

        guard let recognizer = speechRecognizer, recognizer.isAvailable else {
            throw DictationError.recognizerUnavailable
        }

        try audioSession.activate()

        // Create recognition request
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.addsPunctuation = true
        if recognizer.supportsOnDeviceRecognition {
            request.requiresOnDeviceRecognition = true
        }

        self.recognitionRequest = request

        // Reset state
        finalTranscript = nil
        partialTranscript = ""
        continuationResumed = false

        // Start recognition task
        recognitionTask = recognizer.recognitionTask(with: request) { [weak self] result, error in
            Task { @MainActor [weak self] in
                guard let self, self.isRecording || self.finalContinuation != nil else { return }

                if let result {
                    let text = result.bestTranscription.formattedString
                    if result.isFinal {
                        self.finalTranscript = text
                        self.partialTranscript = text
                        self.resumeContinuation(with: text)
                    } else {
                        self.partialTranscript = text
                    }
                }

                if let error, self.finalTranscript == nil {
                    print("Speech recognition error: \(error.localizedDescription)")
                    let fallback = self.partialTranscript.isEmpty ? nil : self.partialTranscript
                    self.resumeContinuation(with: fallback)
                }
            }
        }

        // Always create a fresh audio engine to avoid stale input node state
        let engine = AVAudioEngine()
        self.audioEngine = engine

        let inputNode = engine.inputNode
        let recordingFormat = inputNode.outputFormat(forBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: recordingFormat) { [weak self] buffer, _ in
            self?.recognitionRequest?.append(buffer)
        }

        engine.prepare()
        do {
            try engine.start()
        } catch {
            inputNode.removeTap(onBus: 0)
            self.audioEngine = nil
            throw DictationError.audioEngineError(error)
        }

        isRecording = true
    }

    // MARK: - Stop Recording

    func stopRecording() async throws -> String? {
        guard isRecording else { return nil }
        isRecording = false

        // Stop audio engine and discard it — a fresh one is created on next startRecording()
        audioEngine?.stop()
        audioEngine?.inputNode.removeTap(onBus: 0)
        audioEngine = nil

        // End the audio stream so the recognizer can deliver its final result
        recognitionRequest?.endAudio()
        recognitionRequest = nil

        // If we already have a final transcript, return it immediately
        if let final = finalTranscript {
            cancelRecognitionTask()
            audioSession.deactivate()
            return final
        }

        // Wait for the final result callback, with a timeout
        let transcript: String? = await withCheckedContinuation { continuation in
            self.finalContinuation = continuation
            self.continuationResumed = false

            // Timeout after 1.5 seconds — use whatever partial we have
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                let fallback = self.partialTranscript.isEmpty ? nil : self.partialTranscript
                self.resumeContinuation(with: fallback)
            }
        }

        cancelRecognitionTask()
        audioSession.deactivate()
        return transcript
    }

    // MARK: - Cleanup

    func cleanup() {
        if isRecording {
            audioEngine?.stop()
            audioEngine?.inputNode.removeTap(onBus: 0)
            recognitionRequest?.endAudio()
            isRecording = false
        }
        audioEngine = nil
        recognitionRequest = nil
        cancelRecognitionTask()
        audioSession.deactivate()
    }

    // MARK: - Private

    /// Thread-safe continuation resume — ensures we only resume once
    private func resumeContinuation(with value: String?) {
        guard !continuationResumed, let continuation = finalContinuation else { return }
        continuationResumed = true
        finalContinuation = nil
        continuation.resume(returning: value)
    }

    /// Cancel (not finish) the recognition task — avoids the RPC deadlock that .finish() causes
    private func cancelRecognitionTask() {
        recognitionTask?.cancel()
        recognitionTask = nil
    }

    private func requestSpeechPermissionIfNeeded() async throws {
        guard isSpeechPermissionGranted != true else { return }
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized:
            isSpeechPermissionGranted = true
        case .notDetermined:
            let granted = await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
                SFSpeechRecognizer.requestAuthorization { status in
                    continuation.resume(returning: status == .authorized)
                }
            }
            isSpeechPermissionGranted = granted
            if !granted { throw DictationError.speechPermissionDenied }
        default:
            isSpeechPermissionGranted = false
            throw DictationError.speechPermissionDenied
        }
    }
}

// MARK: - SFSpeechRecognizerDelegate

extension AppleDictationService: SFSpeechRecognizerDelegate {
    nonisolated func speechRecognizer(_ speechRecognizer: SFSpeechRecognizer, availabilityDidChange available: Bool) {
        Task { @MainActor in
            if !available && self.isRecording {
                print("Speech recognizer became unavailable during recording")
            }
        }
    }
}
