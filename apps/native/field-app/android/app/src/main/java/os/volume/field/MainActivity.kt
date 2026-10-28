package os.volume.field

import android.Manifest
import android.annotation.SuppressLint
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import kotlinx.coroutines.launch
import net.openid.appauth.AuthorizationResponse
import os.volume.field.auth.OidcAuth
import os.volume.field.auth.OidcConfig
import os.volume.field.census.CensusRecord
import os.volume.field.outbox.EncryptedOutbox
import os.volume.field.outbox.OutboxStatus
import os.volume.field.sync.SyncWorker
import java.util.UUID

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val oidc = OidcAuth(this, OidcConfig(
            Uri.parse("https://id.operator.example.invalid"), Uri.parse("https://id.operator.example.invalid/authorize"),
            Uri.parse("https://id.operator.example.invalid/token"), "volumeos-field",
            Uri.parse("os.volume.field:/oauth2redirect"), "volumeos",
        ))
        setContent { MaterialTheme { FieldScreen(oidc) } }
    }
}

@Composable
fun FieldScreen(oidc: OidcAuth) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    var state by remember { mutableStateOf("Signed out") }
    var consent by remember { mutableStateOf("") }
    var tenant by remember { mutableStateOf("") }
    val authLauncher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val response = result.data?.let { AuthorizationResponse.fromIntent(it) }
        if (response == null) state = "Sign-in cancelled or rejected"
        else scope.launch { runCatching { oidc.exchange(response) }.onSuccess { state = "Signed in" }.onFailure { state = "Sign-in failed" } }
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted -> if (!granted) state = "Location permission required" }
    Column(Modifier.fillMaxSize().padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("VolumeOS Field", style = MaterialTheme.typography.headlineMedium)
        Text(state)
        Button(onClick = { authLauncher.launch(oidc.signInIntent()) }) { Text("Sign in with system browser") }
        OutlinedTextField(tenant, { tenant = it }, label = { Text("Tenant identifier") }, singleLine = true)
        OutlinedTextField(consent, { consent = it }, label = { Text("Consent / lawful-basis reference") }, singleLine = true)
        Button(onClick = {
            permission.launch(Manifest.permission.ACCESS_FINE_LOCATION)
            capture(context, tenant, consent) { state = it }
        }, enabled = tenant.isNotBlank() && consent.isNotBlank()) { Text("Capture geotagged census") }
        Button(onClick = {
            WorkManager.getInstance(context).enqueue(OneTimeWorkRequestBuilder<SyncWorker>()
                .setInputData(workDataOf("api_url" to "https://api.operator.example.invalid")).build())
            state = "Synchronization scheduled"
        }) { Text("Synchronize encrypted outbox") }
        Text("Records remain local-only until the server verifies every chunk and acknowledges the root digest.")
    }
}

@SuppressLint("MissingPermission")
private fun capture(context: android.content.Context, tenant: String, consent: String, done: (String) -> Unit) {
    LocationServices.getFusedLocationProviderClient(context).getCurrentLocation(Priority.PRIORITY_HIGH_ACCURACY, null).addOnSuccessListener { location ->
        if (location == null) { done("Current location unavailable"); return@addOnSuccessListener }
        runCatching {
            val device = android.provider.Settings.Secure.getString(context.contentResolver, android.provider.Settings.Secure.ANDROID_ID)
            val record = CensusRecord(tenant, device, UUID.randomUUID().toString(), consent, "consent",
                location.latitude, location.longitude, location.accuracy, location.time / 1000, mapOf("business_name" to "Field census"))
            EncryptedOutbox(context).enqueue(record.eventId, record.payload()); record.eventId
        }.onSuccess { done("Saved local-only: $it") }.onFailure { done("Capture rejected: ${it.message}") }
    }
}
