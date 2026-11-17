package app.castvoice

import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Unisce più file WAV (PCM 16 bit, anche con frequenze diverse) in un solo WAV mono,
 * con una piccola pausa tra un pezzo e l'altro. Serve per far parlare più voci nello stesso audio.
 */
object WavJoin {
    private class Pcm(val rate: Int, val samples: ShortArray)

    private fun read(f: File): Pcm {
        val b = f.readBytes()
        require(b.size > 44 && String(b, 0, 4) == "RIFF" && String(b, 8, 4) == "WAVE") { "Audio della voce non valido." }
        val bb = ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN)
        var pos = 12
        var rate = 0
        var channels = 1
        var bits = 16
        while (pos + 8 <= b.size) {
            val id = String(b, pos, 4)
            var size = bb.getInt(pos + 4)
            val body = pos + 8
            if (id == "fmt ") {
                channels = bb.getShort(body + 2).toInt()
                rate = bb.getInt(body + 4)
                bits = bb.getShort(body + 14).toInt()
            } else if (id == "data") {
                // alcuni motori lasciano la dimensione a 0 o -1: in quel caso i dati arrivano fino alla fine
                if (size <= 0 || body + size > b.size) size = b.size - body
                require(bits == 16 && rate > 0 && channels > 0) { "Formato audio non supportato ($bits bit)." }
                val n = size / 2 / channels
                val out = ShortArray(n)
                for (i in 0 until n) {
                    var s = 0
                    for (c in 0 until channels) s += bb.getShort(body + (i * channels + c) * 2).toInt()
                    out[i] = (s / channels).toShort()
                }
                return Pcm(rate, out)
            }
            pos = body + size + (size and 1)
        }
        error("Audio della voce senza dati.")
    }

    private fun resample(s: ShortArray, from: Int, to: Int): ShortArray {
        if (from == to || s.isEmpty()) return s
        val n = (s.size.toLong() * to / from).toInt()
        val out = ShortArray(n)
        for (i in 0 until n) {
            val x = i.toDouble() * from / to
            val i0 = x.toInt().coerceAtMost(s.size - 1)
            val i1 = (i0 + 1).coerceAtMost(s.size - 1)
            val f = x - i0
            out[i] = (s[i0] * (1 - f) + s[i1] * f).toInt().toShort()
        }
        return out
    }

    fun join(files: List<File>, out: File, gapMs: Int = 180) {
        require(files.isNotEmpty()) { "Niente da unire." }
        val parts = files.map(::read)
        val rate = parts.first().rate
        val gap = ShortArray(rate * gapMs / 1000)
        val all = ArrayList<ShortArray>()
        parts.forEachIndexed { i, p ->
            all += resample(p.samples, p.rate, rate)
            if (i < parts.size - 1) all += gap
        }
        val total = all.sumOf { it.size }
        val buf = ByteBuffer.allocate(44 + total * 2).order(ByteOrder.LITTLE_ENDIAN)
        buf.put("RIFF".toByteArray()).putInt(36 + total * 2).put("WAVE".toByteArray())
        buf.put("fmt ".toByteArray()).putInt(16).putShort(1).putShort(1).putInt(rate).putInt(rate * 2).putShort(2).putShort(16)
        buf.put("data".toByteArray()).putInt(total * 2)
        all.forEach { a -> a.forEach { buf.putShort(it) } }
        out.writeBytes(buf.array())
    }
}
