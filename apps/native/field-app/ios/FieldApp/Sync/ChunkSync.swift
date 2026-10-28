import CryptoKit
import Foundation

struct Chunk { let index: Int; let digestHex: String; let bytes: Data }

enum SyncError: Error { case unauthorized, rejected, invalidManifest }

actor ChunkSync {
    static let chunkSize = 256 * 1024
    let baseURL: URL
    init(baseURL: URL) throws { guard baseURL.scheme == "https" else { throw SyncError.rejected }; self.baseURL = baseURL }
    static func chunks(_ payload: Data) -> [Chunk] {
        stride(from: 0, to: payload.count, by: chunkSize).enumerated().map { index, offset in
            let bytes = payload.subdata(in: offset..<min(offset + chunkSize, payload.count))
            return Chunk(index: index, digestHex: SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined(), bytes: bytes)
        }
    }
    func upload(envelope: Data) async throws -> String {
        let local = try JSONSerialization.jsonObject(with: envelope) as! [String: Any]
        let payload = Data(base64Encoded: local["document_base64"] as! String)!, list = Self.chunks(payload)
        let tenant = local["tenant_id"] as! String, device = local["device_id"] as! String, event = local["event_id"] as! String
        let uploadID = "upload-\(event)"; let root = SHA256.hash(data: payload).map { String(format: "%02x", $0) }.joined()
        var start = local; start.removeValue(forKey: "document_base64"); start["upload_id"] = uploadID; start["total_chunks"] = list.count
        start["total_bytes"] = payload.count; start["payload_sha256"] = root
        let session = try await request("POST", "/v1/tenants/\(tenant)/census/uploads", start, device, event)
        if session["status"] as? String == "published" { return event }
        let resume = Set(session["resume"] as? [Int] ?? [])
        for chunk in list where resume.contains(chunk.index) {
            let body: [String: Any] = ["index": chunk.index, "encoded": chunk.bytes.base64EncodedString(), "expected_sha256": chunk.digestHex]
            let response = try await request("PUT", "/v1/tenants/\(tenant)/census/uploads/\(uploadID)/chunks/\(chunk.index)", body, device, "\(event):\(chunk.digestHex)")
            guard !(response["resume"] as? [Int] ?? []).contains(chunk.index) else { throw SyncError.invalidManifest }
        }
        let completed = try await request("POST", "/v1/tenants/\(tenant)/census/uploads/\(uploadID)/complete", [:], device, "\(event):complete")
        guard completed["status"] as? String == "published", completed["event_id"] as? String == event else { throw SyncError.invalidManifest }
        return event
    }
    private func request(_ method: String, _ path: String, _ json: [String: Any], _ device: String, _ idempotency: String) async throws -> [String: Any] {
        guard let token = KeychainTokenStore().accessToken() else { throw SyncError.unauthorized }
        var request = URLRequest(url: URL(string: path, relativeTo: baseURL)!); request.httpMethod = method; request.httpBody = try JSONSerialization.data(withJSONObject: json)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type"); request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue(device, forHTTPHeaderField: "X-VolumeOS-Device"); request.setValue(idempotency, forHTTPHeaderField: "Idempotency-Key")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else { throw SyncError.rejected }
        return try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
    }
}
