//
//  EventDetailsModal.swift
//  Cali
//
//  Created by Auto on 12/12/25.
//

import SwiftUI

struct EventDetailsModal: View {
    let event: CalendarEvent
    /// For a pending update, the event as it is now: changed details show the new value with the old one struck through
    var original: CalendarEvent? = nil
    @ObservedObject private var contactDirectory = ContactDirectory.shared
    
    private let cornerRadius: CGFloat = 22
    private let horizontalPadding: CGFloat = 24 // Match AgentModal horizontal padding
    private let verticalPadding: CGFloat = 20
    
    var body: some View {
        HStack(spacing: 0) {
            Spacer()
                .frame(width: horizontalPadding)
            
            ZStack {
                // Content layer
                VStack(alignment: .leading, spacing: 0) {
                    // Header with title
                    VStack(alignment: .leading, spacing: 2) {
                        Text(event.title ?? "Untitled Event")
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundColor(previousValue(event.title, original?.title) != nil ? changeColor : ColorPalette.Text.primary)
                            .lineLimit(2)
                            .multilineTextAlignment(.leading)
                            .frame(maxWidth: .infinity, alignment: .leading)

                        if let previousTitle = previousValue(event.title, original?.title) {
                            previousValueText(previousTitle, size: 14)
                        }
                    }
                    .padding(.horizontal, horizontalPadding)
                    .padding(.top, 16)
                    .padding(.bottom, 12)
                    
                    // Only show divider if there's content below
                    if hasAnyDetails() {
                        Divider()
                            .background(ColorPalette.Surface.overlay)
                            .padding(.horizontal, horizontalPadding)
                    }
                    
                    VStack(alignment: .leading, spacing: 12) {
                        // Date/Time section
                        let dateTimeInfo = formatDateTime(for: event)
                        if let dateTimeInfo {
                            detailRow(
                                icon: "calendar",
                                title: dateTimeInfo,
                                previous: previousValue(dateTimeInfo, original.flatMap(formatDateTime(for:)))
                            )
                        }
                        
                        // Location section
                        if let location = event.location, !location.isEmpty {
                            detailRow(
                                icon: "mappin.circle.fill",
                                title: location,
                                previous: previousValue(location, original?.location)
                            )
                        }
                        
                        // Description section
                        if let description = event.description, !description.isEmpty {
                            detailRow(
                                icon: "text.alignleft",
                                title: description,
                                isMultiline: true,
                                previous: previousValue(description, original?.description)
                            )
                        }
                        
                        // Attendees section (for an update, including the guests it removes)
                        if !event.attendees.isEmpty || !removedAttendees.isEmpty {
                            VStack(alignment: .leading, spacing: 6) {
                                HStack(spacing: 8) {
                                    Image(systemName: "person.2.fill")
                                        .font(.system(size: 14))
                                        .foregroundStyle(ColorPalette.Text.secondary)
                                        .frame(width: 18)
                                    
                                    Text("Attendees")
                                        .font(.system(size: 13, weight: .semibold))
                                        .foregroundColor(ColorPalette.Text.secondary)
                                }
                                
                                ForEach(Array(event.attendees.enumerated()), id: \.offset) { _, attendee in
                                    attendeeRow(attendee: attendee, change: isAdded(attendee) ? .added : nil)
                                        .padding(.leading, 26)
                                }

                                ForEach(Array(removedAttendees.enumerated()), id: \.offset) { _, attendee in
                                    attendeeRow(attendee: attendee, change: .removed)
                                        .padding(.leading, 26)
                                }
                            }
                        }
                        
                        // Conference/Meeting link section
                        if let conference = event.conference {
                            if let hangoutLink = conference.hangoutLink {
                                detailRow(
                                    icon: "video.fill",
                                    title: "Join Meeting",
                                    action: {
                                        UIApplication.shared.open(hangoutLink)
                                    }
                                )
                            } else if !conference.entryPoints.isEmpty {
                                if let firstEntryPoint = conference.entryPoints.first,
                                   let uri = firstEntryPoint.uri {
                                    detailRow(
                                        icon: "video.fill",
                                        title: firstEntryPoint.label ?? "Join Meeting",
                                        action: {
                                            UIApplication.shared.open(uri)
                                        }
                                    )
                                }
                            }
                        }
                    }
                    .padding(.horizontal, horizontalPadding)
                    .padding(.vertical, 12)
                }
            }
            .frame(maxWidth: .infinity)
            .glassEffect(.regular.interactive(), in: .rect(cornerRadius: cornerRadius))
            .shadow(
                color: Color.black.opacity(0.1),
                radius: 12,
                x: 0,
                y: 4
            )
            
            Spacer()
                .frame(width: horizontalPadding)
        }
        .transition(.opacity.combined(with: .move(edge: .top)))
    }
    
    private enum AttendeeChange {
        case added
        case removed
    }

    /// Color for details a pending update changes
    private var changeColor: Color { ColorPalette.Semantic.primary }

    /// For a pending update, the current value of a detail it changes (nil when unchanged or not an update)
    private func previousValue(_ new: String?, _ old: String?) -> String? {
        guard original != nil else { return nil }
        let new = new?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let old = old?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return new == old ? nil : (old.isEmpty ? "None" : old)
    }

    /// Ends a time that falls on the day after the start; shown as a superscript
    private static let nextDayMarker = "+1"

    /// The text, with a trailing next-day "+1" raised as a superscript
    private func detailText(_ text: String, size: CGFloat) -> Text {
        guard text.hasSuffix(Self.nextDayMarker) else { return Text(text) }
        let marker = Text(Self.nextDayMarker)
            .font(.system(size: size * 0.65, weight: .semibold))
            .baselineOffset(size * 0.4)
        return Text("\(String(text.dropLast(Self.nextDayMarker.count)))\(marker)")
    }

    private func previousValueText(_ text: String, size: CGFloat = 12) -> some View {
        detailText(text, size: size)
            .font(.system(size: size, weight: .regular))
            .strikethrough(true, color: ColorPalette.Text.secondary)
            .foregroundColor(ColorPalette.Text.secondary)
            .lineLimit(2)
            .multilineTextAlignment(.leading)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func attendeeKey(_ attendee: CalendarEvent.Attendee) -> String? {
        attendee.email?.lowercased()
    }

    private func isAdded(_ attendee: CalendarEvent.Attendee) -> Bool {
        guard let original, let key = attendeeKey(attendee) else { return false }
        return !original.attendees.contains { attendeeKey($0) == key }
    }

    /// Guests a pending update removes
    private var removedAttendees: [CalendarEvent.Attendee] {
        guard let original else { return [] }
        let remaining = Set(event.attendees.compactMap(attendeeKey))
        return original.attendees.filter { attendee in
            guard let key = attendeeKey(attendee) else { return false }
            return !remaining.contains(key)
        }
    }

    @ViewBuilder
    private func detailRow(
        icon: String,
        title: String,
        isMultiline: Bool = false,
        previous: String? = nil,
        action: (() -> Void)? = nil
    ) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: icon)
                .font(.system(size: 14))
                .foregroundStyle(previous != nil ? changeColor : ColorPalette.Text.secondary)
                .frame(width: 18)
            
            if let action = action {
                Button {
                    action()
                } label: {
                    Text(title)
                        .font(.system(size: 13, weight: .regular))
                        .foregroundColor(ColorPalette.Semantic.primary)
                        .lineLimit(isMultiline ? 3 : 2)
                        .multilineTextAlignment(.leading)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            } else {
                VStack(alignment: .leading, spacing: 2) {
                    detailText(title, size: 13)
                        .font(.system(size: 13, weight: previous != nil ? .semibold : .regular))
                        .foregroundColor(previous != nil ? changeColor : ColorPalette.Text.primary)
                        .lineLimit(isMultiline ? 3 : 2)
                        .multilineTextAlignment(.leading)
                        .frame(maxWidth: .infinity, alignment: .leading)

                    if let previous {
                        previousValueText(previous)
                    }
                }
            }
        }
    }
    
    @ViewBuilder
    private func attendeeRow(attendee: CalendarEvent.Attendee, change: AttendeeChange? = nil) -> some View {
        HStack(spacing: 6) {
            Text(contactDirectory.name(for: attendee.email) ?? attendee.email ?? attendee.displayName ?? "Unknown")
                .font(.system(size: 13, weight: change == .added ? .semibold : .regular))
                .strikethrough(change == .removed, color: ColorPalette.Text.secondary)
                .foregroundColor(
                    change == .added ? changeColor
                        : change == .removed ? ColorPalette.Text.secondary
                        : ColorPalette.Text.primary
                )
            
            switch change {
            case .added:
                Image(systemName: "plus.circle.fill")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(changeColor)
                    .frame(width: 16, height: 16)
            case .removed:
                Image(systemName: "minus.circle.fill")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(ColorPalette.Text.secondary)
                    .frame(width: 16, height: 16)
            case nil:
                if let responseStatus = attendee.responseStatus {
                    statusIcon(for: responseStatus)
                }
            }
        }
    }
    
    @ViewBuilder
    private func statusIcon(for status: String) -> some View {
        let (iconName, color) = statusIconInfo(for: status)
        Image(systemName: iconName)
            .font(.system(size: 12, weight: .medium))
            .foregroundColor(color)
            .frame(width: 16, height: 16)
    }
    
    private func statusIconInfo(for status: String) -> (iconName: String, color: Color) {
        switch status.lowercased() {
        case "accepted":
            return ("checkmark.circle.fill", ColorPalette.Semantic.success)
        case "declined":
            return ("xmark.circle.fill", ColorPalette.Semantic.destructive)
        case "tentative":
            return ("clock.fill", ColorPalette.Semantic.warning)
        case "needsaction":
            return ("clock", ColorPalette.Text.secondary)
        default:
            return ("circle", ColorPalette.Text.secondary)
        }
    }
    
    private func hasAnyDetails() -> Bool {
        formatDateTime(for: event) != nil ||
        (event.location != nil && !event.location!.isEmpty) ||
        (event.description != nil && !event.description!.isEmpty) ||
        !event.attendees.isEmpty ||
        !removedAttendees.isEmpty ||
        event.conference != nil
    }
    
    /// When an event happens, kept short: the year only on the start date and only when it isn't this year;
    /// an end time on the next day gets a flight-style superscript "+1", and later end days get their own date.
    /// e.g. "Oct 16, 9:00 PM – 11:15 PM", "Oct 16, 9:00 PM – 12:15 AM⁺¹", "Oct 16, 9:00 PM – Oct 18, 1:00 AM"
    private func formatDateTime(for event: CalendarEvent) -> String? {
        guard let start = event.start else { return nil }
        
        if event.isAllDay {
            // All-day dates are calendar days, so read and write them in one fixed time zone
            let utc = TimeZone(secondsFromGMT: 0)!
            var utcCalendar = Calendar(identifier: .gregorian)
            utcCalendar.timeZone = utc
            let isoFormatter = DateFormatter()
            isoFormatter.dateFormat = "yyyy-MM-dd"
            isoFormatter.timeZone = utc
            
            guard let startString = start.date,
                  let startDate = isoFormatter.date(from: startString) else {
                return nil
            }
            let startText = dayText(startDate, timeZone: utc, withYear: !isThisYear(utcCalendar.component(.year, from: startDate)))
            
            // The end date is exclusive, so the event's last day is the one before it
            guard let endString = event.end?.date,
                  let endDate = isoFormatter.date(from: endString),
                  let lastDay = utcCalendar.date(byAdding: .day, value: -1, to: endDate),
                  lastDay > startDate else {
                return startText
            }
            return "\(startText) – \(dayText(lastDay, timeZone: utc, withYear: false))"
        }
        
        guard let startDateTime = start.dateTime,
              let endDateTime = event.end?.dateTime else {
            return nil
        }
        
        let calendar = Calendar.autoupdatingCurrent
        let startText = "\(dayText(startDateTime, timeZone: calendar.timeZone, withYear: !isThisYear(calendar.component(.year, from: startDateTime)))), \(timeText(startDateTime))"
        let dayGap = calendar.dateComponents(
            [.day],
            from: calendar.startOfDay(for: startDateTime),
            to: calendar.startOfDay(for: endDateTime)
        ).day ?? 0
        
        switch dayGap {
        case ...0:
            return "\(startText) – \(timeText(endDateTime))"
        case 1:
            return "\(startText) – \(timeText(endDateTime))\(Self.nextDayMarker)"
        default:
            return "\(startText) – \(dayText(endDateTime, timeZone: calendar.timeZone, withYear: false)), \(timeText(endDateTime))"
        }
    }
    
    private func isThisYear(_ year: Int) -> Bool {
        year == Calendar.autoupdatingCurrent.component(.year, from: Date())
    }
    
    /// "Oct 16", or "Oct 16, 2027" with the year
    private func dayText(_ date: Date, timeZone: TimeZone, withYear: Bool) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale.autoupdatingCurrent
        formatter.timeZone = timeZone
        formatter.setLocalizedDateFormatFromTemplate(withYear ? "yMMMd" : "MMMd")
        return formatter.string(from: date)
    }
    
    /// "9:00 PM"
    private func timeText(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale.autoupdatingCurrent
        formatter.dateStyle = .none
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }
    
}

#Preview {
    ZStack {
        ColorPalette.Gradients.backgroundBase
            .ignoresSafeArea()
        
        VStack {
            Spacer()
            
            EventDetailsModal(
                event: CalendarEvent(
                    id: "1",
                    title: "Team Meeting",
                    description: "Weekly team sync to discuss progress and blockers",
                    start: CalendarEvent.EventDateTime(
                        dateTime: Date().addingTimeInterval(3600),
                        date: nil,
                        timeZone: TimeZone.autoupdatingCurrent.identifier
                    ),
                    end: CalendarEvent.EventDateTime(
                        dateTime: Date().addingTimeInterval(7200),
                        date: nil,
                        timeZone: TimeZone.autoupdatingCurrent.identifier
                    ),
                    attendees: [
                        CalendarEvent.Attendee(
                            email: "john@example.com",
                            displayName: "John Doe",
                            responseStatus: "accepted"
                        ),
                        CalendarEvent.Attendee(
                            email: "jane@example.com",
                            displayName: "Jane Smith",
                            responseStatus: "tentative"
                        )
                    ],
                    location: "Conference Room A",
                    conference: nil
                ),
                original: CalendarEvent(
                    id: "1",
                    title: "Team Sync",
                    description: "Weekly team sync to discuss progress and blockers",
                    start: CalendarEvent.EventDateTime(
                        dateTime: Date(),
                        date: nil,
                        timeZone: TimeZone.autoupdatingCurrent.identifier
                    ),
                    end: CalendarEvent.EventDateTime(
                        dateTime: Date().addingTimeInterval(3600),
                        date: nil,
                        timeZone: TimeZone.autoupdatingCurrent.identifier
                    ),
                    attendees: [
                        CalendarEvent.Attendee(
                            email: "john@example.com",
                            displayName: "John Doe",
                            responseStatus: "accepted"
                        ),
                        CalendarEvent.Attendee(
                            email: "sam@example.com",
                            displayName: "Sam Lee",
                            responseStatus: "accepted"
                        )
                    ],
                    location: "Conference Room A",
                    conference: nil
                )
            )
        }
        .padding()
    }
}
