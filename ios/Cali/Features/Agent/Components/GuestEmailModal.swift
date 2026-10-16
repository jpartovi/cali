//
//  GuestEmailModal.swift
//  Cali
//

import SwiftUI

/// Asks for the email of a guest whose contact has none on file, after the user confirms the event.
/// "Add to contact" saves it (in Cali and on the Apple card) and invites them; "Cancel" leaves them off.
struct GuestEmailModal: View {
    let name: String
    let isSaving: Bool
    let onAdd: (String) -> Void
    let onCancel: () -> Void

    @State private var email = ""
    @FocusState private var isFocused: Bool

    private var trimmedEmail: String {
        email.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var canAdd: Bool {
        !isSaving && Self.isValidEmail(trimmedEmail)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text("What's \(name)'s email?")
                    .font(.system(size: 20, weight: .semibold))
                    .foregroundStyle(ColorPalette.Text.primary)
                Text("\(name) has no email in your contacts. Add one to invite them.")
                    .font(.system(size: 15))
                    .foregroundStyle(ColorPalette.Text.secondary)
            }

            TextField("Email", text: $email)
                .font(.system(size: 17))
                .foregroundStyle(ColorPalette.Text.primary)
                .keyboardType(.emailAddress)
                .textContentType(.emailAddress)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.done)
                .focused($isFocused)
                .onSubmit(add)
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .background(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(ColorPalette.Surface.overlay)
                )

            HStack(spacing: 12) {
                Button(action: onCancel) {
                    Text("Cancel")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(ColorPalette.Text.primary)
                        .frame(maxWidth: .infinity)
                        .frame(height: 50)
                        .background(
                            Capsule().stroke(ColorPalette.Surface.destructiveMuted, lineWidth: 2)
                        )
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .disabled(isSaving)

                Button(action: add) {
                    ZStack {
                        Capsule()
                            .fill(ColorPalette.Gradients.primary)
                        if isSaving {
                            ProgressView()
                                .progressViewStyle(.circular)
                                .tint(ColorPalette.Text.inverted)
                        } else {
                            Text("Add to contact")
                                .font(.system(size: 17, weight: .semibold))
                                .foregroundStyle(ColorPalette.Text.inverted)
                        }
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: 50)
                    .opacity(canAdd || isSaving ? 1.0 : 0.4)
                }
                .buttonStyle(.plain)
                .disabled(!canAdd)
            }
        }
        .padding(24)
        .onAppear { isFocused = true }
    }

    private func add() {
        guard canAdd else { return }
        onAdd(trimmedEmail.lowercased())
    }

    /// One "@" with something before it and a dotted domain after it, and no spaces
    static func isValidEmail(_ value: String) -> Bool {
        let parts = value.split(separator: "@", omittingEmptySubsequences: false)
        guard parts.count == 2, !parts[0].isEmpty, !value.contains(where: \.isWhitespace) else { return false }
        let domainLabels = parts[1].split(separator: ".", omittingEmptySubsequences: false)
        return domainLabels.count >= 2 && domainLabels.allSatisfy { !$0.isEmpty }
    }
}

#Preview {
    Color.clear
        .sheet(isPresented: .constant(true)) {
            GuestEmailModal(name: "Henry Ford", isSaving: false, onAdd: { _ in }, onCancel: {})
                .presentationDetents([.height(260)])
        }
}
