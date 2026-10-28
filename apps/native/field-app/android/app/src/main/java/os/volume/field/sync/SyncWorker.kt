package os.volume.field.sync

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import os.volume.field.auth.TokenStore
import os.volume.field.outbox.EncryptedOutbox
import os.volume.field.outbox.OutboxStatus

class SyncWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val outbox = EncryptedOutbox(applicationContext)
        val sync = ChunkSync(checkNotNull(inputData.getString("api_url"))) { TokenStore(applicationContext).accessToken() }
        outbox.pending().forEach { entry ->
            try {
                outbox.status(entry.eventId, OutboxStatus.UPLOADING)
                check(sync.upload(entry.payload) == entry.eventId)
                outbox.status(entry.eventId, OutboxStatus.ACKNOWLEDGED)
            } catch (error: Exception) {
                outbox.status(entry.eventId, OutboxStatus.FAILED)
                return Result.retry()
            }
        }
        return Result.success()
    }
}
