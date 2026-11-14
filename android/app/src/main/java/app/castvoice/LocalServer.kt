package app.castvoice

import android.content.Context
import android.net.Uri
import java.io.File
import java.io.FileInputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.Inet4Address
import java.net.NetworkInterface
import java.net.ServerSocket
import java.net.Socket
import java.security.SecureRandom
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors

/** Qualcosa che il telefono può dare ai Google: un file o un contenuto scelto dall'utente. */
class Source(
    val mime: String,
    val length: Long,
    val open: (offset: Long) -> InputStream,
)

fun fileSource(file: File, mime: String) = Source(mime, file.length()) { off ->
    FileInputStream(file).also { it.channel.position(off) }
}

fun uriSource(ctx: Context, uri: Uri, mime: String): Source? {
    val len = ctx.contentResolver.openFileDescriptor(uri, "r")?.use { it.statSize } ?: return null
    return Source(mime, len) { off ->
        val pfd = ctx.contentResolver.openFileDescriptor(uri, "r") ?: error("file non leggibile")
        FileInputStream(pfd.fileDescriptor).also { it.channel.position(off) }
    }
}

/**
 * Mini server HTTP (con supporto "Range") che i Google Cast interrogano per scaricare l'audio.
 * Gli indirizzi sono casuali e validi solo finché l'app è aperta.
 */
class LocalServer {
    private val sources = ConcurrentHashMap<String, Source>()
    private val pool = Executors.newCachedThreadPool()
    private val rnd = SecureRandom()
    private var socket: ServerSocket? = null

    @Synchronized
    fun start() {
        if (socket != null) return
        val s = ServerSocket(0)
        socket = s
        pool.execute {
            while (!s.isClosed) {
                val c = try { s.accept() } catch (_: Exception) { break }
                pool.execute { runCatching { handle(c) }; runCatching { c.close() } }
            }
        }
    }

    fun stop() {
        socket?.close()
        socket = null
        sources.clear()
    }

    /** Pubblica la sorgente e ritorna l'URL da passare al Google. */
    fun publish(source: Source, ext: String): String? {
        start()
        val ip = lanAddress() ?: return null
        val token = ByteArray(12).also { rnd.nextBytes(it) }.joinToString("") { "%02x".format(it) }
        sources[token] = source
        return "http://$ip:${socket!!.localPort}/$token.$ext"
    }

    private fun handle(c: Socket) {
        c.soTimeout = 30_000
        val input = c.getInputStream().bufferedReader(Charsets.ISO_8859_1)
        val request = input.readLine() ?: return
        val parts = request.split(" ")
        if (parts.size < 2) return
        val headers = HashMap<String, String>()
        while (true) {
            val line = input.readLine() ?: break
            if (line.isEmpty()) break
            val i = line.indexOf(':')
            if (i > 0) headers[line.substring(0, i).trim().lowercase()] = line.substring(i + 1).trim()
        }
        val out = c.getOutputStream()
        val token = parts[1].removePrefix("/").substringBefore('.').substringBefore('?')
        val src = sources[token]
        if (src == null || (parts[0] != "GET" && parts[0] != "HEAD")) {
            out.write("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".toByteArray())
            out.flush()
            return
        }
        var start = 0L
        var end = src.length - 1
        var partial = false
        headers["range"]?.let { r ->
            val m = Regex("bytes=(\\d*)-(\\d*)").matchEntire(r)
            if (m != null) {
                val a = m.groupValues[1]
                val b = m.groupValues[2]
                if (a.isNotEmpty()) {
                    start = a.toLong()
                    if (b.isNotEmpty()) end = minOf(b.toLong(), end)
                } else if (b.isNotEmpty()) {
                    start = maxOf(0L, src.length - b.toLong())
                }
                partial = true
            }
        }
        if (start > end || start >= src.length) {
            out.write("HTTP/1.1 416 Range Not Satisfiable\r\nContent-Range: bytes */${src.length}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".toByteArray())
            out.flush()
            return
        }
        val count = end - start + 1
        val sb = StringBuilder()
        sb.append(if (partial) "HTTP/1.1 206 Partial Content\r\n" else "HTTP/1.1 200 OK\r\n")
        sb.append("Content-Type: ${src.mime}\r\n")
        sb.append("Accept-Ranges: bytes\r\n")
        sb.append("Access-Control-Allow-Origin: *\r\n")
        if (partial) sb.append("Content-Range: bytes $start-$end/${src.length}\r\n")
        sb.append("Content-Length: $count\r\nConnection: close\r\n\r\n")
        out.write(sb.toString().toByteArray())
        if (parts[0] == "GET") copy(src.open(start), out, count)
        out.flush()
    }

    private fun copy(input: InputStream, out: OutputStream, count: Long) {
        input.use {
            val buf = ByteArray(64 * 1024)
            var left = count
            while (left > 0) {
                val n = it.read(buf, 0, minOf(buf.size.toLong(), left).toInt())
                if (n < 0) break
                out.write(buf, 0, n)
                left -= n
            }
        }
    }

    /** Indirizzo del telefono sulla rete locale (Wi-Fi), quello che i Google riescono a raggiungere. */
    private fun lanAddress(): String? {
        val candidates = NetworkInterface.getNetworkInterfaces().toList()
            .filter { it.isUp && !it.isLoopback }
            .flatMap { ni -> ni.inetAddresses.toList().filterIsInstance<Inet4Address>().map { ni.name to it } }
            .filter { it.second.isSiteLocalAddress }
        return (candidates.firstOrNull { it.first.startsWith("wlan") } ?: candidates.firstOrNull())?.second?.hostAddress
    }
}
