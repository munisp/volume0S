import AuthenticationServices
import CryptoKit
import Foundation
import Security
import UIKit

struct OIDCConfig {
    let authorizationEndpoint: URL
    let tokenEndpoint: URL
    let clientID: String
    let redirectURI: URL
    let audience: String
}

struct TokenSet: Codable {
    let accessToken: String
    let refreshToken: String?
    let idToken: String?
    let expiresAt: Date
}

enum OIDCError: Error { case invalidCallback, invalidState, tokenRejected }

/// ASWebAuthenticationSession Authorization Code + PKCE; no client secret or embedded web view.
@MainActor
final class OIDCAuth: NSObject, ASWebAuthenticationPresentationContextProviding {
    let config: OIDCConfig
    private var expectedState: String?
    init(config: OIDCConfig) { self.config = config }

    func signIn() async throws -> TokenSet {
        let verifier = Self.base64URL(Self.random(count: 32))
        let state = Self.base64URL(Self.random(count: 32))
        let challenge = Self.base64URL(Data(SHA256.hash(data: Data(verifier.utf8))))
        expectedState = state
        var components = URLComponents(url: config.authorizationEndpoint, resolvingAgainstBaseURL: false)!
        components.queryItems = [
            .init(name: "response_type", value: "code"), .init(name: "client_id", value: config.clientID),
            .init(name: "redirect_uri", value: config.redirectURI.absoluteString), .init(name: "scope", value: "openid profile offline_access"),
            .init(name: "state", value: state), .init(name: "code_challenge", value: challenge), .init(name: "code_challenge_method", value: "S256"),
            .init(name: "audience", value: config.audience), .init(name: "prompt", value: "login"),
        ]
        let callback: URL = try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(url: components.url!, callbackURLScheme: config.redirectURI.scheme) { url, error in
                if let url { continuation.resume(returning: url) }
                else { continuation.resume(throwing: error ?? OIDCError.invalidCallback) }
            }
            session.presentationContextProvider = self
            session.prefersEphemeralWebBrowserSession = false
            session.start()
        }
        let items = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems
        guard items?.first(where: { $0.name == "state" })?.value == state else { throw OIDCError.invalidState }
        guard let code = items?.first(where: { $0.name == "code" })?.value else { throw OIDCError.invalidCallback }
        return try await exchange(code: code, verifier: verifier)
    }

    private func exchange(code: String, verifier: String) async throws -> TokenSet {
        var request = URLRequest(url: config.tokenEndpoint); request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        let fields = ["grant_type": "authorization_code", "code": code, "client_id": config.clientID,
                      "redirect_uri": config.redirectURI.absoluteString, "code_verifier": verifier]
        request.httpBody = fields.map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "")" }.joined(separator: "&").data(using: .utf8)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { throw OIDCError.tokenRejected }
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let access = object?["access_token"] as? String else { throw OIDCError.tokenRejected }
        let tokens = TokenSet(accessToken: access, refreshToken: object?["refresh_token"] as? String,
                              idToken: object?["id_token"] as? String,
                              expiresAt: Date().addingTimeInterval(object?["expires_in"] as? TimeInterval ?? 0))
        try KeychainTokenStore().save(tokens); return tokens
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first?.windows.first ?? ASPresentationAnchor()
    }

    static func random(count: Int) -> Data { var data = Data(count: count); _ = data.withUnsafeMutableBytes { SecRandomCopyBytes(kSecRandomDefault, count, $0.baseAddress!) }; return data }
    static func base64URL(_ data: Data) -> String { data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "") }
}

final class KeychainTokenStore {
    private let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "os.volume.field.oidc", kSecAttrAccount as String: "tokens"]
    func save(_ tokens: TokenSet) throws {
        let data = try JSONEncoder().encode(tokens); SecItemDelete(query as CFDictionary)
        var item = query; item[kSecValueData as String] = data; item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else { throw OIDCError.tokenRejected }
    }
    func accessToken() -> String? {
        var item = query; item[kSecReturnData as String] = true; item[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?; guard SecItemCopyMatching(item as CFDictionary, &result) == errSecSuccess,
            let data = result as? Data, let tokens = try? JSONDecoder().decode(TokenSet.self, from: data), tokens.expiresAt > Date() else { return nil }
        return tokens.accessToken
    }
    func clear() { SecItemDelete(query as CFDictionary) }
}
