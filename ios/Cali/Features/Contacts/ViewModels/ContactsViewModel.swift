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
    @Published private(set) var isLinking: Bool = false
    @Published private(set) var errorMessage: String?
    @Published private(set) var statusMessage: String?

    private let contactsService: ContactsServicing
    private let calendarService: CalendarServicing
    private let callbackScheme: String
    private static let didImportContactsKey = "cali.didImportContacts"

    init(
        contactsService: ContactsServicing? = nil,
        calendarService: CalendarServicing? = nil,
        callbackScheme: String? = nil
    ) {
        self.contactsService = contactsService ?? ContactsService()
        self.calendarService = calendarService ?? CalendarService()
        self.callbackScheme = callbackScheme ?? AppConfiguration.googleOAuthCallbackScheme
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

    func importIfNeeded(using coordinator: CalendarOAuthCoordinating) async {
        if UserDefaults.standard.bool(forKey: Self.didImportContactsKey) {
            await loadContacts()
            return
        }
        await sync(using: coordinator)
    }

    func sync(using coordinator: CalendarOAuthCoordinating) async {
        guard isSyncing == false, isLinking == false else { return }
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
            do {
                let googleResult = try await contactsService.importGoogleContacts(accessToken: accessToken)
                statusMessage = Self.summary(apple: appleResult, google: googleResult)
            } catch ContactsServiceError.needsReauth {
                await loadContacts()
                await linkGoogle(using: coordinator)
                return
            } catch {
                statusMessage = "Imported \(appleResult.imported + appleResult.merged) contacts. Google emails can be added after you re-link Google."
            }
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

    private func linkGoogle(using coordinator: CalendarOAuthCoordinating) async {
        guard isLinking == false else { return }
        isLinking = true
        defer { isLinking = false }

        guard let accessToken = await AuthTokenProvider.shared.currentAccessToken() else {
            errorMessage = "You're signed out. Please sign in again."
            return
        }

        do {
            let start = try await calendarService.beginGoogleOAuth(accessToken: accessToken)
            let callbackURL = try await coordinator.startAuthorization(with: start, callbackScheme: callbackScheme)
            let outcome = try evaluateCallbackURL(callbackURL, expectedState: start.state)
            switch outcome {
            case .success:
                if let retryToken = await AuthTokenProvider.shared.currentAccessToken() {
                    do {
                        let googleResult = try await contactsService.importGoogleContacts(accessToken: retryToken)
                        let googleEmails = googleResult.accounts.reduce(0) { $0 + $1.emailsAdded }
                        statusMessage = "Google added \(googleEmails) emails to your contacts."
                    } catch {
                        errorMessage = "Imported Apple contacts. Re-link Google from Calendar Accounts to add emails."
                    }
                }
                await loadContacts()
            case .failure(let message):
                errorMessage = message
            }
        } catch CalendarLinkError.userCancelled {
            errorMessage = "Google sign-in was cancelled."
        } catch {
            errorMessage = "Imported Apple contacts. Re-link Google from Calendar Accounts to add emails."
        }
    }

    func clearFeedback() {
        errorMessage = nil
        statusMessage = nil
    }

    private static func summary(apple: AppleContactsImportResponse, google: GoogleContactsImportResponse) -> String {
        let googleEmails = google.accounts.reduce(0) { $0 + $1.emailsAdded }
        return "Imported \(apple.imported + apple.merged) contacts. Google added \(googleEmails) emails."
    }

    private enum OAuthCallbackOutcome {
        case success
        case failure(message: String)
    }

    private func evaluateCallbackURL(_ url: URL, expectedState: String) throws -> OAuthCallbackOutcome {
        guard url.scheme?.caseInsensitiveCompare(callbackScheme) == .orderedSame else {
            throw CalendarLinkError.missingResult
        }
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            throw CalendarLinkError.missingResult
        }
        let queryItems = components.queryItems ?? []
        var payload: [String: String] = [:]
        for item in queryItems {
            if let value = item.value {
                payload[item.name] = value
            }
        }
        guard let returnedState = payload["state"], returnedState == expectedState else {
            throw CalendarLinkError.stateMismatch
        }
        guard let result = payload["result"] else {
            throw CalendarLinkError.missingResult
        }
        if result.lowercased() == "success" {
            return .success
        }
        if result.lowercased() == "error" {
            return .failure(message: payload["message"] ?? "Google reported an error.")
        }
        throw CalendarLinkError.missingResult
    }
}
