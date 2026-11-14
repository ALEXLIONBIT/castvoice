package app.castvoice

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import androidx.mediarouter.media.MediaRouteSelector
import androidx.mediarouter.media.MediaRouter
import com.google.android.gms.cast.CastMediaControlIntent
import com.google.android.gms.cast.MediaInfo
import com.google.android.gms.cast.MediaLoadRequestData
import com.google.android.gms.cast.MediaMetadata
import com.google.android.gms.cast.framework.CastContext
import com.google.android.gms.cast.framework.CastOptions
import com.google.android.gms.cast.framework.CastSession
import com.google.android.gms.cast.framework.OptionsProvider
import com.google.android.gms.cast.framework.SessionManagerListener
import com.google.android.gms.cast.framework.SessionProvider

/** Usa il "Default Media Receiver" di Google: riproduce un URL audio, senza app da registrare. */
class CastOptionsProvider : OptionsProvider {
    override fun getCastOptions(context: Context): CastOptions =
        CastOptions.Builder()
            .setReceiverApplicationId(CastMediaControlIntent.DEFAULT_MEDIA_RECEIVER_APPLICATION_ID)
            .setStopReceiverApplicationWhenEndingSession(true)
            .build()

    override fun getAdditionalSessionProviders(context: Context): List<SessionProvider>? = null
}

data class CastDevice(val id: String, val name: String, val model: String)

/** Trova i Google Nest / Chromecast e ci riproduce un URL. Un dispositivo alla volta. */
class CastController(private val app: Context) {
    private val router = MediaRouter.getInstance(app)
    private val selector = MediaRouteSelector.Builder()
        .addControlCategory(CastMediaControlIntent.categoryForCast(CastMediaControlIntent.DEFAULT_MEDIA_RECEIVER_APPLICATION_ID))
        .build()
    private var castContext: CastContext? = null
    private var pending: (() -> Unit)? = null
    private var scanning = false

    val devices = mutableStateListOf<CastDevice>()
    var selectedId by mutableStateOf<String?>(null)
        private set
    var connected by mutableStateOf(false)
        private set
    var status by mutableStateOf("")
    var volume by mutableStateOf(0.5f)

    private val routeCallback = object : MediaRouter.Callback() {
        override fun onRouteAdded(router: MediaRouter, route: MediaRouter.RouteInfo) = refresh()
        override fun onRouteRemoved(router: MediaRouter, route: MediaRouter.RouteInfo) = refresh()
        override fun onRouteChanged(router: MediaRouter, route: MediaRouter.RouteInfo) = refresh()
        override fun onRouteSelected(router: MediaRouter, route: MediaRouter.RouteInfo, reason: Int) = refresh()
        override fun onRouteUnselected(router: MediaRouter, route: MediaRouter.RouteInfo, reason: Int) {
            connected = false
            refresh()
        }
    }

    private val sessionListener = object : SessionManagerListener<CastSession> {
        override fun onSessionStarted(session: CastSession, sessionId: String) = ready(session)
        override fun onSessionResumed(session: CastSession, wasSuspended: Boolean) = ready(session)
        override fun onSessionStarting(session: CastSession) { status = "Mi collego…" }
        override fun onSessionResuming(session: CastSession, sessionId: String) { status = "Mi ricollego…" }
        override fun onSessionStartFailed(session: CastSession, error: Int) = failed(error)
        override fun onSessionResumeFailed(session: CastSession, error: Int) = failed(error)
        override fun onSessionEnding(session: CastSession) {}
        override fun onSessionEnded(session: CastSession, error: Int) { connected = false }
        override fun onSessionSuspended(session: CastSession, reason: Int) { connected = false }
    }

    private fun failed(error: Int) {
        connected = false
        pending = null
        status = "Collegamento non riuscito (codice $error). Riprova."
    }

    private fun ready(session: CastSession) {
        connected = true
        status = "Collegato a ${session.castDevice?.friendlyName ?: "dispositivo"}"
        runCatching { volume = session.volume.toFloat() }
        pending?.let { pending = null; it() }
    }

    private fun currentSession(): CastSession? =
        castContext?.sessionManager?.currentCastSession?.takeIf { it.isConnected }

    /** Va chiamato dal thread principale. */
    fun start() {
        if (castContext == null) {
            CastContext.getSharedInstance(app, ContextCompat.getMainExecutor(app))
                .addOnSuccessListener { ctx ->
                    castContext = ctx
                    ctx.sessionManager.addSessionManagerListener(sessionListener, CastSession::class.java)
                    currentSession()?.let { ready(it) }
                }
                .addOnFailureListener {
                    status = "Servono i Google Play Services per usare i Google Cast."
                }
        }
        rescan()
    }

    fun rescan() {
        if (scanning) router.removeCallback(routeCallback)
        router.addCallback(selector, routeCallback, MediaRouter.CALLBACK_FLAG_PERFORM_ACTIVE_SCAN)
        scanning = true
        refresh()
    }

    fun stopScan() {
        if (scanning) router.removeCallback(routeCallback)
        scanning = false
    }

    private fun refresh() {
        val found = router.routes
            .filter { !it.isDefaultOrBluetooth && it.matchesSelector(selector) }
            .map { CastDevice(it.id, it.name, it.description ?: "") }
            .sortedBy { it.name.lowercase() }
        devices.clear()
        devices.addAll(found)
        val sel = router.selectedRoute
        selectedId = if (sel.isDefaultOrBluetooth) null else sel.id
    }

    fun select(device: CastDevice) {
        val route = router.routes.firstOrNull { it.id == device.id } ?: return
        if (selectedId == device.id && connected) return
        connected = false
        status = "Mi collego a ${device.name}…"
        router.selectRoute(route)
    }

    fun disconnect() {
        pending = null
        castContext?.sessionManager?.endCurrentSession(true)
        router.unselect(MediaRouter.UNSELECT_REASON_DISCONNECTED)
        selectedId = null
        connected = false
        status = "Scollegato"
    }

    fun changeVolume(v: Float) {
        volume = v.coerceIn(0f, 1f)
        runCatching { currentSession()?.setVolume(volume.toDouble()) }
    }

    /** Riproduce [url] (servito dal telefono) sul dispositivo selezionato. */
    fun play(url: String, mime: String, title: String) {
        if (selectedId == null) {
            status = "Scegli prima un dispositivo."
            return
        }
        val action = {
            val client = currentSession()?.remoteMediaClient
            if (client == null) {
                status = "Il dispositivo non è pronto."
            } else {
                val meta = MediaMetadata(MediaMetadata.MEDIA_TYPE_MUSIC_TRACK)
                meta.putString(MediaMetadata.KEY_TITLE, title)
                val info = MediaInfo.Builder(url)
                    .setStreamType(MediaInfo.STREAM_TYPE_BUFFERED)
                    .setContentType(mime)
                    .setMetadata(meta)
                    .build()
                client.load(MediaLoadRequestData.Builder().setMediaInfo(info).setAutoplay(true).build())
                status = "In riproduzione: $title"
            }
        }
        if (connected && currentSession() != null) action() else {
            pending = action
            status = "Mi collego…"
        }
    }

    fun stopPlayback() {
        runCatching { currentSession()?.remoteMediaClient?.stop() }
        status = "Fermato"
    }
}
