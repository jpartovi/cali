//
//  StreamingDictationService.swift
//  Cali
//
//  Dictation that streams the mic to a cloud speech-to-text websocket. Everything that is
//  the same for every vendor lives here (mic, resampling, buffering while connecting,
//  tokens, timeouts); a StreamingTranscriptionEngine supplies the vendor's protocol.
//
//  The mic starts at once and audio is held while the socket connects, so nothing said
//  right after the press is lost. The app never holds a vendor API key: the backend mints
//  a short-lived token (POST /api/v1/agent/transcription/{provider}/token).
//

import AVFoundation
import Combine
import Foundation

/// The vendor-specific half of streaming dictation.
@MainActor
protocol StreamingTranscriptionEngine: AnyObject {
    /// Backend token route segment, e.g. "openai" for /agent/transcription/openai/token.
    var provider: String { get }
    /// Sample rate of the 16-bit mono PCM the vendor expects.
    var sampleRate: Double { get }
    func makeRequest(token: String) -> URLRequest
    /// Wraps one chunk of PCM in the vendor's message. Called on the audio thread.
    var audioFramer: @Sendable (Data) -> URLSessionWebSocketTask.Message { get }
    /// Sent on release, after the last audio, to flush the final words.
    var finishMessages: [URLSessionWebSocketTask.Message] { get }
    /// Sent once the final words are in, before the socket closes.
    var closeMessages: [URLSessionWebSocketTask.Message] { get }
    /// Starts a fresh transcript for a new press.
    func reset()
    /// Reads one server message. Returns true when no more text is coming for this press.
    func handle(_ data: Data) -> Bool
    /// Everything heard so far.
    var transcript: String { get }
}

@MainActor
final class StreamingDictationService: DictationService {

    struct Timeouts {
        /// How long the token fetch and websocket handshake may take before giving up.
        var connect: TimeInterval = 4
        /// How long to wait for the last words after the mic is released.
        var finalize: TimeInterval = 2
    }

    @Published private var partialTranscript: String = ""

    var partialTranscriptPublisher: AnyPublisher<String, Never> {
        $partialTranscript.eraseToAnyPublisher()
    }

    private(set) var isRecording = false

    private let engine: StreamingTranscriptionEngine
    private let timeouts: Timeouts
    private let tokenProvider: StreamingTokenProvider
    private let audioSession = DictationAudioSession()

    private var audioEngine: AVAudioEngine?
    private var pipe: StreamingAudioPipe?
    private var socket: URLSessionWebSocketTask?
    private var connectTask: Task<URLSessionWebSocketTask, Error>?
    private var receiveTask: Task<Void, Never>?

    private var finalizeContinuation: CheckedContinuation<Void, Never>?
    /// Bumped per stop, so a late timer from an earlier recording can't cut this one short.
    private var finalizeGeneration = 0

    init(engine: StreamingTranscriptionEngine, timeouts: Timeouts = Timeouts()) {
        self.engine = engine
        self.timeouts = timeouts
        self.tokenProvider = StreamingTokenProvider.shared(for: engine.provider)
    }

    // MARK: - DictationService

    func prewarm() {
        try? audioSession.configure()
        tokenProvider.prefetch()
    }

    func startRecording() async throws {
        guard !isRecording else { return }

        try await DictationAudioSession.requestMicrophonePermissionIfNeeded()
        try audioSession.activate()

        engine.reset()
        partialTranscript = ""

        let pipe = StreamingAudioPipe()
        self.pipe = pipe
        do {
            try startAudioEngine(feeding: pipe)
        } catch {
            teardown()
            throw DictationError.audioEngineError(error)
        }
        isRecording = true

        let connectTask = Task { try await self.connect(pipe: pipe) }
        self.connectTask = connectTask
        let deadline = Task { [timeout = timeouts.connect] in
            try? await Task.sleep(for: .seconds(timeout))
            connectTask.cancel()
        }
        defer { deadline.cancel() }
        do {
            _ = try await connectTask.value
        } catch {
            // The release may already have torn this recording down; only clean up our own.
            if self.connectTask == connectTask {
                isRecording = false
                teardown()
            }
            throw DictationError.connectionFailed(error)
        }
    }

    func stopRecording() async throws -> String? {
        guard isRecording else { return nil }
        isRecording = false
        stopAudioEngine()

        // The press may have been shorter than the handshake; the held audio is sent once it connects.
        guard let connectTask, let socket = try? await connectTask.value else {
            teardown()
            return nil
        }

        // Nothing more from the mic: anything after this would race the finish messages.
        pipe?.close()
        for message in engine.finishMessages {
            socket.send(message) { _ in }
        }

        finalizeGeneration += 1
        let generation = finalizeGeneration
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            finalizeContinuation = continuation
            let timeout = timeouts.finalize
            Task { @MainActor [weak self] in
                try? await Task.sleep(for: .seconds(timeout))
                guard let self, self.finalizeGeneration == generation else { return }
                self.resumeFinalize()
            }
        }

        let transcript = engine.transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        for message in engine.closeMessages {
            socket.send(message) { _ in }
        }
        teardown()
        return transcript.isEmpty ? nil : transcript
    }

    func cleanup() {
        isRecording = false
        stopAudioEngine()
        teardown()
    }

    // MARK: - Connection

    private func connect(pipe: StreamingAudioPipe) async throws -> URLSessionWebSocketTask {
        let token = try await tokenProvider.token()
        try Task.checkCancellation()

        let socket = URLSession.shared.webSocketTask(with: engine.makeRequest(token: token))
        self.socket = socket
        socket.resume()

        // A pong proves the handshake (and the token) worked before we commit to this engine.
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                socket.sendPing { error in
                    if let error { continuation.resume(throwing: error) } else { continuation.resume() }
                }
            }
        } onCancel: {
            socket.cancel(with: .goingAway, reason: nil)
        }
        try Task.checkCancellation()

        receiveTask = Task { [weak self] in
            while true {
                do {
                    let message = try await socket.receive()
                    guard let self, self.socket === socket else { return }
                    self.handle(message)
                } catch {
                    if self?.socket === socket { self?.resumeFinalize() }
                    return
                }
            }
        }
        let framer = engine.audioFramer
        pipe.attach { data in
            socket.send(framer(data)) { _ in }
        }
        return socket
    }

    private func handle(_ message: URLSessionWebSocketTask.Message) {
        let data: Data
        switch message {
        case .string(let text): data = Data(text.utf8)
        case .data(let raw): data = raw
        @unknown default: return
        }
        let isDone = engine.handle(data)
        partialTranscript = engine.transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        if isDone {
            resumeFinalize()
        }
    }

    private func resumeFinalize() {
        finalizeContinuation?.resume()
        finalizeContinuation = nil
    }

    // MARK: - Audio

    private func startAudioEngine(feeding pipe: StreamingAudioPipe) throws {
        let audioEngine = AVAudioEngine()
        let inputNode = audioEngine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
        guard let encoder = PCM16Encoder(inputFormat: inputFormat, sampleRate: engine.sampleRate) else {
            throw DictationError.recognizerUnavailable
        }
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: inputFormat, block: Self.tapBlock(encoder: encoder, pipe: pipe))
        audioEngine.prepare()
        do {
            try audioEngine.start()
        } catch {
            inputNode.removeTap(onBus: 0)
            throw error
        }
        self.audioEngine = audioEngine
    }

    /// Built outside the main actor: the tap runs on the audio thread.
    private nonisolated static func tapBlock(encoder: PCM16Encoder, pipe: StreamingAudioPipe) -> AVAudioNodeTapBlock {
        { buffer, _ in
            if let data = encoder.encode(buffer) {
                pipe.write(data)
            }
        }
    }

    private func stopAudioEngine() {
        audioEngine?.stop()
        audioEngine?.inputNode.removeTap(onBus: 0)
        audioEngine = nil
    }

    private func teardown() {
        resumeFinalize()
        connectTask?.cancel()
        connectTask = nil
        receiveTask?.cancel()
        receiveTask = nil
        pipe?.close()
        pipe = nil
        socket?.cancel(with: .normalClosure, reason: nil)
        socket = nil
        audioSession.deactivate()
    }
}

// MARK: - Audio plumbing (audio thread)

/// Converts mic buffers to mono 16-bit little-endian PCM at the vendor's sample rate.
nonisolated final class PCM16Encoder: @unchecked Sendable {
    private let converter: AVAudioConverter
    private let outputFormat: AVAudioFormat

    init?(inputFormat: AVAudioFormat, sampleRate: Double) {
        guard let outputFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: sampleRate,
            channels: 1,
            interleaved: true
        ), let converter = AVAudioConverter(from: inputFormat, to: outputFormat) else {
            return nil
        }
        self.converter = converter
        self.outputFormat = outputFormat
    }

    func encode(_ buffer: AVAudioPCMBuffer) -> Data? {
        let ratio = outputFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount((Double(buffer.frameLength) * ratio).rounded(.up)) + 32
        guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else { return nil }

        var consumed = false
        var error: NSError?
        converter.convert(to: output, error: &error) { _, status in
            if consumed {
                status.pointee = .noDataNow
                return nil
            }
            consumed = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, output.frameLength > 0, let samples = output.int16ChannelData else { return nil }
        return Data(bytes: samples[0], count: Int(output.frameLength) * MemoryLayout<Int16>.size)
    }
}

/// Holds audio until the websocket is open, then forwards it as it arrives.
nonisolated final class StreamingAudioPipe: @unchecked Sendable {
    private let lock = NSLock()
    private var pending: [Data] = []
    private var sink: ((Data) -> Void)?
    private var isClosed = false

    func write(_ data: Data) {
        lock.lock(); defer { lock.unlock() }
        guard !isClosed else { return }
        if let sink { sink(data) } else { pending.append(data) }
    }

    func attach(_ sink: @escaping (Data) -> Void) {
        lock.lock(); defer { lock.unlock() }
        guard !isClosed else { return }
        pending.forEach(sink)
        pending = []
        self.sink = sink
    }

    /// Stops forwarding; anything written afterwards is dropped.
    func close() {
        lock.lock(); defer { lock.unlock() }
        isClosed = true
        sink = nil
        pending = []
    }
}

// MARK: - Token

/// Fetches short-lived vendor tokens from the backend and reuses them until they near expiry.
@MainActor
final class StreamingTokenProvider {
    private static var providers: [String: StreamingTokenProvider] = [:]

    static func shared(for provider: String) -> StreamingTokenProvider {
        if let existing = providers[provider] { return existing }
        let created = StreamingTokenProvider(provider: provider)
        providers[provider] = created
        return created
    }

    private struct TokenResponse: Decodable {
        let access_token: String
        let expires_in: Int
    }

    private let provider: String
    private var cached: (token: String, expiry: Date)?
    private var inFlight: Task<String, Error>?

    private init(provider: String) {
        self.provider = provider
    }

    /// Warms the cache so the first press doesn't wait on the backend. Failures are ignored.
    func prefetch() {
        Task { _ = try? await token() }
    }

    func token() async throws -> String {
        if let cached, cached.expiry.timeIntervalSinceNow > 20 {
            return cached.token
        }
        if let inFlight {
            return try await inFlight.value
        }
        let task = Task { try await fetch() }
        inFlight = task
        defer { inFlight = nil }
        return try await task.value
    }

    private func fetch() async throws -> String {
        guard let accessToken = await AuthTokenProvider.shared.currentAccessToken() else {
            throw ServerError(statusCode: 401, message: "Not signed in")
        }
        let endpoint = URL(string: "/api/v1/agent/transcription/\(provider)/token", relativeTo: AppConfiguration.backendURL)!
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")

        let (data, response) = try await NetworkSession.shared.data(for: request)
        let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard 200..<300 ~= statusCode else {
            throw ServerError(statusCode: statusCode, message: String(data: data, encoding: .utf8) ?? "")
        }
        let decoded = try JSONDecoder().decode(TokenResponse.self, from: data)
        cached = (decoded.access_token, Date().addingTimeInterval(TimeInterval(decoded.expires_in)))
        return decoded.access_token
    }
}
