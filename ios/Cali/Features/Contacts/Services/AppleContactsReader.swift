//
//  AppleContactsReader.swift
//  Cali
//

import Contacts
import Foundation

struct AppleImportedEmail: Codable, Sendable {
    let email: String
    let label: String?
    let isPrimary: Bool
}

struct AppleImportedPhone: Codable, Sendable {
    let phone: String
    let label: String?
    let isPrimary: Bool
}

struct AppleContactPayload: Codable, Sendable {
    let appleIdentifier: String
    let givenName: String?
    let familyName: String?
    let nickname: String?
    let emails: [AppleImportedEmail]
    let phones: [AppleImportedPhone]
}

enum AppleContactsReaderError: Error {
    case accessDenied
    case fetchFailed(Error)
}

enum AppleContactsReader {
    static func authorizationStatus() -> CNAuthorizationStatus {
        CNContactStore.authorizationStatus(for: .contacts)
    }

    static func hasAccess() -> Bool {
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

    static func loadContacts() throws -> [AppleContactPayload] {
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
            CNContactPhoneNumbersKey as CNKeyDescriptor,
        ]
        let request = CNContactFetchRequest(keysToFetch: keys)
        var payloads: [AppleContactPayload] = []

        do {
            try store.enumerateContacts(with: request) { contact, _ in
                let emails = contact.emailAddresses.enumerated().compactMap { index, value -> AppleImportedEmail? in
                    let email = (value.value as String).trimmingCharacters(in: .whitespacesAndNewlines)
                    guard email.isEmpty == false else { return nil }
                    return AppleImportedEmail(
                        email: email,
                        label: localizedLabel(value.label),
                        isPrimary: index == 0
                    )
                }
                let phones = contact.phoneNumbers.enumerated().compactMap { index, value -> AppleImportedPhone? in
                    let phone = value.value.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard phone.isEmpty == false else { return nil }
                    return AppleImportedPhone(
                        phone: phone,
                        label: localizedLabel(value.label),
                        isPrimary: index == 0
                    )
                }
                let given = nonempty(contact.givenName)
                let family = nonempty(contact.familyName)
                let nickname = nonempty(contact.nickname)
                guard given != nil || family != nil || nickname != nil || emails.isEmpty == false || phones.isEmpty == false else {
                    return
                }
                payloads.append(
                    AppleContactPayload(
                        appleIdentifier: contact.identifier,
                        givenName: given,
                        familyName: family,
                        nickname: nickname,
                        emails: emails,
                        phones: phones
                    )
                )
            }
        } catch {
            throw AppleContactsReaderError.fetchFailed(error)
        }

        return payloads
    }

    private static func nonempty(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private static func localizedLabel(_ label: String?) -> String? {
        guard let label, label.isEmpty == false else { return nil }
        let localized = CNLabeledValue<NSString>.localizedString(forLabel: label)
        return localized.isEmpty ? label : localized
    }
}
