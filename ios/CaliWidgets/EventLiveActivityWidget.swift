//
//  EventLiveActivityWidget.swift
//  CaliWidgets
//

import ActivityKit
import AppIntents
import SwiftUI
import WidgetKit

struct EventLiveActivityWidget: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: EventActivityAttributes.self) { context in
            EventLockScreenView(
                title: context.state.title,
                eventId: context.attributes.eventId
            )
            .widgetURL(cameraURL(eventId: context.attributes.eventId))
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Text("cali")
                        .font(.system(.subheadline, design: .rounded, weight: .semibold))
                }
                DynamicIslandExpandedRegion(.center) {
                    Text(context.state.title)
                        .font(.system(.headline, design: .rounded, weight: .semibold))
                        .lineLimit(2)
                        .minimumScaleFactor(0.7)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    EventActionButtons(eventId: context.attributes.eventId)
                }
            } compactLeading: {
                Text("cali")
                    .font(.system(.caption2, design: .rounded, weight: .semibold))
            } compactTrailing: {
                Text(context.state.title)
                    .font(.system(.caption, design: .rounded, weight: .semibold))
                    .lineLimit(1)
            } minimal: {
                Image(systemName: "calendar")
            }
            .widgetURL(cameraURL(eventId: context.attributes.eventId))
        }
    }
}

private struct EventLockScreenView: View {
    let title: String
    let eventId: String

    var body: some View {
        HStack(spacing: 8) {
            Text(title)
                .font(.system(.headline, design: .rounded, weight: .semibold))
                .lineLimit(2)
                .minimumScaleFactor(0.7)
                .frame(maxWidth: .infinity, alignment: .leading)

            EventActionButtons(eventId: eventId)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }
}

private struct EventActionButtons: View {
    let eventId: String

    var body: some View {
        HStack(spacing: 4) {
            DismissActivityButton(eventId: eventId)
            OpenCameraButton(eventId: eventId)
        }
    }
}

private struct OpenCameraButton: View {
    let eventId: String

    var body: some View {
        Button(intent: OpenCameraIntent(eventId: eventId)) {
            Image(systemName: "camera.fill")
                .font(.system(size: 16, weight: .semibold))
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .accessibilityLabel("Take Photo")
    }
}

private struct DismissActivityButton: View {
    let eventId: String

    var body: some View {
        Button(intent: DismissEventActivityIntent(eventId: eventId)) {
            Image(systemName: "xmark")
                .font(.system(size: 14, weight: .semibold))
                .frame(width: 36, height: 44)
                .contentShape(Rectangle())
        }
        .accessibilityLabel("Dismiss")
    }
}

private func cameraURL(eventId: String) -> URL? {
    var components = URLComponents()
    components.scheme = "cali"
    components.host = "camera"
    components.queryItems = [URLQueryItem(name: "eventId", value: eventId)]
    return components.url
}
