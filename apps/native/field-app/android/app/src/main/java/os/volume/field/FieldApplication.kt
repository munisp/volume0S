package os.volume.field

import android.app.Application
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import os.volume.field.sync.SyncWorker
import java.util.concurrent.TimeUnit

class FieldApplication : Application()
