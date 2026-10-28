import Foundation
import SQLCipher
import Security

struct OutboxEntry { let eventID: String; let payload: Data; let status: String; let attempts: Int }

/// SQLCipher encrypted bounded outbox. The random database key is ThisDeviceOnly Keychain material.
final class EncryptedOutbox {
    private var db: OpaquePointer?
    private let maxRecords = 500, maxBytes = 50 * 1024 * 1024
    init() throws {
        let url = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!.appendingPathComponent("field-outbox.db")
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard sqlite3_open(url.path, &db) == SQLITE_OK else { throw CensusError.invalidRecord }
        var key = try Self.key(); defer { key.resetBytes(in: 0..<key.count) }
        key.withUnsafeBytes { _ = sqlite3_key(db, $0.baseAddress, Int32(key.count)) }
        try execute("CREATE TABLE IF NOT EXISTS outbox(event_id TEXT PRIMARY KEY,payload BLOB NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,updated_at INTEGER NOT NULL)")
    }
    deinit { sqlite3_close(db) }
    func enqueue(eventID: String, payload: Data) throws {
        guard !payload.isEmpty, payload.count <= 1024 * 1024 else { throw CensusError.invalidRecord }
        var statement: OpaquePointer?; defer { sqlite3_finalize(statement) }
        sqlite3_prepare_v2(db, "SELECT COUNT(*),COALESCE(SUM(LENGTH(payload)),0) FROM outbox WHERE status != 'ACKNOWLEDGED'", -1, &statement, nil)
        if sqlite3_step(statement) == SQLITE_ROW {
            guard sqlite3_column_int(statement, 0) < maxRecords, sqlite3_column_int64(statement, 1) + Int64(payload.count) <= maxBytes else { throw CensusError.outboxFull }
        }
        sqlite3_finalize(statement); statement = nil
        let sql = "INSERT INTO outbox(event_id,payload,status,updated_at) VALUES(?,?,'LOCAL_ONLY',?)"
        sqlite3_prepare_v2(db, sql, -1, &statement, nil); sqlite3_bind_text(statement, 1, eventID, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self))
        payload.withUnsafeBytes { sqlite3_bind_blob(statement, 2, $0.baseAddress, Int32(payload.count), unsafeBitCast(-1, to: sqlite3_destructor_type.self)) }
        sqlite3_bind_int64(statement, 3, Int64(Date().timeIntervalSince1970 * 1000)); guard sqlite3_step(statement) == SQLITE_DONE else { throw CensusError.invalidRecord }
    }
    func pending(limit: Int = 20) throws -> [OutboxEntry] {
        var statement: OpaquePointer?; defer { sqlite3_finalize(statement) }
        sqlite3_prepare_v2(db, "SELECT event_id,payload,status,attempts FROM outbox WHERE status IN ('LOCAL_ONLY','FAILED','UPLOADING') ORDER BY updated_at LIMIT \(min(max(limit, 1), 100))", -1, &statement, nil)
        var entries: [OutboxEntry] = []
        while sqlite3_step(statement) == SQLITE_ROW {
            let event = String(cString: sqlite3_column_text(statement, 0)), status = String(cString: sqlite3_column_text(statement, 2))
            let bytes = sqlite3_column_blob(statement, 1), count = Int(sqlite3_column_bytes(statement, 1))
            entries.append(OutboxEntry(eventID: event, payload: Data(bytes: bytes, count: count), status: status, attempts: Int(sqlite3_column_int(statement, 3))))
        }
        return entries
    }
    func setStatus(_ eventID: String, _ status: String) throws {
        try execute("UPDATE outbox SET status='\(status)',attempts=attempts+\(status == "UPLOADING" ? 1 : 0),updated_at=\(Int64(Date().timeIntervalSince1970 * 1000)) WHERE event_id='\(eventID)'")
    }
    private func execute(_ sql: String) throws { guard sqlite3_exec(db, sql, nil, nil, nil) == SQLITE_OK else { throw CensusError.invalidRecord } }
    private static func key() throws -> Data {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "os.volume.field.outbox", kSecAttrAccount as String: "key"]
        var read = query; read[kSecReturnData as String] = true; var result: AnyObject?
        if SecItemCopyMatching(read as CFDictionary, &result) == errSecSuccess, let data = result as? Data { return data }
        let data = OIDCAuth.random(count: 32); var item = query; item[kSecValueData as String] = data; item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else { throw CensusError.invalidRecord }; return data
    }
}
