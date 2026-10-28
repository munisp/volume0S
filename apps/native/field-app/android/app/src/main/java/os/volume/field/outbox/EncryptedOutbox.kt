package os.volume.field.outbox

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import net.sqlcipher.database.SQLiteDatabase
import net.sqlcipher.database.SupportFactory
import java.security.SecureRandom

enum class OutboxStatus { LOCAL_ONLY, UPLOADING, ACKNOWLEDGED, FAILED }
data class OutboxEntry(val eventId: String, val payload: ByteArray, val status: OutboxStatus, val attempts: Int)

/** SQLCipher at-rest encryption with explicit count/byte bounds; no plaintext Room fallback. */
class EncryptedOutbox(private val context: Context, private val maxRecords: Int = 500, private val maxBytes: Int = 50 * 1024 * 1024) {
    init { SQLiteDatabase.loadLibs(context) }
    private fun passphrase(): ByteArray {
        val preferences = EncryptedSharedPreferences.create(context, "outbox-key",
            MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV, EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM)
        val encoded = preferences.getString("key", null) ?: ByteArray(32).also { SecureRandom().nextBytes(it) }
            .joinToString("") { "%02x".format(it) }.also { preferences.edit().putString("key", it).apply() }
        return encoded.toByteArray()
    }
    private fun database(): SQLiteDatabase = SQLiteDatabase.openOrCreateDatabase(context.getDatabasePath("field-outbox.db"), passphrase(), null).also {
        it.execSQL("CREATE TABLE IF NOT EXISTS outbox(event_id TEXT PRIMARY KEY, payload BLOB NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL)")
    }
    fun enqueue(eventId: String, payload: ByteArray) = database().use { db ->
        require(payload.isNotEmpty() && payload.size <= 1024 * 1024)
        db.rawQuery("SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)),0) FROM outbox WHERE status != 'ACKNOWLEDGED'", null).use { cursor ->
            cursor.moveToFirst(); require(cursor.getInt(0) < maxRecords && cursor.getLong(1) + payload.size <= maxBytes) { "Encrypted outbox is full; synchronize before capture" }
        }
        db.execSQL("INSERT OR IGNORE INTO outbox(event_id,payload,status,updated_at) VALUES(?,?,?,?)", arrayOf(eventId, payload, OutboxStatus.LOCAL_ONLY.name, System.currentTimeMillis()))
        if (db.rawQuery("SELECT changes()", null).use { it.moveToFirst(); it.getInt(0) } != 1) throw IllegalStateException("Duplicate event ID")
    }
    fun pending(limit: Int = 20): List<OutboxEntry> = database().use { db ->
        db.rawQuery("SELECT event_id,payload,status,attempts FROM outbox WHERE status IN ('LOCAL_ONLY','FAILED','UPLOADING') ORDER BY updated_at LIMIT ?", arrayOf(limit.toString())).use { cursor ->
            buildList { while (cursor.moveToNext()) add(OutboxEntry(cursor.getString(0), cursor.getBlob(1), OutboxStatus.valueOf(cursor.getString(2)), cursor.getInt(3))) }
        }
    }
    fun status(eventId: String, status: OutboxStatus) = database().use { db ->
        if (status == OutboxStatus.UPLOADING) db.execSQL("UPDATE outbox SET status=?,attempts=attempts+1,updated_at=? WHERE event_id=? AND status != 'ACKNOWLEDGED'", arrayOf(status.name, System.currentTimeMillis(), eventId))
        else db.execSQL("UPDATE outbox SET status=?,updated_at=? WHERE event_id=?", arrayOf(status.name, System.currentTimeMillis(), eventId))
    }
    fun purgeAcknowledged(olderThanUnixMs: Long) = database().use { it.execSQL("DELETE FROM outbox WHERE status='ACKNOWLEDGED' AND updated_at<?", arrayOf(olderThanUnixMs)) }
}
