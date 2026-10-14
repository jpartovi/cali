//
//  ContactDirectory.swift
//  Cali
//

import Combine
import Foundation

/// The user's contacts keyed by every email on them, so event guests can be shown by contact name.
@MainActor
final class ContactDirectory: ObservableObject {
    static let shared = ContactDirectory()

    @Published private(set) var namesByEmail: [String: String] = [:]

    private let contactsService: ContactsServicing
    private var isLoading = false

    init(contactsService: ContactsServicing? = nil) {
        self.contactsService = contactsService ?? ContactsService()
    }

    func load() async {
        guard isLoading == false else { return }
        isLoading = true
        defer { isLoading = false }

        guard let accessToken = await AuthTokenProvider.shared.currentAccessToken(),
              let contacts = try? await contactsService.listContacts(accessToken: accessToken) else {
            return
        }
        update(with: contacts)
    }

    func update(with contacts: [ContactRecord]) {
        var names: [String: String] = [:]
        for contact in contacts {
            let emails = contact.emails + [contact.inviteEmail].compactMap { $0 }
            for email in emails {
                let key = Self.normalize(email)
                if !key.isEmpty, names[key] == nil {
                    names[key] = contact.displayName
                }
            }
        }
        namesByEmail = names
    }

    func name(for email: String?) -> String? {
        guard let email else { return nil }
        return namesByEmail[Self.normalize(email)]
    }

    private static func normalize(_ email: String) -> String {
        email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }
}
