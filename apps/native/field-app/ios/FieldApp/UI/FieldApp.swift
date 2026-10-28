import CoreLocation
import SwiftUI

@main
struct VolumeOSFieldApp: App {
    var body: some Scene { WindowGroup { FieldView() } }
}

@MainActor
final class FieldViewModel: NSObject, ObservableObject, CLLocationManagerDelegate {
    @Published var status = "Signed out"
    @Published var tenant = "", consent = ""
    private let location = CLLocationManager()
    private lazy var oidc = OIDCAuth(config: .init(
        authorizationEndpoint: URL(string: "https://id.operator.example.invalid/authorize")!,
        tokenEndpoint: URL(string: "https://id.operator.example.invalid/token")!, clientID: "volumeos-field",
        redirectURI: URL(string: "os.volume.field:/oauth2redirect")!, audience: "volumeos"))
    func signIn() async { do { _ = try await oidc.signIn(); status = "Signed in" } catch { status = "Sign-in cancelled or rejected" } }
    func capture() {
        location.delegate = self; location.desiredAccuracy = kCLLocationAccuracyBest
        location.requestWhenInUseAuthorization(); location.requestLocation(); status = "Locating…"
    }
    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        Task { @MainActor in
            guard let value = locations.first else { status = "Location unavailable"; return }
            do {
                let device = UIDevice.current.identifierForVendor?.uuidString ?? "unknown-device"
                let record = try CensusRecord(tenantID: tenant, deviceID: device, localID: UUID().uuidString,
                    consentReference: consent, lawfulBasis: "consent", latitude: value.coordinate.latitude,
                    longitude: value.coordinate.longitude, accuracyMeters: value.horizontalAccuracy,
                    observedAtUnix: Int64(value.timestamp.timeIntervalSince1970), attributes: ["business_name": "Field census"]).validated()
                try EncryptedOutbox().enqueue(eventID: record.eventID, payload: record.envelope())
                status = "Saved local-only: \(record.eventID)"
            } catch { status = "Capture rejected" }
        }
    }
    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) { Task { @MainActor in status = "Location unavailable" } }
    func synchronize() async {
        do {
            let outbox = try EncryptedOutbox()
            let sync = try ChunkSync(baseURL: URL(string: "https://api.operator.example.invalid")!)
            for entry in try outbox.pending() {
                do {
                    try outbox.setStatus(entry.eventID, "UPLOADING")
                    guard try await sync.upload(envelope: entry.payload) == entry.eventID else { throw SyncError.invalidManifest }
                    try outbox.setStatus(entry.eventID, "ACKNOWLEDGED")
                } catch {
                    try outbox.setStatus(entry.eventID, "FAILED"); status = "Synchronization failed; will resume"; return
                }
            }
            status = "All pending records acknowledged"
        } catch { status = "Encrypted outbox unavailable" }
    }
}

struct FieldView: View {
    @StateObject private var model = FieldViewModel()
    var body: some View {
        NavigationStack {
            Form {
                Section("Tenant and consent") { TextField("Tenant identifier", text: $model.tenant); TextField("Consent reference", text: $model.consent) }
                Section("Field operations") {
                    Button("Sign in with system browser") { Task { await model.signIn() } }
                    Button("Capture geotagged census") { model.capture() }.disabled(model.tenant.isEmpty || model.consent.isEmpty)
                    Button("Synchronize encrypted outbox") { Task { await model.synchronize() } }
                }
                Section { Text(model.status); Text("Local-only is not acknowledged. Every chunk and the final payload must pass server verification.") }
            }.navigationTitle("VolumeOS Field")
        }
    }
}
