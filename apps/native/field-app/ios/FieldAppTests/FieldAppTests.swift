import XCTest
@testable import FieldApp

final class FieldAppTests: XCTestCase {
    func record() -> CensusRecord {
        CensusRecord(tenantID: "tenant-a", deviceID: "device-1", localID: "local-1", consentReference: "consent-1",
                     lawfulBasis: "consent", latitude: 6.5244, longitude: 3.3792, accuracyMeters: 8,
                     observedAtUnix: 1_700_000_000, attributes: ["business_name": "Test Business"])
    }
    func testDeterministicULIDAndValidation() throws {
        XCTAssertEqual(try record().validated().eventID, "48MMQRF017Y0HQ4C8QSQG8DD7Z"); XCTAssertEqual(record().eventID.count, 26)
        XCTAssertThrowsError(try CensusRecord(tenantID: "tenant-a", deviceID: "device-1", localID: "local-1", consentReference: "../bad", lawfulBasis: "consent", latitude: 0, longitude: 0, accuracyMeters: 8, observedAtUnix: 1, attributes: ["business_name": "B"]).validated())
    }
    func testContentAddressedChunksReassemble() {
        let payload = Data((0..<(ChunkSync.chunkSize * 2 + 17)).map { UInt8($0 % 251) })
        let chunks = ChunkSync.chunks(payload); XCTAssertEqual(chunks.count, 3)
        XCTAssertEqual(chunks.flatMap { Array($0.bytes) }, Array(payload)); XCTAssertTrue(chunks.allSatisfy { $0.digestHex.count == 64 })
    }
}
