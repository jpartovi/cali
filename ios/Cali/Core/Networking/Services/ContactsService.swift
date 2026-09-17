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
    let updated: Int
}

struct ContactRecord: Identifiable, Decodable, Hashable, Sendable {
    let id: String
    let appleIdentifier: String
    let displayName: String
    let givenName: String?
    let familyName: String?
    let nickname: String?
    let inviteEmail: String?
}

enum ContactsServiceError: Error {
    case invalidURL
    case unauthorized
    case http(Int)
    case decoding(Error)
    case network(Error)
}

protocol ContactsServicing {
    func importAppleContacts(accessToken: String, contacts: [AppleContactPayload]) async throws -> AppleContactsImportResponse
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
        var updated = 0

        if contacts.isEmpty {
            return AppleContactsImportResponse(imported: 0, updated: 0)
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
            updated += response.updated
        }

        contactsLogger.debug("Apple import imported=\(imported, privacy: .public) updated=\(updated, privacy: .public)")
        return AppleContactsImportResponse(imported: imported, updated: updated)
    }

    func listContacts(accessToken: String) async throws -> [ContactRecord] {
        try await getJSON(path: "/api/v1/contacts/", accessToken: accessToken)
    }

    private func getJSON<Response: Decodable>(path: String, accessToken: String) async throws -> Response {
        var request = try makeRequest(path: path, accessToken: accessToken, method: "GET")
        request.setValue(nil, forHTTPHeaderField: "Content-Type")
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
