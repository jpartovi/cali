//
//  ContactsService.swift
//  Cali
//

import Foundation
import os

private let contactsLogger = Logger(subsystem: "com.cali.app", category: "ContactsService")

struct AppleContactsImportRequest: Encodable {
    let contacts: [AppleContactPayload]
}

struct AppleContactsImportResponse: Decodable {
    let imported: Int
    let merged: Int
    let emailsAdded: Int
    let phonesAdded: Int
}

struct GoogleContactsAccountImportResult: Decodable {
    let accountId: String
    let email: String?
    let created: Int
    let enriched: Int
    let emailsAdded: Int
    let phonesAdded: Int
    let needsReauth: Bool
    let error: String?
}

struct GoogleContactsImportResponse: Decodable {
    let accounts: [GoogleContactsAccountImportResult]
    let needsReauth: Bool
    let needsReauthAccountIds: [String]
}

struct ContactEmailRecord: Decodable, Hashable, Sendable {
    let email: String
    let label: String?
    let isPrimary: Bool
    let source: String
}

struct ContactPhoneRecord: Decodable, Hashable, Sendable {
    let phoneRaw: String
    let phoneE164: String?
    let label: String?
    let isPrimary: Bool
    let source: String
}

struct ContactRecord: Identifiable, Decodable, Hashable, Sendable {
    let id: String
    let displayName: String
    let givenName: String?
    let familyName: String?
    let nickname: String?
    let emails: [ContactEmailRecord]
    let phones: [ContactPhoneRecord]
}

enum ContactsServiceError: Error {
    case invalidURL
    case unauthorized
    case needsReauth(GoogleContactsImportResponse)
    case http(Int)
    case decoding(Error)
    case network(Error)
}

protocol ContactsServicing {
    func importAppleContacts(accessToken: String, contacts: [AppleContactPayload]) async throws -> AppleContactsImportResponse
    func importGoogleContacts(accessToken: String) async throws -> GoogleContactsImportResponse
    func listContacts(accessToken: String) async throws -> [ContactRecord]
}

final class ContactsService: ContactsServicing {
    private let baseURL: URL
    private let urlSession: URLSession
    private let chunkSize: Int

    init(
        baseURL: URL = AppConfiguration.backendURL,
        urlSession: URLSession? = nil,
        chunkSize: Int = 200
    ) {
        self.baseURL = baseURL
        self.urlSession = urlSession ?? Self.makeImportSession()
        self.chunkSize = chunkSize
    }

    private static func makeImportSession() -> URLSession {
        let configuration = URLSessionConfiguration.default
        configuration.waitsForConnectivity = true
        configuration.timeoutIntervalForRequest = 180
        configuration.timeoutIntervalForResource = 300
        configuration.httpMaximumConnectionsPerHost = 4
        configuration.networkServiceType = .default
        return URLSession(configuration: configuration)
    }

    func importAppleContacts(accessToken: String, contacts: [AppleContactPayload]) async throws -> AppleContactsImportResponse {
        var imported = 0
        var merged = 0
        var emailsAdded = 0
        var phonesAdded = 0

        if contacts.isEmpty {
            return AppleContactsImportResponse(imported: 0, merged: 0, emailsAdded: 0, phonesAdded: 0)
        }

        for chunk in stride(from: 0, to: contacts.count, by: chunkSize) {
            let end = min(chunk + chunkSize, contacts.count)
            let slice = Array(contacts[chunk..<end])
            let response: AppleContactsImportResponse = try await postJSON(
                path: "/api/v1/contacts/import/apple",
                accessToken: accessToken,
                body: AppleContactsImportRequest(contacts: slice)
            )
            imported += response.imported
            merged += response.merged
            emailsAdded += response.emailsAdded
            phonesAdded += response.phonesAdded
        }

        contactsLogger.debug("Apple import imported=\(imported, privacy: .public) merged=\(merged, privacy: .public)")
        return AppleContactsImportResponse(
            imported: imported,
            merged: merged,
            emailsAdded: emailsAdded,
            phonesAdded: phonesAdded
        )
    }

    func importGoogleContacts(accessToken: String) async throws -> GoogleContactsImportResponse {
        let response: GoogleContactsImportResponse = try await postEmpty(
            path: "/api/v1/contacts/import/google",
            accessToken: accessToken
        )
        if response.needsReauth {
            throw ContactsServiceError.needsReauth(response)
        }
        return response
    }

    func listContacts(accessToken: String) async throws -> [ContactRecord] {
        try await getJSON(path: "/api/v1/contacts/", accessToken: accessToken)
    }

    private func getJSON<Response: Decodable>(path: String, accessToken: String) async throws -> Response {
        var request = try makeRequest(path: path, accessToken: accessToken, method: "GET")
        request.setValue(nil, forHTTPHeaderField: "Content-Type")
        return try await decodeResponse(request: request)
    }

    private func postEmpty<Response: Decodable>(path: String, accessToken: String) async throws -> Response {
        let request = try makeRequest(path: path, accessToken: accessToken, method: "POST")
        return try await decodeResponse(request: request)
    }

    private func postJSON<Body: Encodable, Response: Decodable>(
        path: String,
        accessToken: String,
        body: Body?
    ) async throws -> Response {
        var request = try makeRequest(path: path, accessToken: accessToken, method: "POST")
        if let body {
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            request.httpBody = try encoder.encode(body)
        }
        return try await decodeResponse(request: request)
    }

    private func decodeResponse<Response: Decodable>(request: URLRequest) async throws -> Response {
        do {
            let (data, urlResponse) = try await urlSession.data(for: request)
            guard let httpResponse = urlResponse as? HTTPURLResponse else {
                throw ContactsServiceError.http(-1)
            }
            guard 200..<300 ~= httpResponse.statusCode else {
                if httpResponse.statusCode == 401 {
                    throw ContactsServiceError.unauthorized
                }
                contactsLogger.error("HTTP \(httpResponse.statusCode) contacts request")
                throw ContactsServiceError.http(httpResponse.statusCode)
            }
            do {
                let decoder = JSONDecoder()
                decoder.keyDecodingStrategy = .convertFromSnakeCase
                return try decoder.decode(Response.self, from: data)
            } catch {
                throw ContactsServiceError.decoding(error)
            }
        } catch let error as ContactsServiceError {
            throw error
        } catch {
            throw ContactsServiceError.network(error)
        }
    }

    private func makeRequest(path: String, accessToken: String, method: String) throws -> URLRequest {
        guard let url = URL(string: path, relativeTo: baseURL) else {
            throw ContactsServiceError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if method != "GET" {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        return request
    }
}
