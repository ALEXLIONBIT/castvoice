package app.castvoice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.PI
import kotlin.math.sin

/** Unire più voci in un solo audio: durata, frequenza e intestazione devono essere giuste. */
class WavJoinTest {
    private fun wav(file: File, rate: Int, seconds: Double, hz: Double, channels: Int = 1, badSize: Boolean = false) {
        val n = (rate * seconds).toInt()
        val buf = ByteBuffer.allocate(44 + n * 2 * channels).order(ByteOrder.LITTLE_ENDIAN)
        val dataBytes = n * 2 * channels
        buf.put("RIFF".toByteArray()).putInt(36 + dataBytes).put("WAVE".toByteArray())
        buf.put("fmt ".toByteArray()).putInt(16).putShort(1).putShort(channels.toShort()).putInt(rate)
            .putInt(rate * 2 * channels).putShort((2 * channels).toShort()).putShort(16)
        buf.put("data".toByteArray()).putInt(if (badSize) 0 else dataBytes) // alcuni motori lasciano 0
        for (i in 0 until n) repeat(channels) { buf.putShort((sin(2 * PI * hz * i / rate) * 8000).toInt().toShort()) }
        file.writeBytes(buf.array())
    }

    private fun duration(f: File): Double {
        val bb = ByteBuffer.wrap(f.readBytes()).order(ByteOrder.LITTLE_ENDIAN)
        assertEquals("RIFF", String(bb.array(), 0, 4))
        val rate = bb.getInt(24)
        assertEquals(1, bb.getShort(22).toInt())
        assertEquals(bb.array().size - 44, bb.getInt(40)) // la dimensione dichiarata è quella vera
        return (bb.array().size - 44) / 2.0 / rate
    }

    @Test
    fun joinsDifferentRatesWithGap() {
        val dir = createTempDir()
        val a = File(dir, "a.wav").also { wav(it, 24000, 1.0, 440.0) }
        val b = File(dir, "b.wav").also { wav(it, 16000, 2.0, 880.0) }
        val c = File(dir, "c.wav").also { wav(it, 22050, 0.5, 300.0, badSize = true) } // dimensione a 0
        val out = File(dir, "out.wav")
        WavJoin.join(listOf(a, b, c), out, gapMs = 200)
        // 1,0 + 2,0 + 0,5 secondi + 2 pause da 0,2 s = 3,9 s (tolleranza per l'arrotondamento)
        assertEquals(3.9, duration(out), 0.01)
        assertTrue(out.length() > 44)
    }

    @Test
    fun stereoIsMixedToMono() {
        val dir = createTempDir()
        val a = File(dir, "a.wav").also { wav(it, 24000, 1.0, 440.0, channels = 2) }
        val out = File(dir, "out.wav")
        WavJoin.join(listOf(a), out)
        assertEquals(1.0, duration(out), 0.01)
    }
}
