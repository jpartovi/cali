//
//  CalendarAccountsViewModel.swift
//  Cali
//
//  Created by GPT-5 Codex on 11/9/25.
//

import Combine
import Foundation

@MainActor
final class CalendarAccountsViewModel: ObservableObject {
    @Published private(set) var accounts: [GoogleAccount] = []
    @Published private(set) var isLoading: Bool = false
    @Published private(set) var errorMessage: String?
    @Published private(set) var isLinking: Bool = false
    @Published private(set) var linkingMessage: String?
    @Published private(set) var linkingError: String?
    @Published private(set) var deletingAccountIDs: Set<String> = []
    @Published private(set) var deletionError: String?
    @Published private(set) var togglingCalendarIDs: Set<String> = []
    @Published private(set) var toggleError: String?
    @Published private(set) var isSyncingContacts: Bool = false
    @Published private(set) var contactsMessage: String?
    @Published private(set) var contactsError: String?
    @Published var expandedAccountIDs: Set<String> = []

    private let calendarService: CalendarServicing
    private let contactsService: ContactsServicing
    private static let didImportContactsKey = "cali.didImportContacts"
    private let callbackScheme: String
    private var hasLoaded: Bool = false
    private let sessionProvider: AuthSessionProviding?
    private let storedSession: OTPSession?

    init(session: OTPSession, calendarService: CalendarServicing? = nil, contactsService: ContactsServicing? = nil, callbackScheme: String? = nil) {
        self.calendarService = calendarService ?? CalendarService()
        self.contactsService = contactsService ?? ContactsService()
        self.callbackScheme = callbackScheme ?? AppConfiguration.googleOAuthCallbackScheme
        self.sessionProvider = nil
        self.storedSession = session
    }

    init(sessionProvider: AuthSessionProviding, calendarService: CalendarServicing? = nil, contactsService: ContactsServicing? = nil, callbackScheme: String? = nil) {
        self.calendarService = calendarService ?? CalendarService()
        self.contactsService = contactsService ?? ContactsService()
        self.callbackScheme = callbackScheme ?? AppConfiguration.googleOAuthCallbackScheme
        self.sessionProvider = sessionProvider
        self.storedSession = sessionProvider.session
    }

    func loadCalendars(force: Bool = false) async {
        guard !isLoading else { return }
        if hasLoaded && !force {
            return
        }

        isLoading = true
        defer { isLoading = false }

        guard let accessToken = await currentAccessToken() else {
            errorMessage = "We couldn't load your calendars because your session is unavailable."
            return
        }

        do {
            // Refresh calendars from Google API on first load or when forced
            // This ensures calendar metadata is up-to-date in Supabase
            if !hasLoaded || force {
                try await calendarService.refreshCalendars(accessToken: accessToken)
            }
            
            // Fetch calendars from Supabase (now up-to-date after refresh)
            let fetched = try await calendarService.fetchCalendars(accessToken: accessToken)
            accounts = fetched
            errorMessage = nil
            hasLoaded = true
        } catch let serviceError as CalendarServiceError {
            switch serviceError {
            case .unauthorized:
                // Supabase should auto-refresh, but if we still get 401, session is invalid
                errorMessage = "We couldn't access your calendars. Please sign in again."
            default:
                errorMessage = "We couldn't load your calendars. Please try again later."
            }
        } catch {
            errorMessage = "We couldn't load your calendars. Please try again later."
        }
    }

    func linkCalendar(using coordinator: CalendarOAuthCoordinating) async {
        guard isLinking == false else { return }

        isLinking = true
        linkingError = nil
        linkingMessage = nil

        guard let accessToken = await currentAccessToken() else {
            linkingError = "You're signed out. Please sign in again."
            isLinking = false
            return
        }

        do {
            let start = try await calendarService.beginGoogleOAuth(accessToken: accessToken)
            let callbackURL = try await coordinator.startAuthorization(with: start, callbackScheme: callbackScheme)
            let outcome = try evaluateCallbackURL(callbackURL, expectedState: start.state)

            switch outcome {
            case .success(let message):
                linkingMessage = message ?? "Google Calendar linked."
                await loadCalendars(force: true)
                await enrichFromGoogle()
            case .failure(let message):
                linkingError = message
            }
        } catch let serviceError as CalendarServiceError {
            switch serviceError {
            case .unauthorized:
                // Supabase should auto-refresh, but if we still get 401, session is invalid
                linkingError = "We couldn't access your account. Please sign in again."
            default:
                linkingError = "We couldn't start the Google sign-in flow. Please try again."
            }
        } catch let linkError as CalendarLinkError {
            switch linkError {
            case .userCancelled:
                linkingError = "Google sign-in was cancelled."
            case .stateMismatch:
                linkingError = "Security validation failed. Please try again."
            case .resultError(let message):
                linkingError = message ?? "Google reported an error while linking your calendar."
            case .missingResult, .missingCallbackURL:
                linkingError = "We couldn't read Google's response. Please try again."
            case .failedToStart:
                linkingError = "Unable to open the Google sign-in flow."
            case .underlying(let error):
                if (error as NSError).code == NSUserCancelledError {
                    linkingError = "Google sign-in was cancelled."
                } else {
                    linkingError = "Something went wrong while linking your calendar."
                }
            }
        } catch {
            linkingError = "Something went wrong while linking your calendar."
        }

        isLinking = false
    }

    func clearLinkingFeedback() {
        linkingError = nil
        linkingMessage = nil
    }

    func deleteAccount(_ account: GoogleAccount) async {
        guard deletingAccountIDs.contains(account.id) == false else { return }

        deletingAccountIDs.insert(account.id)
        defer { deletingAccountIDs.remove(account.id) }

        guard let accessToken = await currentAccessToken() else {
            deletionError = "You're signed out. Please sign in again."
            return
        }

        do {
            try await calendarService.deleteCalendar(accessToken: accessToken, accountId: account.id)
            accounts.removeAll { $0.id == account.id }
            deletionError = nil
        } catch let serviceError as CalendarServiceError {
            switch serviceError {
            case .unauthorized:
                // Supabase should auto-refresh, but if we still get 401, session is invalid
                deletionError = "We couldn't access your account. Please sign in again."
            default:
                deletionError = "We couldn't remove that account. Please try again."
            }
        } catch {
            deletionError = "We couldn't remove that account. Please try again."
        }
    }

    func isDeleting(_ account: GoogleAccount) -> Bool {
        deletingAccountIDs.contains(account.id)
    }

    func clearDeletionError() {
        deletionError = nil
    }

    func toggleExpansion(for accountID: String) {
        if expandedAccountIDs.contains(accountID) {
            expandedAccountIDs.remove(accountID)
        } else {
            expandedAccountIDs.insert(accountID)
        }
    }

    func isExpanded(_ account: GoogleAccount) -> Bool {
        // Always return true since collapse functionality has been removed
        true
    }

    func toggleCalendarVisibility(_ calendar: GoogleCalendar) async {
        guard togglingCalendarIDs.contains(calendar.id) == false else { return }

        togglingCalendarIDs.insert(calendar.id)
        defer { togglingCalendarIDs.remove(calendar.id) }

        guard let accessToken = await currentAccessToken() else {
            toggleError = "You're signed out. Please sign in again."
            return
        }

        do {
            let newIsHidden = !calendar.isHidden
            let updatedCalendar = try await calendarService.updateCalendar(
                accessToken: accessToken,
                calendarId: calendar.id,
                isHidden: newIsHidden
            )
            
            // Update the calendar in the local accounts array
            accounts = accounts.map { account in
                guard account.id == calendar.googleAccountId,
                      var calendars = account.calendars,
                      let calendarIndex = calendars.firstIndex(where: { $0.id == calendar.id }) else {
                    return account
                }
                calendars[calendarIndex] = updatedCalendar
                return GoogleAccount(
                    id: account.id,
                    userId: account.userId,
                    googleUserId: account.googleUserId,
                    email: account.email,
                    displayName: account.displayName,
                    avatarURL: account.avatarURL,
                    createdAt: account.createdAt,
                    updatedAt: account.updatedAt,
                    calendars: calendars
                )
            }
            
            toggleError = nil
        } catch let serviceError as CalendarServiceError {
            switch serviceError {
            case .unauthorized:
                toggleError = "We couldn't access your account. Please sign in again."
            default:
                toggleError = "We couldn't update the calendar visibility. Please try again."
            }
        } catch {
            toggleError = "We couldn't update the calendar visibility. Please try again."
        }
    }

    func isToggling(_ calendar: GoogleCalendar) -> Bool {
        togglingCalendarIDs.contains(calendar.id)
    }

    func clearToggleError() {
        toggleError = nil
    }

    func clearContactsFeedback() {
        contactsError = nil
        contactsMessage = nil
    }

    func importContactsIfNeeded(using coordinator: CalendarOAuthCoordinating?) async {
        if UserDefaults.standard.bool(forKey: Self.didImportContactsKey) {
            return
        }
        await syncContacts(using: coordinator, requestPermission: true, allowReauth: coordinator != nil)
    }

    func syncContacts(
        using coordinator: CalendarOAuthCoordinating?,
        requestPermission: Bool,
        allowReauth: Bool
    ) async {
        guard isSyncingContacts == false else { return }
        isSyncingContacts = true
        contactsError = nil
        contactsMessage = nil
        defer { isSyncingContacts = false }

        guard let accessToken = await currentAccessToken() else {
            contactsError = "You're signed out. Please sign in again."
            return
        }

        if AppleContactsReader.hasAccess() == false {
            if requestPermission == false {
                contactsError = "Cali needs access to your contacts to import them."
                return
            }
            do {
                let granted = try await AppleContactsReader.requestAccess()
                if granted == false {
                    contactsError = "Contacts access was denied. You can enable it in Settings."
                    return
                }
            } catch {
                contactsError = "Cali needs access to your contacts to import them."
                return
            }
        }

        let appleContacts: [AppleContactPayload]
        do {
            appleContacts = try AppleContactsReader.loadContacts()
        } catch {
            contactsError = "We couldn't read your Apple contacts."
            return
        }

        do {
            let appleResult = try await contactsService.importAppleContacts(
                accessToken: accessToken,
                contacts: appleContacts
            )
            let googleResult = try await contactsService.importGoogleContacts(accessToken: accessToken)
            UserDefaults.standard.set(true, forKey: Self.didImportContactsKey)
            contactsMessage = Self.summary(apple: appleResult, google: googleResult)
        } catch ContactsServiceError.needsReauth {
            UserDefaults.standard.set(true, forKey: Self.didImportContactsKey)
            if allowReauth, let coordinator {
                await linkCalendar(using: coordinator)
                return
            }
            contactsError = "Imported Apple contacts. Re-link Google to add emails from Google Contacts."
        } catch ContactsServiceError.unauthorized {
            contactsError = "We couldn't access your account. Please sign in again."
        } catch {
            contactsError = "We couldn't import your contacts. Please try again."
        }
    }

    func enrichFromGoogle() async {
        guard let accessToken = await currentAccessToken() else { return }
        do {
            let googleResult = try await contactsService.importGoogleContacts(accessToken: accessToken)
            let googleEmails = googleResult.accounts.reduce(0) { $0 + $1.emailsAdded }
            if googleEmails > 0 {
                contactsMessage = "Google added \(googleEmails) emails to your contacts."
            }
        } catch ContactsServiceError.needsReauth {
            contactsError = "Re-link Google to allow Cali to read Contacts and fill in emails."
        } catch {
            // Calendar linking succeeded; contact enrichment can be retried from Sync contacts.
        }
    }

    private static func summary(apple: AppleContactsImportResponse, google: GoogleContactsImportResponse) -> String {
        let googleEmails = google.accounts.reduce(0) { $0 + $1.emailsAdded }
        return "Imported \(apple.imported + apple.merged) contacts. Google added \(googleEmails) emails."
    }

    private func currentAccessToken() async -> String? {
        // Always use AuthTokenProvider - single source of truth, always fresh
        return await AuthTokenProvider.shared.currentAccessToken()
    }

    private enum OAuthCallbackOutcome {
        case success(message: String?)
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
            return .success(message: payload["message"])
        }

        if result.lowercased() == "error" {
            throw CalendarLinkError.resultError(message: payload["message"])
        }

        throw CalendarLinkError.missingResult
    }
}

