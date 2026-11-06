//
//  ContactDirectory.swift
//  Cali
//

import Combine
import Foundation

/// The user's contacts keyed by every email on them, so event guests can be shown by contact name.
/// The user's calendars are keyed by their Google calendar ID too, since a shared calendar can be a guest.
@MainActor
final class ContactDirectory: ObservableObject {
    static let shared = ContactDirectory()

    @Published private(set) var namesByEmail: [String: String] = [:]
    @Published private(set) var calendarNamesByID: [String: String] = [:]

    private let contactsService: ContactsServicing
    private let calendarService: CalendarServicing
    private var isLoading = false

    init(contactsService: ContactsServicing? = nil, calendarService: CalendarServicing? = nil) {
        self.contactsService = contactsService ?? ContactsService()
        self.calendarService = calendarService ?? CalendarService()
    }

    func load() async {
        guard isLoading == false else { return }
        isLoading = true
        defer { isLoading = false }

        guard let accessToken = await AuthTokenProvider.shared.currentAccessToken() else { return }
        async let contacts = try? contactsService.listContacts(accessToken: accessToken)
        async let accounts = try? calendarService.fetchCalendars(accessToken: accessToken)
        if let contacts = await contacts {
            update(with: contacts)
        }
        if let accounts = await accounts {
            update(with: accounts)
        }
    }

    func update(with accounts: [GoogleAccount]) {
        var names: [String: String] = [:]
        for calendar in accounts.flatMap({ $0.calendars ?? [] }) {
            names[Self.normalize(calendar.googleCalendarId)] = calendar.name
        }
        calendarNamesByID = names
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
        let key = Self.normalize(email)
        return namesByEmail[key] ?? calendarNamesByID[key]
    }

    private static func normalize(_ email: String) -> String {
        email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }
}
