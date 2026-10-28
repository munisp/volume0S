import CryptoKit
import Foundation

struct CensusRecord: Codable {
    let tenantID: String, deviceID: String, localID: String, consentReference: String, lawfulBasis: String
    let latitude: Double, longitude: Double, accuracyMeters: Double, observedAtUnix: Int64
    let attributes: [String: String]
    var eventID: String { Self.deterministicULID("\(tenantID)|\(deviceID)|\(localID)") }
    private static let safe = /^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$/
    private static let bases: Set<String> = ["consent", "contract", "legal_obligation", "legitimate_interest"]
    func validated() throws -> CensusRecord {
        guard [tenantID, deviceID, localID, consentReference].allSatisfy({ $0.wholeMatch(of: Self.safe) != nil }),
              Self.bases.contains(lawfulBasis), (-90...90).contains(latitude), (-180...180).contains(longitude),
              accuracyMeters > 0, accuracyMeters <= 100, observedAtUnix > 0, attributes["business_name"]?.isEmpty == false,
              attributes.count <= 5, attributes.keys.allSatisfy({ ["business_name", "niche", "address", "notes", "phone"].contains($0) }),
              attributes.values.allSatisfy({ $0.count <= 1000 }) else { throw CensusError.invalidRecord }
        return self
    }
    func document() throws -> Data { try JSONSerialization.data(withJSONObject: attributes, options: [.sortedKeys]) }
    func envelope() throws -> Data {
        let record = try validated()
        return try JSONSerialization.data(withJSONObject: [
            "tenant_id": tenantID, "device_id": deviceID, "event_id": eventID, "captured_at_unix": observedAtUnix,
            "latitude": latitude, "longitude": longitude, "accuracy_m": accuracyMeters,
            "consent_reference": consentReference, "lawful_basis": lawfulBasis,
            "document_base64": try record.document().base64EncodedString(),
        ], options: [.sortedKeys])
    }
    static func deterministicULID(_ value: String) -> String {
        let alphabet = Array("0123456789ABCDEFGHJKMNPQRSTVWXYZ"); let bytes = Array(SHA256.hash(data: Data(value.utf8)).prefix(16))
        var bits = bytes.map { String($0, radix: 2).leftPad(8) }.joined(); var output = String(alphabet[Int(bits.prefix(3), radix: 2)!]); bits.removeFirst(3)
        for offset in stride(from: 0, to: 125, by: 5) { let start = bits.index(bits.startIndex, offsetBy: offset); let end = bits.index(start, offsetBy: 5); output.append(alphabet[Int(bits[start..<end], radix: 2)!]) }
        return output
    }
}
enum CensusError: Error { case invalidRecord, outboxFull }
private extension String { func leftPad(_ count: Int) -> String { String(repeating: "0", count: max(0, count - self.count)) + self } }
