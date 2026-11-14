package app.castvoice

import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.net.HttpURLConnection
import java.net.URL

/** Il mini server HTTP che i Google Cast interrogano deve rispettare le richieste "Range". */
class ProtocolTest {
    @Test
    fun localServerHonoursRanges() {
        val data = ByteArray(1000) { (it % 251).toByte() }
        val server = LocalServer()
        val src = Source("audio/mpeg", data.size.toLong()) { off -> ByteArrayInputStream(data, off.toInt(), data.size - off.toInt()) }
        val url = server.publish(src, "mp3")
        assumeTrue("nessuna rete locale", url != null)
        val local = url!!.replace(Regex("//[^:]+:"), "//127.0.0.1:")
        try {
            val full = URL(local).openConnection() as HttpURLConnection
            assertEquals(200, full.responseCode)
            assertEquals("audio/mpeg", full.contentType)
            assertEquals(1000, full.inputStream.readBytes().size)

            val part = URL(local).openConnection() as HttpURLConnection
            part.setRequestProperty("Range", "bytes=100-199")
            assertEquals(206, part.responseCode)
            assertEquals("bytes 100-199/1000", part.getHeaderField("Content-Range"))
            assertEquals(data.slice(100..199), part.inputStream.readBytes().toList())

            val bad = URL(local.replace(Regex("/[0-9a-f]+\\.mp3"), "/nope.mp3")).openConnection() as HttpURLConnection
            assertEquals(404, bad.responseCode)
        } finally {
            server.stop()
        }
    }
}
