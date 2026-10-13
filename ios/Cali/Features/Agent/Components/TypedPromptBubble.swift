//
//  TypedPromptBubble.swift
//  Cali
//

import SwiftUI

/// Text bubble for typing a prompt to the agent instead of dictating it.
struct TypedPromptBubble: View {
    @Binding var text: String
    let isSending: Bool
    let onSend: () -> Void

    @FocusState private var isFocused: Bool

    private let cornerRadius: CGFloat = 22
    private let horizontalPadding: CGFloat = 24 // Match AgentModal horizontal inset
    private let sendButtonSize: CGFloat = 36

    private var canSend: Bool {
        !isSending && !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        HStack(alignment: .bottom, spacing: 10) {
            TextField("Ask Cali…", text: $text, axis: .vertical)
                .font(.system(size: 16, weight: .regular))
                .foregroundStyle(ColorPalette.Text.primary)
                .lineLimit(1...4)
                .focused($isFocused)
                .submitLabel(.send)
                .onSubmit(send)
                .padding(.vertical, 8)

            Button(action: send) {
                ZStack {
                    Circle()
                        .fill(ColorPalette.Gradients.primary)
                        .frame(width: sendButtonSize, height: sendButtonSize)

                    if isSending {
                        ProgressView()
                            .progressViewStyle(.circular)
                            .scaleEffect(0.7)
                            .tint(ColorPalette.Text.inverted)
                    } else {
                        Image(systemName: "arrow.up")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundStyle(ColorPalette.Text.inverted)
                    }
                }
                .opacity(canSend || isSending ? 1.0 : 0.4)
            }
            .buttonStyle(.plain)
            .disabled(!canSend)
            .accessibilityLabel("Send prompt")
        }
        .padding(.leading, 18)
        .padding(.trailing, 8)
        .padding(.vertical, 6)
        .glassEffect(.regular.interactive(), in: .rect(cornerRadius: cornerRadius))
        .shadow(color: Color.black.opacity(0.1), radius: 12, x: 0, y: 4)
        .padding(.horizontal, horizontalPadding)
        .onAppear { isFocused = true }
    }

    private func send() {
        guard canSend else { return }
        onSend()
    }
}

#Preview {
    ZStack {
        ColorPalette.Gradients.backgroundBase
            .ignoresSafeArea()

        TypedPromptBubble(text: .constant("Lunch with Sam tomorrow at noon"), isSending: false, onSend: {})
    }
}
