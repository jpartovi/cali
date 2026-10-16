//
//  AppleContactsWriter.swift
//  Cali
//

import Contacts
import Foundation

enum AppleContactsWriterError: Error, Sendable {
    case accessDenied
    case contactNotFound
    case saveFailed(String)
}

enum AppleContactsWriter {
    /// Adds an email to the Apple contact card (no-op when the card already has it)
    nonisolated static func addEmail(_ email: String, toContactWithIdentifier identifier: String) async throws {
        try await Task.detached(priority: .userInitiated) {
            try saveEmail(email, identifier: identifier)
        }.value
    }

    nonisolated private static func saveEmail(_ email: String, identifier: String) throws {
        guard AppleContactsReader.hasAccess() else {
            throw AppleContactsWriterError.accessDenied
        }

        let store = CNContactStore()
        let contact: CNContact
        do {
            contact = try store.unifiedContact(
                withIdentifier: identifier,
                keysToFetch: [CNContactEmailAddressesKey as CNKeyDescriptor]
            )
        } catch {
            throw AppleContactsWriterError.contactNotFound
        }

        let normalized = email.lowercased()
        guard !contact.emailAddresses.contains(where: { ($0.value as String).lowercased() == normalized }) else {
            return
        }

        guard let mutable = contact.mutableCopy() as? CNMutableContact else {
            throw AppleContactsWriterError.saveFailed("Contact can't be edited")
        }
        mutable.emailAddresses.append(CNLabeledValue(label: CNLabelOther, value: email as NSString))

        let request = CNSaveRequest()
        request.update(mutable)
        do {
            try store.execute(request)
        } catch {
            throw AppleContactsWriterError.saveFailed(error.localizedDescription)
        }
    }
}
