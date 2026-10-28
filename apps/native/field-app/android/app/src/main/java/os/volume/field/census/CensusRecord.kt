package os.volume.field.census

import java.security.MessageDigest
import android.util.Base64
import org.json.JSONObject

private val SAFE_ID = Regex("^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
private val BASES = setOf("consent", "contract", "legal_obligation", "legitimate_interest")
private const val CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

data class CensusRecord(
    val tenantId: String, val deviceId: String, val localId: String, val consentReference: String,
    val lawfulBasis: String, val latitude: Double, val longitude: Double, val accuracyMeters: Float,
    val observedAtUnix: Long, val attributes: Map<String, String>,
) {
    val eventId: String get() = deterministicUlid("$tenantId|$deviceId|$localId")
    init {
        require(SAFE_ID.matches(tenantId) && SAFE_ID.matches(deviceId) && SAFE_ID.matches(localId) && SAFE_ID.matches(consentReference))
        require(lawfulBasis in BASES)
        require(latitude in -90.0..90.0 && longitude in -180.0..180.0)
        require(accuracyMeters > 0.0f && accuracyMeters <= 100.0f) { "A current accurate geotag is required" }
        require(observedAtUnix > 0 && attributes.size <= 5 && attributes["business_name"]?.isNotBlank() == true)
        require(attributes.keys.all { it in setOf("business_name", "niche", "address", "notes", "phone") })
        require(attributes.values.all { it.length <= 1000 })
    }
    fun document(): ByteArray = JSONObject(attributes).toString().toByteArray()
    fun payload(): ByteArray = JSONObject()
        .put("tenant_id", tenantId).put("device_id", deviceId).put("event_id", eventId)
        .put("captured_at_unix", observedAtUnix).put("latitude", latitude).put("longitude", longitude)
        .put("accuracy_m", accuracyMeters).put("consent_reference", consentReference).put("lawful_basis", lawfulBasis)
        .put("document_base64", Base64.encodeToString(document(), Base64.NO_WRAP)).toString().toByteArray()
}

fun deterministicUlid(value: String): String {
    val input = sha256(value.toByteArray()).copyOf(16)
    val bits = StringBuilder(128); input.forEach { bits.append(it.toInt().and(255).toString(2).padStart(8, '0')) }
    return buildString(26) {
        append(CROCKFORD[bits.substring(0, 3).toInt(2)])
        for (offset in 3 until 128 step 5) append(CROCKFORD[bits.substring(offset, offset + 5).toInt(2)])
    }
}

fun sha256(value: String): String = sha256(value.toByteArray()).joinToString("") { "%02x".format(it) }
fun sha256(value: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(value)
