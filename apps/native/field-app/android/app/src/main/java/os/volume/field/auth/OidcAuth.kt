package os.volume.field.auth

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import net.openid.appauth.AuthorizationRequest
import net.openid.appauth.AuthorizationService
import net.openid.appauth.AuthorizationServiceConfiguration
import net.openid.appauth.CodeVerifierUtil
import net.openid.appauth.ResponseTypeValues
import net.openid.appauth.TokenRequest

data class OidcConfig(val issuer: Uri, val authEndpoint: Uri, val tokenEndpoint: Uri, val clientId: String, val redirect: Uri, val audience: String)

/** System-browser Authorization Code + PKCE. No client secret is present or accepted. */
class OidcAuth(private val context: Context, private val config: OidcConfig) {
    private val service = AuthorizationService(context)
    fun signInIntent(): Intent {
        val request = AuthorizationRequest.Builder(
            AuthorizationServiceConfiguration(config.authEndpoint, config.tokenEndpoint),
            config.clientId, ResponseTypeValues.CODE, config.redirect,
        ).setScope("openid profile offline_access").setCodeVerifier(CodeVerifierUtil.generateRandomCodeVerifier())
            .setAdditionalParameters(mapOf("audience" to config.audience, "prompt" to "login")).build()
        return service.getAuthorizationRequestIntent(request)
    }

    suspend fun exchange(response: net.openid.appauth.AuthorizationResponse): TokenSet {
        val tokenRequest: TokenRequest = response.createTokenExchangeRequest() // includes the original PKCE verifier
        val result = kotlinx.coroutines.suspendCancellableCoroutine<TokenSet> { continuation ->
            service.performTokenRequest(tokenRequest) { token, error ->
                if (token == null || error != null || token.accessToken == null) continuation.resumeWith(Result.failure(IllegalStateException("OIDC exchange failed")))
                else continuation.resumeWith(Result.success(TokenSet(token.accessToken!!, token.refreshToken, token.idToken, token.accessTokenExpirationTime ?: 0L)))
            }
        }
        TokenStore(context).save(result)
        return result
    }
}

data class TokenSet(val accessToken: String, val refreshToken: String?, val idToken: String?, val expiresAt: Long)

class TokenStore(context: Context) {
    private val preferences = EncryptedSharedPreferences.create(
        context, "oidc", MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )
    fun save(tokens: TokenSet) = preferences.edit().putString("access", tokens.accessToken).putString("refresh", tokens.refreshToken)
        .putString("id", tokens.idToken).putLong("expires", tokens.expiresAt).apply()
    fun accessToken(): String? = preferences.getString("access", null)?.takeIf { preferences.getLong("expires", 0) > System.currentTimeMillis() }
    fun clear() = preferences.edit().clear().apply()
}
