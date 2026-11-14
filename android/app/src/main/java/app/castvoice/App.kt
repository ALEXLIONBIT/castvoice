package app.castvoice

import android.app.Application
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.UUID

private const val NO_WIFI = "Non trovo la rete Wi-Fi: collega il telefono alla stessa rete dei Google."

class AppViewModel(app: Application) : AndroidViewModel(app) {
    private val ctx: Context = app
    val prefs = Prefs(ctx)
    val library = Library(ctx)
    val cast = CastController(ctx)
    private val server = LocalServer()
    private val localTts = LocalTts(ctx)
    private val player = LocalPlayer()
    private val recorder = Recorder(ctx)
    private val audioDir = File(ctx.cacheDir, "audio")

    var voices by mutableStateOf<List<Voice>>(emptyList()); private set
    var voicesLoading by mutableStateOf(true); private set
    var voiceId by mutableStateOf(prefs.voice); private set
    var rate by mutableIntStateOf(prefs.rate); private set
    var favorites by mutableStateOf(prefs.favorites); private set
    val phrases = mutableStateListOf<Phrase>().apply { addAll(prefs.phrases) }
    var busy by mutableStateOf(false); private set
    var previewingId by mutableStateOf<String?>(null); private set
    var recording by mutableStateOf(false); private set
    var recordSeconds by mutableIntStateOf(0); private set
    var pendingClip by mutableStateOf<File?>(null); private set

    init {
        viewModelScope.launch(Dispatchers.IO) {
            // pulizia: gli audio temporanei di sessioni precedenti non servono più
            val limit = System.currentTimeMillis() - 6 * 3600_000L
            audioDir.listFiles()?.filter { it.lastModified() < limit }?.forEach { it.delete() }
        }
        loadVoices()
    }

    fun say(msg: String) { cast.status = msg }

    private fun work(start: String, block: suspend () -> Unit) {
        viewModelScope.launch(Dispatchers.IO) {
            busy = true
            say(start)
            try {
                block()
            } catch (e: Exception) {
                say(e.message ?: e.javaClass.simpleName)
            } finally {
                busy = false
            }
        }
    }

    // ------------------------------------------------------------------ voci
    fun loadVoices() {
        viewModelScope.launch(Dispatchers.IO) {
            voicesLoading = true
            val found = localTts.voices()
            voices = found
            if (found.isEmpty()) {
                say("Nessuna voce trovata: installa o abilita «Sintesi vocale Google» nelle impostazioni del telefono.")
            } else if (found.none { it.id == voiceId }) {
                // prima volta (o voce sparita): parti da una voce italiana, meglio se online/alta qualità
                chooseVoice((found.filter { it.lang == "it" }.maxByOrNull { (if (it.note.isNotEmpty()) 2 else 0) + (if (it.online) 1 else 0) } ?: found.first()).id)
            }
            voicesLoading = false
        }
    }

    fun chooseVoice(id: String) { voiceId = id; prefs.voice = id }
    fun changeRate(v: Int) { rate = v; prefs.rate = v }

    fun toggleFavorite(id: String) {
        favorites = if (id in favorites) favorites - id else favorites + id
        prefs.favorites = favorites
    }

    fun preview(v: Voice) {
        if (previewingId == v.id) { player.stop(); previewingId = null; return }
        player.stop()
        viewModelScope.launch(Dispatchers.IO) {
            previewingId = v.id
            try {
                val phrase = SAMPLE_PHRASES[v.lang] ?: SAMPLE_PHRASES.getValue("en")
                val (file, _) = Tts.synthesize(localTts, v.id, phrase, rate, audioDir)
                withContext(Dispatchers.Main) { player.play(file) { if (previewingId == v.id) previewingId = null } }
            } catch (e: Exception) {
                say(e.message ?: e.javaClass.simpleName)
                previewingId = null
            }
        }
    }

    // ------------------------------------------------------------------ parlare
    fun speak(text: String, voice: String = voiceId) {
        val t = text.trim()
        if (t.isEmpty()) { say("Scrivi qualcosa da dire."); return }
        if (cast.selectedId == null) { say("Scegli prima un dispositivo."); return }
        work("Preparo la voce…") {
            val (file, mime) = Tts.synthesize(localTts, voice, t, rate, audioDir)
            val url = server.publish(fileSource(file, mime), "wav") ?: error(NO_WIFI)
            withContext(Dispatchers.Main) { cast.play(url, mime, t.take(40)) }
        }
    }

    fun savePhrase(text: String) {
        val t = text.trim()
        if (t.isEmpty() || phrases.any { it.text == t && it.voice == voiceId }) return
        phrases += Phrase(t, voiceId)
        prefs.phrases = phrases.toList()
    }

    fun deletePhrase(p: Phrase) { phrases.remove(p); prefs.phrases = phrases.toList() }

    // ------------------------------------------------------------------ audio e video
    fun addFiles(uris: List<Uri>) {
        viewModelScope.launch(Dispatchers.IO) {
            for (u in uris) {
                runCatching { ctx.contentResolver.takePersistableUriPermission(u, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
                val display = ctx.contentResolver.query(u, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
                    if (it.moveToFirst()) it.getString(0) else null
                } ?: "audio"
                library.add(display.substringBeforeLast('.'), u.toString(), "file", AudioPrep.mimeOf(ctx, u, display))
            }
        }
    }

    fun playItem(item: Item) {
        if (cast.selectedId == null) { say("Scegli prima un dispositivo."); return }
        work("Preparo l'audio…") {
            val p = AudioPrep.prepare(ctx, item, ::say)
            val url = server.publish(p.source, p.ext) ?: error(NO_WIFI)
            withContext(Dispatchers.Main) { cast.play(url, p.mime, item.name) }
        }
    }

    /** Riascolta una clip qui sul telefono. */
    fun playClipHere(item: Item) {
        player.stop()
        runCatching { player.play(File(item.uri)) }.onFailure { say("Non riesco a riprodurla.") }
    }

    fun remove(item: Item) = library.remove(item)

    fun stopAll() {
        cast.stopPlayback()
        player.stop()
        previewingId = null
    }

    // ------------------------------------------------------------------ registrazione
    fun startRecording() {
        if (recording) return
        runCatching { recorder.start(File(library.clipsDir(), "tmp-${UUID.randomUUID()}.m4a")) }
            .onFailure { say("Non riesco ad avviare il microfono."); return }
        recording = true
        recordSeconds = 0
        viewModelScope.launch {
            while (recording) { delay(1000); if (recording) recordSeconds++ }
        }
    }

    fun stopRecording() {
        if (!recording) return
        recording = false
        val f = recorder.stop()
        if (f == null) say("Registrazione troppo corta o vuota.") else pendingClip = f
    }

    fun saveClip(name: String) {
        val tmp = pendingClip ?: return
        pendingClip = null
        val dest = File(library.clipsDir(), "${UUID.randomUUID().toString().take(8)}.m4a")
        if (tmp.renameTo(dest)) library.add(name.ifBlank { "Registrazione" }, dest.path, "clip", "audio/mp4")
    }

    fun discardClip() { pendingClip?.delete(); pendingClip = null }

    override fun onCleared() {
        recording = false
        recorder.stop()
        player.stop()
        cast.stopScan()
        server.stop()
        localTts.shutdown()
    }
}
