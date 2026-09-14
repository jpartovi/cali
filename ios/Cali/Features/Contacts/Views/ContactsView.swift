//
//  ContactsView.swift
//  Cali
//

import SwiftUI

struct ContactsView: View {
    @StateObject private var viewModel = ContactsViewModel()
    @StateObject private var coordinator = CalendarOAuthCoordinator()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                content
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 24)
            .padding(.vertical, 32)
        }
        .background(ColorPalette.Surface.background.ignoresSafeArea())
        .navigationTitle("Contacts")
        .toolbarTitleDisplayMode(.inline)
        .task {
            await viewModel.importIfNeeded(using: coordinator)
        }
        .alert("Contacts", isPresented: Binding(
            get: { viewModel.errorMessage != nil || viewModel.statusMessage != nil },
            set: { isPresented in
                if isPresented == false {
                    viewModel.clearFeedback()
                }
            }
        )) {
            Button("OK", role: .cancel) {
                viewModel.clearFeedback()
            }
        } message: {
            Text(viewModel.errorMessage ?? viewModel.statusMessage ?? "")
        }
    }

    @ViewBuilder
    private var content: some View {
        if viewModel.isLoading && viewModel.contacts.isEmpty {
            ProgressView()
                .frame(maxWidth: .infinity)
                .padding(.vertical, 40)
        } else {
            if viewModel.contacts.isEmpty == false {
                contactsList
            } else if viewModel.isSyncing == false {
                Text("No contacts yet. Sync to import from Apple and Google.")
                    .font(.subheadline)
                    .foregroundStyle(ColorPalette.Text.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            syncButton
                .padding(.top, 4)
        }
    }

    private var contactsList: some View {
        VStack(spacing: 12) {
            ForEach(viewModel.contacts) { contact in
                contactRow(contact)
            }
        }
    }

    private func contactRow(_ contact: ContactRecord) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(contact.displayName)
                .font(.headline)
                .foregroundStyle(ColorPalette.Text.primary)
                .lineLimit(1)

            if let subtitle = subtitle(for: contact) {
                Text(subtitle)
                    .font(.subheadline)
                    .foregroundStyle(ColorPalette.Text.secondary)
                    .lineLimit(2)
            }
        }
        .padding(.vertical, 18)
        .padding(.horizontal, 20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 20)
                .fill(ColorPalette.Surface.elevated.opacity(0.85))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 20)
                .stroke(ColorPalette.Surface.overlay.opacity(0.4), lineWidth: 1)
        )
    }

    private func subtitle(for contact: ContactRecord) -> String? {
        let email = contact.emails.first(where: { $0.isPrimary })?.email ?? contact.emails.first?.email
        let phone = contact.phones.first(where: { $0.isPrimary })?.phoneRaw ?? contact.phones.first?.phoneRaw
        let parts = [email, phone].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private var syncButton: some View {
        Button {
            Task {
                await viewModel.sync(using: coordinator)
            }
        } label: {
            HStack(spacing: 10) {
                if viewModel.isSyncing || viewModel.isLinking {
                    ProgressView()
                        .progressViewStyle(.circular)
                        .tint(ColorPalette.Text.secondary)
                        .scaleEffect(0.85, anchor: .center)
                } else {
                    Image(systemName: "arrow.triangle.2.circlepath")
                        .imageScale(.medium)
                        .font(.system(size: 16, weight: .semibold))
                }
                Text(viewModel.isLinking ? "Connecting Google…" : viewModel.isSyncing ? "Syncing…" : "Sync contacts")
                    .font(.callout.weight(.semibold))
            }
            .padding(.vertical, 10)
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity)
            .background(
                RoundedRectangle(cornerRadius: 16)
                    .fill(ColorPalette.Surface.elevated.opacity(0.9))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16)
                    .stroke(ColorPalette.Surface.overlay.opacity(0.5), lineWidth: 1)
            )
            .foregroundStyle(ColorPalette.Text.primary)
        }
        .buttonStyle(.plain)
        .disabled(viewModel.isSyncing || viewModel.isLinking)
        .accessibilityIdentifier("sync-contacts")
    }
}
