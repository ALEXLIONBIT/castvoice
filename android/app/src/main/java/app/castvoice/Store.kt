package app.castvoice

import android.content.Context
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.net.Uri
import android.os.Build
import androidx.compose.runtime.mutableStateListOf
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.ByteBuffer
import java.time.LocalDateTime
import java.time.temporal.ChronoUnit
import java.util.UUID

/** Frase rapida salvata: testo + voce con cui dirla. */
data class Phrase(val text: String, val voice: String)

/** Impostazioni (voce scelta, velocità, preferite, frasi rapide). Tutto resta sul telefono. */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("castvoice", Context.MODE_PRIVATE)

    var voice: String
        get() = sp.getString("voice", "") ?: ""
        set(v) { sp.edit().putString("voice", v).apply() }

    var rate: Int
        get() = sp.getInt("rate", 0)
        set(v) { sp.edit().putInt("rate", v).apply() }

    var favorites: Set<String>
        get() = sp.getStringSet("favorites", emptySet())?.toSet() ?: emptySet()
        set(v) { sp.edit().putStringSet("favorites", v).apply() }

    var phrases: List<Phrase>
        get() = runCatching {
            val a = JSONArray(sp.getString("phrases", "[]"))
            (0 until a.length()).map { a.getJSONObject(it).let { o -> Phrase(o.getString("text"), o.getString("voice")) } }
        }.getOrDefault(emptyList())
        set(v) {
            val a = JSONArray()
            v.forEach { a.put(JSONObject().put("text", it.text).put("voice", it.voice)) }
            sp.edit().putString("phrases", a.toString()).apply()
        }
}

/** Un audio nella libreria: "file" = scelto dall'utente (resta dov'è), "clip" = registrato qui (privato). */
data class Item(val id: String, val name: String, val uri: String, val kind: String, val mime: String, val added: String)

class Library(private val context: Context) {
    private val file = File(context.filesDir, "library.json")
    val items = mutableStateListOf<Item>()

    init {
        runCatching {
            val arr = JSONObject(file.readText()).getJSONArray("items")
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                items += Item(o.getString("id"), o.getString("name"), o.getString("uri"), o.getString("kind"), o.optString("mime"), o.optString("added"))
            }
        }
    }

    private fun save() {
        val arr = JSONArray()
        items.forEach {
            arr.put(JSONObject().put("id", it.id).put("name", it.name).put("uri", it.uri)
                .put("kind", it.kind).put("mime", it.mime).put("added", it.added))
        }
        file.writeText(JSONObject().put("items", arr).toString())
    }

    fun add(name: String, uri: String, kind: String, mime: String): Item {
        val item = Item(UUID.randomUUID().toString().take(8), name, uri, kind, mime, LocalDateTime.now().truncatedTo(ChronoUnit.SECONDS).toString())
        items += item
        save()
        return item
    }

    fun remove(item: Item) {
        if (item.kind == "clip") File(item.uri).delete()   // le clip sono nostre: si cancella anche il file
        items.removeAll { it.id == item.id }
        save()
    }

    fun clipsDir() = File(context.filesDir, "clips").also { it.mkdirs() }
}

// --------------------------------------------------------------------------------
// Preparazione dell'audio da mandare ai Google
// --------------------------------------------------------------------------------
class Prepared(val source: Source, val ext: String, val mime: String)

object AudioPrep {
    private fun normalize(mime: String) = when (mime) {
        "audio/x-wav", "audio/wave", "audio/vnd.wave" -> "audio/wav"
        "audio/x-m4a", "audio/m4a" -> "audio/mp4"
        "audio/mp3" -> "audio/mpeg"
        "audio/x-flac" -> "audio/flac"
        "application/ogg" -> "audio/ogg"
        else -> mime
    }

    private fun extFor(mime: String) = when (mime) {
        "audio/mpeg" -> "mp3"
        "audio/mp4" -> "m4a"
        "audio/wav" -> "wav"
        "audio/ogg" -> "ogg"
        "audio/flac" -> "flac"
        "audio/aac" -> "aac"
        "audio/webm", "video/webm" -> "webm"
        "video/mp4" -> "mp4"
        else -> "bin"
    }

    fun mimeOf(context: Context, uri: Uri, displayName: String): String {
        val fromProvider = context.contentResolver.getType(uri)
        if (!fromProvider.isNullOrEmpty() && fromProvider != "application/octet-stream") return fromProvider
        val ext = displayName.substringAfterLast('.', "").lowercase()
        return android.webkit.MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext) ?: "audio/mpeg"
    }

    fun prepare(context: Context, item: Item, onStatus: (String) -> Unit): Prepared {
        if (item.kind == "clip") return Prepared(fileSource(File(item.uri), "audio/mp4"), "m4a", "audio/mp4")
        val uri = Uri.parse(item.uri)
        if (item.mime.startsWith("video/")) {
            onStatus("Estraggo l'audio dal video…")
            extractAac(context, uri)?.let { return Prepared(fileSource(it, "audio/mp4"), "m4a", "audio/mp4") }
        }
        val mime = normalize(item.mime)
        val src = uriSource(context, uri, mime) ?: error("Non riesco a leggere il file (è stato spostato o cancellato?).")
        return Prepared(src, extFor(mime), mime)
    }

    /** Copia la sola traccia audio (se è AAC, come nei normali mp4/mov) in un .m4a. Altrimenti null. */
    private fun extractAac(context: Context, uri: Uri): File? {
        val ex = MediaExtractor()
        return try {
            ex.setDataSource(context, uri, null)
            val idx = (0 until ex.trackCount).firstOrNull {
                ex.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true
            } ?: return null
            val fmt = ex.getTrackFormat(idx)
            if (fmt.getString(MediaFormat.KEY_MIME) != "audio/mp4a-latm") return null
            ex.selectTrack(idx)
            val dir = File(context.cacheDir, "audio").also { it.mkdirs() }
            val out = File(dir, UUID.randomUUID().toString().replace("-", "") + ".m4a")
            val mux = MediaMuxer(out.path, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
            val track = mux.addTrack(fmt)
            mux.start()
            val buf = ByteBuffer.allocate(1 shl 20)
            val info = MediaCodec.BufferInfo()
            while (true) {
                val n = ex.readSampleData(buf, 0)
                if (n < 0) break
                val flags = if (ex.sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC != 0) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0
                info.set(0, n, ex.sampleTime, flags)
                mux.writeSampleData(track, buf, info)
                ex.advance()
            }
            mux.stop()
            mux.release()
            out
        } catch (_: Exception) {
            null
        } finally {
            ex.release()
        }
    }
}

// --------------------------------------------------------------------------------
// Riproduzione sul telefono (anteprime) e registratore
// --------------------------------------------------------------------------------
class LocalPlayer {
    private var mp: MediaPlayer? = null

    fun play(file: File, onDone: () -> Unit = {}) {
        stop()
        val p = MediaPlayer()
        mp = p
        p.setDataSource(file.path)
        p.setOnCompletionListener { onDone(); if (mp === p) { p.release(); mp = null } }
        p.setOnErrorListener { _, _, _ -> onDone(); true }
        p.prepare()
        p.start()
    }

    fun stop() {
        mp?.let { runCatching { it.stop() }; it.release() }
        mp = null
    }
}

class Recorder(private val context: Context) {
    private var rec: MediaRecorder? = null
    private var out: File? = null

    fun start(file: File) {
        val r = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(context) else @Suppress("DEPRECATION") MediaRecorder()
        r.setAudioSource(MediaRecorder.AudioSource.MIC)
        r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
        r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
        r.setAudioSamplingRate(44100)
        r.setAudioEncodingBitRate(128_000)
        r.setOutputFile(file.path)
        r.prepare()
        r.start()
        rec = r
        out = file
    }

    /** Ferma e ritorna il file registrato (o null se vuoto/errore). */
    fun stop(): File? {
        val r = rec ?: return null
        rec = null
        val ok = runCatching { r.stop() }.isSuccess
        r.release()
        val f = out
        out = null
        return if (ok && f != null && f.exists() && f.length() > 2000) f else { f?.delete(); null }
    }
}
