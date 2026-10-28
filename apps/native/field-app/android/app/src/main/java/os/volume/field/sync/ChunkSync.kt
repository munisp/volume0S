package os.volume.field.sync

import android.util.Base64
import os.volume.field.census.sha256
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

const val CHUNK_SIZE = 256 * 1024
data class Chunk(val index: Int, val digestHex: String, val bytes: ByteArray)

fun chunks(payload: ByteArray): List<Chunk> {
    require(payload.isNotEmpty() && payload.size <= 8 * 1024 * 1024)
    return payload.asIterable().chunked(CHUNK_SIZE).mapIndexed { index, bytes ->
        val data = bytes.toByteArray(); Chunk(index, sha256(data).joinToString("") { "%02x".format(it) }, data)
    }
}

/** Existing svc-census-sync protocol: resume by missing indexes and acknowledge only published events. */
class ChunkSync(private val baseUrl: String, private val token: () -> String?, private val client: OkHttpClient = OkHttpClient()) {
    init { require(baseUrl.startsWith("https://")) }
    suspend fun upload(envelope: ByteArray): String {
        val local = JSONObject(String(envelope)); val payload = Base64.decode(local.getString("document_base64"), Base64.DEFAULT)
        val list = chunks(payload); val root = sha256(payload).joinToString("") { "%02x".format(it) }
        val tenant = local.getString("tenant_id"); val device = local.getString("device_id"); val event = local.getString("event_id")
        val uploadId = "upload-$event"
        val start = JSONObject(local.toString()).apply {
            remove("document_base64"); put("upload_id", uploadId); put("total_chunks", list.size)
            put("total_bytes", payload.size); put("payload_sha256", root)
        }
        val session = request("POST", "/v1/tenants/$tenant/census/uploads", start, device, event)
        if (session.getString("status") == "published") return event
        val resume = session.getJSONArray("resume"); val missing = (0 until resume.length()).map { resume.getInt(it) }.toSet()
        for (chunk in list.filter { it.index in missing }) {
            val body = JSONObject().put("index", chunk.index)
                .put("encoded", Base64.encodeToString(chunk.bytes, Base64.NO_WRAP)).put("expected_sha256", chunk.digestHex)
            val response = request("PUT", "/v1/tenants/$tenant/census/uploads/$uploadId/chunks/${chunk.index}", body, device, "$event:${chunk.digestHex}")
            require(response.getJSONArray("resume").let { (0 until it.length()).none { index -> it.getInt(index) == chunk.index } })
        }
        val completed = request("POST", "/v1/tenants/$tenant/census/uploads/$uploadId/complete", JSONObject(), device, "$event:complete")
        require(completed.getString("status") == "published" && completed.getString("event_id") == event)
        return event
    }
    private fun request(method: String, path: String, json: JSONObject, device: String, idempotency: String): JSONObject {
        val access = token() ?: throw IllegalStateException("OIDC access token required")
        val request = Request.Builder().url(baseUrl.trimEnd('/') + path).method(method, json.toString().toRequestBody("application/json".toMediaType()))
            .header("Authorization", "Bearer $access").header("X-VolumeOS-Device", device).header("Idempotency-Key", idempotency).build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw IllegalStateException("Sync rejected with HTTP ${response.code}")
            return JSONObject(response.body!!.string())
        }
    }
}
