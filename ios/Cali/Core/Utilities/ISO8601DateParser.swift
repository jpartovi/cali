//
//  ISO8601DateParser.swift
//  Cali
//

import Foundation

enum ISO8601DateParser {
    static var jsonStrategy: JSONDecoder.DateDecodingStrategy {
        .custom { decoder in
            let container = try decoder.singleValueContainer()
            let value = try container.decode(String.self)
            if let date = parse(value) {
                return date
            }
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unrecognized date: \(value)"
            )
        }
    }

    static func parse(_ value: String) -> Date? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        let withT = trimmed.replacingOccurrences(of: " ", with: "T")
        let candidates = [trimmed, withT, padTimezone(withT)]
        for candidate in candidates {
            if let date = fractional.date(from: candidate) {
                return date
            }
            if let date = iso.date(from: candidate) {
                return date
            }
            if let truncated = truncateFractionalSeconds(candidate) {
                if let date = fractional.date(from: truncated) {
                    return date
                }
                if let date = iso.date(from: truncated) {
                    return date
                }
            }
        }
        return jsonFractional.date(from: withT)
            ?? json.date(from: withT)
            ?? postgresFractionalFormatter.date(from: trimmed)
            ?? postgresFormatter.date(from: trimmed)
    }

    private static func padTimezone(_ value: String) -> String {
        if value.hasSuffix("+00") || value.hasSuffix("-00") {
            return value + ":00"
        }
        return value
    }

    /// ISO8601DateFormatter often rejects 4–6 fractional digits from Postgres/Pydantic.
    private static func truncateFractionalSeconds(_ value: String) -> String? {
        guard let dot = value.firstIndex(of: ".") else { return nil }
        let head = value[..<dot]
        let rest = value[value.index(after: dot)...]
        var digits = ""
        var suffix = ""
        var seenNonDigit = false
        for character in rest {
            if !seenNonDigit, character.isNumber {
                digits.append(character)
            } else {
                seenNonDigit = true
                suffix.append(character)
            }
        }
        guard digits.count > 3 else { return nil }
        let truncated = String(digits.prefix(3))
        return "\(head).\(truncated)\(suffix)"
    }

    private static let fractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let iso: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private static let json: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ssxxxxx"
        return formatter
    }()

    private static let jsonFractional: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSSxxxxx"
        return formatter
    }()

    private static let postgresFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ssxxxxx"
        return formatter
    }()

    private static let postgresFractionalFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss.SSSSSSxxxxx"
        return formatter
    }()
}
