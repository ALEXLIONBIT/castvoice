package app.castvoice

import android.content.Context
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.io.File
import java.util.Locale
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

/**
 * Una voce del motore di sintesi vocale del telefono (di solito "Sintesi vocale Google").
 * [online] = quella voce ha bisogno di internet (di solito è la più naturale).
 */
data class Voice(
    val id: String,        // "tts:<nome voce Android>"
    val title: String,
    val name: String,
    val lang: String,      // "it", "en"…
    val locale: String,    // es. "it-IT"
    val online: Boolean,
    val note: String,      // es. "alta qualità"
)

val LANG_NAMES = linkedMapOf(
    "it" to "Italiano", "en" to "English", "es" to "Español",
    "fr" to "Français", "de" to "Deutsch", "pt" to "Português",
)

fun langName(lang: String): String =
    LANG_NAMES[lang] ?: Locale(lang).getDisplayLanguage(Locale.ITALIAN).replaceFirstChar { it.uppercase() }.ifBlank { lang.uppercase() }

/** Frase dell'anteprima, nella lingua della voce. */
val SAMPLE_PHRASES = mapOf(
    "it" to "Ciao, questa è la mia voce. Ti piace?",
    "en" to "Hello, this is my voice. Do you like it?",
    "es" to "Hola, esta es mi voz. ¿Te gusta?",
    "fr" to "Bonjour, voici ma voix. Elle te plaît ?",
    "de" to "Hallo, das ist meine Stimme. Gefällt sie dir?",
    "pt" to "Olá, esta é a minha voz. Gostas?",
)

class LocalTts(context: Context) {
    private val ready = CompletableFuture<Boolean>()
    private val engine: TextToSpeech = TextToSpeech(context.applicationContext) { ready.complete(it == TextToSpeech.SUCCESS) }

    private fun isReady() = runCatching { ready.get(8, TimeUnit.SECONDS) }.getOrDefault(false)

    fun voices(): List<Voice> {
        if (!isReady()) return emptyList()
        val all = runCatching { engine.voices }.getOrNull() ?: return emptyList()
        return all
            .map { v ->
                val variant = v.name.substringAfter("-x-", "").substringBefore("-").ifEmpty { v.name }
                Voice(
                    id = "tts:${v.name}", title = "Voce $variant", name = v.name,
                    lang = v.locale.language, locale = v.locale.toLanguageTag(),
                    online = v.isNetworkConnectionRequired,
                    note = if (v.quality >= android.speech.tts.Voice.QUALITY_HIGH) "alta qualità" else "",
                )
            }
            .sortedWith(compareBy({ it.lang != "it" }, { it.lang }, { it.locale }, { !it.online }, { it.name }))
    }

    @Synchronized
    fun synthesize(text: String, voiceName: String, ratePercent: Int, out: File) {
        check(isReady()) { "Il motore vocale del telefono non è disponibile." }
        val voice = engine.voices?.firstOrNull { it.name == voiceName } ?: error("Voce non trovata sul telefono.")
        engine.setVoice(voice)
        engine.setSpeechRate((1f + ratePercent / 100f).coerceIn(0.3f, 2f))
        val id = UUID.randomUUID().toString()
        val done = CompletableFuture<Boolean>()
        engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {}
            override fun onDone(utteranceId: String?) { if (utteranceId == id) done.complete(true) }
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) { if (utteranceId == id) done.complete(false) }
            override fun onError(utteranceId: String?, errorCode: Int) { if (utteranceId == id) done.complete(false) }
        })
        check(engine.synthesizeToFile(text, Bundle(), out, id) == TextToSpeech.SUCCESS) { "Sintesi non avviata." }
        val ok = try {
            done.get(45, TimeUnit.SECONDS)
        } catch (e: java.util.concurrent.TimeoutException) {
            engine.stop()
            error(
                "La voce non risponde. Se è la prima volta, apri le impostazioni «Sintesi vocale» del telefono " +
                    "e scarica i dati della voce italiana (oppure prova una voce 🌐 online).",
            )
        }
        check(ok) { "Sintesi non riuscita (se la voce è online serve internet: prova una voce offline 💻)." }
    }

    fun shutdown() = runCatching { engine.shutdown() }
}

object Tts {
    /** Genera l'audio del testo (WAV). Ritorna il file e il suo tipo (mime). */
    fun synthesize(local: LocalTts, voiceId: String, text: String, rate: Int, dir: File): Pair<File, String> {
        dir.mkdirs()
        check(voiceId.startsWith("tts:")) { "Scegli una voce." }
        val f = File(dir, UUID.randomUUID().toString().replace("-", "") + ".wav")
        try {
            local.synthesize(text, voiceId.removePrefix("tts:"), rate, f)
        } catch (e: Exception) {
            f.delete()
            throw e
        }
        return f to "audio/wav"
    }
}
