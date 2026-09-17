//
//  ContactsViewModel.swift
//  Cali
//

import Combine
import Foundation

@MainActor
final class ContactsViewModel: ObservableObject {
    @Published private(set) var contacts: [ContactRecord] = []
    @Published private(set) var isLoading: Bool = false
    @Published private(set) var isSyncing: Bool = false
    @Published private(set) var errorMessage: String?
    @Published private(set) var statusMessage: String?

    private let contactsService: ContactsServicing
    private static let didImportContactsKey = "cali.didImportContacts"

    init(contactsService: ContactsServicing? = nil) {
        self.contactsService = contactsService ?? ContactsService()
    }

    func loadContacts() async {
        guard isLoading == false else { return }
        isLoading = true
        defer { isLoading = false }

        guard let accessToken = await AuthTokenProvider.shared.currentAccessToken() else {
            errorMessage = "You're signed out. Please sign in again."
            return
        }

        do {
            contacts = try await contactsService.listContacts(accessToken: accessToken)
            errorMessage = nil
        } catch ContactsServiceError.unauthorized {
            errorMessage = "We couldn't access your account. Please sign in again."
        } catch {
            errorMessage = "We couldn't load your contacts. Please try again."
        }
    }

    func importIfNeeded() async {
        if UserDefaults.standard.bool(forKey: Self.didImportContactsKey) {
            await loadContacts()
            return
        }
        await sync()
    }

    func sync() async {
        guard isSyncing == false else { return }
        isSyncing = true
        errorMessage = nil
        statusMessage = nil
        defer { isSyncing = false }

        guard let accessToken = await AuthTokenProvider.shared.currentAccessToken() else {
            errorMessage = "You're signed out. Please sign in again."
            return
        }

        if AppleContactsReader.hasAccess() == false {
            do {
                let granted = try await AppleContactsReader.requestAccess()
                if granted == false {
                    errorMessage = "Contacts access was denied. You can enable it in Settings."
                    return
                }
            } catch {
                errorMessage = "Cali needs access to your contacts to import them."
                return
            }
        }

        let appleContacts: [AppleContactPayload]
        do {
            appleContacts = try await AppleContactsReader.loadContacts()
        } catch {
            errorMessage = "We couldn't read your Apple contacts."
            return
        }

        do {
            let appleResult = try await contactsService.importAppleContacts(
                accessToken: accessToken,
                contacts: appleContacts
            )
            UserDefaults.standard.set(true, forKey: Self.didImportContactsKey)
            statusMessage = "Imported \(appleResult.imported + appleResult.updated) contacts from Apple."
            await loadContacts()
        } catch ContactsServiceError.unauthorized {
            errorMessage = "We couldn't access your account. Please sign in again."
        } catch ContactsServiceError.network(_) {
            errorMessage = "Import is taking longer than expected. Loading any contacts that already saved."
            await loadContacts()
        } catch {
            errorMessage = "We couldn't import your contacts. Please try again."
            await loadContacts()
        }
    }

    func clearFeedback() {
        errorMessage = nil
        statusMessage = nil
    }
}
