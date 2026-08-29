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
            .activityBackgroundTint(LiveActivityBrand.primary.opacity(0.22))
            .activitySystemActionForegroundColor(.white)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.center) {
                    Text(context.state.title)
                        .font(.system(.headline, design: .rounded, weight: .semibold))
                        .lineLimit(2)
                        .minimumScaleFactor(0.7)
                        .multilineTextAlignment(.center)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    EventIslandActionBar(eventId: context.attributes.eventId)
                }
            } compactLeading: {
                Image(systemName: "camera.fill")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(LiveActivityBrand.primary)
            } compactTrailing: {
                Text(context.state.title)
                    .font(.system(.caption, design: .rounded, weight: .semibold))
                    .lineLimit(1)
            } minimal: {
                Image(systemName: "camera.fill")
                    .foregroundStyle(LiveActivityBrand.primary)
            }
            .widgetURL(cameraURL(eventId: context.attributes.eventId))
            .keylineTint(LiveActivityBrand.primary)
        }
    }
}

private struct EventLockScreenView: View {
    let title: String
    let eventId: String

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            Text(title)
                .font(.system(.headline, design: .rounded, weight: .semibold))
                .foregroundStyle(.primary)
                .lineLimit(2)
                .minimumScaleFactor(0.75)
                .frame(maxWidth: .infinity, alignment: .leading)

            EventActionButtons(eventId: eventId)
        }
        .padding(.leading, 16)
        .padding(.trailing, 10)
        .padding(.vertical, 12)
    }
}

private struct EventActionButtons: View {
    let eventId: String

    var body: some View {
        HStack(spacing: 8) {
            DismissActivityButton(eventId: eventId)
            OpenCameraButton(eventId: eventId)
        }
    }
}

private struct EventIslandActionBar: View {
    let eventId: String

    var body: some View {
        HStack(spacing: 10) {
            DismissActivityButton(eventId: eventId)
            OpenCameraButton(eventId: eventId)
        }
        .padding(.top, 4)
    }
}

private let liveActivityButtonSize: CGFloat = 56

private struct OpenCameraButton: View {
    let eventId: String

    var body: some View {
        Button(intent: OpenCameraIntent(eventId: eventId)) {
            Image(systemName: "camera.fill")
                .font(.system(size: 22, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: liveActivityButtonSize, height: liveActivityButtonSize)
                .background(LiveActivityBrand.gradient, in: Circle())
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Take Photo")
    }
}

private struct DismissActivityButton: View {
    let eventId: String

    var body: some View {
        Button(intent: DismissEventActivityIntent(eventId: eventId)) {
            Image(systemName: "xmark")
                .font(.system(size: 20, weight: .bold))
                .foregroundStyle(.primary.opacity(0.72))
                .frame(width: liveActivityButtonSize, height: liveActivityButtonSize)
                .background(Circle().fill(Color.primary.opacity(0.08)))
                .overlay(
                    Circle()
                        .strokeBorder(Color.primary.opacity(0.22), lineWidth: 1.25)
                )
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Dismiss")
    }
}

private enum LiveActivityBrand {
    static let primary = Color(
        .displayP3,
        red: 1.0,
        green: 0.56,
        blue: 0.24,
        opacity: 1.0
    )

    static let gradient = LinearGradient(
        colors: [
            Color(.displayP3, red: 1.0, green: 0.48, blue: 0.29, opacity: 1.0),
            Color(.displayP3, red: 1.0, green: 0.68, blue: 0.24, opacity: 1.0),
        ],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )
}

private func cameraURL(eventId: String) -> URL? {
    var components = URLComponents()
    components.scheme = "cali"
    components.host = "camera"
    components.queryItems = [URLQueryItem(name: "eventId", value: eventId)]
    return components.url
}
