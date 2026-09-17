//
//  AppleContactsReader.swift
//  Cali
//

import Contacts
import Foundation

struct AppleContactPayload: Codable, Sendable {
    let appleIdentifier: String
    let givenName: String?
    let familyName: String?
    let nickname: String?
    let emails: [String]
}

enum AppleContactsReaderError: Error, Sendable {
    case accessDenied
    case fetchFailed(String)
}

enum AppleContactsReader {
    nonisolated static func authorizationStatus() -> CNAuthorizationStatus {
        CNContactStore.authorizationStatus(for: .contacts)
    }

    nonisolated static func hasAccess() -> Bool {
        let status = authorizationStatus()
        if status == .authorized {
            return true
        }
        if #available(iOS 18.0, *), status == .limited {
            return true
        }
        return false
    }

    static func requestAccess() async throws -> Bool {
        try await CNContactStore().requestAccess(for: .contacts)
    }

    nonisolated static func loadContacts() async throws -> [AppleContactPayload] {
        try await Task.detached(priority: .userInitiated) {
            try fetchContacts()
        }.value
    }

    nonisolated private static func fetchContacts() throws -> [AppleContactPayload] {
        guard hasAccess() else {
            throw AppleContactsReaderError.accessDenied
        }

        let store = CNContactStore()
        let keys: [CNKeyDescriptor] = [
            CNContactIdentifierKey as CNKeyDescriptor,
            CNContactGivenNameKey as CNKeyDescriptor,
            CNContactFamilyNameKey as CNKeyDescriptor,
            CNContactNicknameKey as CNKeyDescriptor,
            CNContactEmailAddressesKey as CNKeyDescriptor,
        ]
        let request = CNContactFetchRequest(keysToFetch: keys)
        var payloads: [AppleContactPayload] = []

        do {
            try store.enumerateContacts(with: request) { contact, _ in
                var emails: [String] = []
                var seen: Set<String> = []
                for value in contact.emailAddresses {
                    let email = (value.value as String)
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                        .lowercased()
                    guard email.isEmpty == false, seen.contains(email) == false else { continue }
                    seen.insert(email)
                    emails.append(email)
                }
                let given = nonempty(contact.givenName)
                let family = nonempty(contact.familyName)
                let nickname = nonempty(contact.nickname)
                guard given != nil || family != nil || nickname != nil || emails.isEmpty == false else {
                    return
                }
                payloads.append(
                    AppleContactPayload(
                        appleIdentifier: contact.identifier,
                        givenName: given,
                        familyName: family,
                        nickname: nickname,
                        emails: emails
                    )
                )
            }
        } catch {
            throw AppleContactsReaderError.fetchFailed(error.localizedDescription)
        }

        return payloads
    }

    nonisolated private static func nonempty(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
