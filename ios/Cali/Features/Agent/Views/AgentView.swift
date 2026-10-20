//
//  AgentView.swift
//  Cali
//
//  Created by GPT-5 Codex on 11/8/25.
//

import SwiftUI
import UIKit

struct AgentView: View {
    @StateObject private var viewModel = AgentViewModel()
    @State private var isPressingMic = false
    @State private var isTypingPrompt = false
    @State private var typedPrompt = ""
    @State private var selectedEventForDetails: CalendarEvent?
    @State private var isLoadingEventDetails = false
    @Binding var scrollToNowTrigger: Int

    @EnvironmentObject private var authViewModel: AuthViewModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var isLoading = false
    @State private var didConfigureViewModel = false
    @State private var ratingNote = ""

    var body: some View {
        ZStack(alignment: .top) {
            ColorPalette.Gradients.backgroundBase
                .ignoresSafeArea()

            // Swipeable schedule with windowed event loading
            SwipeableScheduleView(
                referenceDate: viewModel.scheduleDate,
                numberOfDays: viewModel.numberOfDays,
                events: viewModel.displayEvents,
                loadingDays: viewModel.loadingDays,
                userTimezone: viewModel.userTimezone,
                modalBottomPadding: scheduleModalPadding,
                onVisibleDaysChanged: { date in
                    viewModel.onVisibleDaysChanged(firstVisibleDate: date)
                },
                scrollToNowTrigger: $scrollToNowTrigger,
                scrollTarget: Binding(
                    get: { viewModel.scrollTarget },
                    set: { viewModel.scrollTarget = $0 }
                ),
                selectedEvent: $selectedEventForDetails,
                focusEvent: viewModel.focusEvent,
                onBackgroundTap: { selectedEventForDetails = nil }
            )
            .padding(.horizontal, 4)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .ignoresSafeArea(edges: .bottom)
        }
        .overlay(alignment: .top) {
            if let details = detailsModalContent {
                EventDetailsModal(event: details.event, original: details.original)
                    .padding(.top, 8)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .onChange(of: selectedEventForDetails) { oldValue, newValue in
            // When an event is selected, fetch full event details to ensure we have attendees, location, etc.
            // The pending create/update preview isn't on the calendar yet, so there's nothing to fetch
            if let event = newValue, event.calendarId != nil, event.id != viewModel.pendingEventPreview?.event.id {
                Task {
                    await fetchFullEventDetails(event: event)
                }
            }
        }
        .onChange(of: viewModel.displayEvents) { _, newValue in
            if let selected = selectedEventForDetails,
               !newValue.contains(where: { $0.event.id == selected.id && !$0.isHidden }) {
                selectedEventForDetails = nil
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            VStack(spacing: 8) { // 4pt gap between modal and microphone container
                // Unified modal appears above microphone button, overlaying the schedule view
                // Priority: confirmation > thinking > notice
                // After a thumbs up/down, an optional note on what went wrong (or right)
                if showsRatingNote {
                    TypedPromptBubble(
                        text: $ratingNote,
                        isSending: viewModel.isSendingRatingNote,
                        placeholder: viewModel.lastAgentRating == .bad ? "What went wrong? (optional)" : "Any notes? (optional)",
                        focusOnAppear: false,
                        onSend: {
                            viewModel.sendRatingNote(ratingNote)
                        }
                    )
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                }

                if let modalState = agentModalState {
                    AgentModal(state: modalState)
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                }
                
                if isTypingPrompt {
                    TypedPromptBubble(
                        text: $typedPrompt,
                        isSending: isLoading,
                        // With a proposal on screen, the prompt changes it
                        placeholder: viewModel.agentAction?.requiresConfirmation == true ? "Follow up…" : "Ask Cali…",
                        onSend: sendTypedPrompt
                    )
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                }

                // Microphone button in liquid glass container - always in fixed position at bottom
                MicrophoneButtonContainer {
                    microphoneButton
                }
                .overlay(alignment: .trailing) {
                    typePromptButton
                        .padding(.trailing, 20)
                }
            }
        }
        // After confirming, ask for the email of each guest whose contact has none
        .sheet(item: Binding(
            get: { viewModel.guestNeedingEmail },
            set: { _ in }
        )) { guest in
            GuestEmailModal(
                name: guest.name,
                isSaving: viewModel.isSavingGuestEmail,
                onAdd: { email in
                    Task { await viewModel.addGuestEmail(email) }
                },
                onCancel: {
                    Task { await viewModel.skipGuestEmail() }
                }
            )
            .id(guest.id)
            .presentationDetents([.height(260)])
            .presentationDragIndicator(.hidden)
            .interactiveDismissDisabled()
        }
        .animation(.spring(response: 0.3, dampingFraction: 0.8), value: isTypingPrompt)
        .animation(.spring(response: 0.3, dampingFraction: 0.8), value: showsRatingNote)
        .onChange(of: viewModel.lastAgentRun) { _, _ in
            ratingNote = ""
        }
        .onChange(of: viewModel.lastAgentRating) { _, rating in
            // Deselecting a thumb discards the note in progress
            if rating == nil { ratingNote = "" }
        }
        .animation(.spring(response: 0.3, dampingFraction: 0.8), value: agentModalState != nil)
        .animation(.spring(response: 0.3, dampingFraction: 0.8), value: detailsModalContent != nil)
        .onReceive(viewModel.$displayState) { state in
            handleDisplayStateChange(state)
            
            // Clear EventDetailsModal when agent responses are handled (except no-action and error)
            if case .completed(let result) = state {
                switch result.agentResponse {
                case .noAction, .error:
                    // Don't clear modal for no-action or error responses
                    break
                case .showEvent, .showSchedule, .createEvent, .updateEvent, .deleteEvent:
                    // Clear modal for all action responses
                    selectedEventForDetails = nil
                }
            }
        }
        .task {
            if didConfigureViewModel == false {
                viewModel.configure(authProvider: authViewModel)
                didConfigureViewModel = true
                LiveActivityPushCoordinator.shared.start()
                Task { await ContactDirectory.shared.load() }
                // Load initial windowed events centered on today
                try? await viewModel.loadWindowedEvents(centerDate: Date())
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                viewModel.syncLiveActivities()
                viewModel.refreshScheduleOnForeground()
            }
        }
        .onAppear {
            // Reload events when view appears (if already configured)
            if didConfigureViewModel {
                Task {
                    try? await viewModel.loadWindowedEvents(centerDate: Date(), force: true)
                }
            }
        }
        .onDisappear {
            // Cleanup audio session when view disappears to free up resources
            viewModel.cleanupAudioSession()
        }
    }

    private var agentModalState: AgentModalState? {
        // Priority: error > confirmation > listening > thinking > notice
        // Error has highest priority - critical state that needs immediate attention
        if let errorState = viewModel.errorState {
            return .error(
                message: errorState.message,
                context: errorState.context
            )
        } else if let agentAction = viewModel.agentAction,
           agentAction.requiresConfirmation,
           viewModel.hasLoadedSchedule,
           viewModel.transcriptionText == nil,
           !viewModel.isRecording, !isLoading { // Hidden while a follow-up to it is being recorded or sent
            return .confirmation(
                actionType: actionType(for: agentAction),
                onConfirm: {
                    Task {
                        await viewModel.confirmPendingAction(accessToken: authViewModel.session?.accessToken)
                    }
                },
                onCancel: {
                    viewModel.cancelPendingAction()
                },
                isLoading: viewModel.isConfirmingAction,
                rating: viewModel.lastAgentRating,
                onRate: ratingHandler
            )
        } else if viewModel.isRecording {
            // Show live transcript while recording (on-device speech recognition)
            return .listening(text: viewModel.liveTranscript)
        } else if let transcriptionText = viewModel.transcriptionText, !transcriptionText.isEmpty {
            return .thinking(text: transcriptionText)
        } else if let noticeMessage = viewModel.noticeMessage, !noticeMessage.isEmpty {
            return .notice(message: noticeMessage, rating: viewModel.lastAgentRating, onRate: noticeRatingHandler)
        } else {
            return nil
        }
    }
    
    /// The event the details modal shows: the one the user tapped, otherwise the event a pending
    /// create/update would produce (with the current event, for an update, so the changes show)
    private var detailsModalContent: (event: CalendarEvent, original: CalendarEvent?)? {
        var preview: AgentViewModel.PendingEventPreview?
        if case .confirmation = agentModalState {
            preview = viewModel.pendingEventPreview
        }
        if let selected = selectedEventForDetails, selected.id != preview?.event.id {
            return (selected, nil)
        }
        return preview.map { ($0.event, $0.original) }
    }

    /// Selects or deselects a thumb on the last agent result (nil when that run can't be rated, or
    /// once its feedback note is sent, which takes the thumbs away)
    private var ratingHandler: ((AgentRating) -> Void)? {
        guard viewModel.lastAgentRun != nil, !viewModel.didSendRatingNote else { return nil }
        return { rating in viewModel.toggleLastAgentRating(rating) }
    }

    /// Thumbs on a notice only when it's the last run's no-action response
    private var noticeRatingHandler: ((AgentRating) -> Void)? {
        guard let runId = viewModel.ratableMessageRunId, runId == viewModel.lastAgentRun?.requestId else { return nil }
        return ratingHandler
    }

    /// The note bubble shows while a thumb is selected on a rateable bar, until its note is sent
    private var showsRatingNote: Bool {
        guard viewModel.lastAgentRating != nil, !viewModel.didSendRatingNote, !isTypingPrompt else { return false }
        switch agentModalState {
        case .confirmation, .notice(_, _, .some):
            return true
        default:
            return false
        }
    }

    private var scheduleModalPadding: CGFloat {
        // Base padding (always present) for microphone button container
        // Container height: 72pt button + 12pt top padding + 12pt bottom padding = 96pt
        let microphonePadding: CGFloat = 96 + 8 + 24 // container height (72+12+12) + gap + buffer
        
        // Additional padding when modal visible
        let modalPadding: CGFloat = agentModalState != nil ? 88 + 8 + 24 : 0 // modal height + gap + buffer

        // Additional padding when the typed prompt bubble is open
        let promptPadding: CGFloat = (isTypingPrompt || showsRatingNote) ? 52 + 8 : 0 // bubble height + gap

        return microphonePadding + modalPadding + promptPadding
    }

    /// Small button beside the mic that opens a text bubble for typing a prompt.
    private var typePromptButton: some View {
        Button {
            isTypingPrompt.toggle()
            if !isTypingPrompt {
                typedPrompt = ""
            }
        } label: {
            Image(systemName: isTypingPrompt ? "xmark" : "keyboard")
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(ColorPalette.Text.primary)
                .frame(width: 48, height: 48)
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .glassEffect(.regular.interactive(), in: .circle)
        .shadow(color: Color.black.opacity(0.1), radius: 8, x: 0, y: 3)
        .disabled(viewModel.isRecording)
        .accessibilityLabel(isTypingPrompt ? "Close text prompt" : "Type a prompt")
    }

    private func sendTypedPrompt() {
        viewModel.sendTypedPrompt(typedPrompt, accessToken: authViewModel.session?.accessToken)
        typedPrompt = ""
        isTypingPrompt = false
    }
    
    private var microphoneButton: some View {
        Button {
            // Intentionally empty; gesture handles interaction
        } label: {
            Capsule()
                .fill(ColorPalette.Gradients.primary)
                .frame(width: 196, height: 72)
                .shadow(
                    color: ColorPalette.Semantic.primary.opacity(0.35),
                    radius: 24,
                    x: 0,
                    y: 12
                )
                .overlay {
                    if isLoading {
                        ProgressView()
                            .progressViewStyle(.circular)
                            .tint(ColorPalette.Text.inverted)
                    } else {
                        Image(systemName: "mic.fill")
                            .font(.system(size: 32, weight: .semibold))
                            .foregroundStyle(ColorPalette.Text.inverted)
                    }
                }
        }
        .opacity(viewModel.isRecording ? 0.85 : 1.0)
        .buttonStyle(.plain)
        .disabled(isLoading)
        .onLongPressGesture(
            minimumDuration: 0,
            maximumDistance: 80,
            pressing: { pressing in
                guard !isLoading else { return }
                if pressing && isPressingMic == false {
                    isPressingMic = true
                    // Haptic feedback when mic starts listening
                    let generator = UIImpactFeedbackGenerator(style: .medium)
                    generator.prepare()
                    generator.impactOccurred()
                    viewModel.startRecording()
                } else if pressing == false && isPressingMic {
                    isPressingMic = false
                    // Haptic feedback when releasing the button
                    let generator = UIImpactFeedbackGenerator(style: .medium)
                    generator.prepare()
                    generator.impactOccurred()
                    viewModel.stopAndSendRecording(accessToken: authViewModel.session?.accessToken)
                }
            },
            perform: {}
        )
    }

    private func actionType(for agentAction: AgentViewModel.AgentAction) -> ConfirmationActionType {
        switch agentAction {
        case .createEvent:
            return .createEvent
        case .deleteEvent:
            return .deleteEvent
        case .updateEvent:
            return .updateEvent
        case .showEvent, .showSchedule:
            // These shouldn't reach here since requiresConfirmation is false
            return .createEvent
        }
    }

    private func handleDisplayStateChange(_ state: AgentViewModel.DisplayState) {
        switch state {
        case .recording:
            isLoading = false
        case .uploading:
            isLoading = true
        case .completed:
            isLoading = false
        case .failed(_):
            isLoading = false
        case .idle:
            isLoading = false
        }
    }
    
    private func fetchFullEventDetails(event: CalendarEvent) async {
        guard let calendarId = event.calendarId,
              let accessToken = authViewModel.session?.accessToken else {
            return
        }
        
        isLoadingEventDetails = true
        defer { isLoadingEventDetails = false }
        
        do {
            let calendarService = CalendarService()
            let fullEvent = try await calendarService.fetchEvent(
                accessToken: accessToken,
                calendarId: calendarId,
                eventId: event.id
            )
            
            await MainActor.run {
                selectedEventForDetails = fullEvent
            }
        } catch {
            // If fetch fails, keep showing the event we have (even if incomplete)
            print("Failed to fetch full event details: \(error)")
        }
    }
}

#Preview {
    AgentView(scrollToNowTrigger: .constant(0))
        .environmentObject(AuthViewModel())
}
