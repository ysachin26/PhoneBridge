package com.phonebridge.server

import android.util.Base64
import android.util.Log
import fi.iki.elonen.NanoHTTPD
import java.io.*
import java.net.URLDecoder
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicInteger

/**
 * Real-time server statistics tracked across all requests.
 */
data class ServerStats(
    val bytesServed: Long = 0,
    val bytesReceived: Long = 0,
    val totalRequests: Long = 0,
    val activeConnections: Int = 0,
    val startedAt: Long = 0,
) {
    val uptimeSeconds: Long get() = if (startedAt > 0) (System.currentTimeMillis() - startedAt) / 1000 else 0
}

/**
 * WebDAV server built on NanoHTTPD.
 *
 * Serves the phone's storage via a subset of the WebDAV protocol,
 * supporting the methods needed by rclone: OPTIONS, PROPFIND, GET,
 * PUT, DELETE, MKCOL, MOVE, COPY.
 *
 * Includes HTTP Basic Auth to prevent unauthorized access on the network.
 * Tracks transfer stats for display in the UI.
 *
 * @param port The port to listen on (default: 8273)
 * @param rootDir The root directory to serve
 * @param authPassword The password required for Basic Auth (username is always "phonebridge")
 */
class WebDavServer(
    port: Int = ServerConfig.DEFAULT_PORT,
    private val rootDir: File,
    @Volatile private var authPassword: String
) : NanoHTTPD(port) {

    companion object {
        private const val TAG = "WebDavServer"
    }

    /** Update the password at runtime (e.g. when user regenerates it). Thread-safe via @Volatile. */
    fun updatePassword(newPassword: String) {
        authPassword = newPassword
        Log.i(TAG, "Auth password updated")
    }

    // ─── Stats Tracking ─────────────────────────────────────────
    private val _bytesServed = AtomicLong(0)
    private val _bytesReceived = AtomicLong(0)
    private val _totalRequests = AtomicLong(0)
    private val _activeConnections = AtomicInteger(0)
    private var _startedAt: Long = 0

    /** Get a snapshot of current server statistics. */
    fun getStats(): ServerStats = ServerStats(
        bytesServed = _bytesServed.get(),
        bytesReceived = _bytesReceived.get(),
        totalRequests = _totalRequests.get(),
        activeConnections = _activeConnections.get(),
        startedAt = _startedAt,
    )

    override fun start() {
        _startedAt = System.currentTimeMillis()
        super.start()
    }

    init {
        require(rootDir.exists() && rootDir.isDirectory) {
            "Root directory must exist and be a directory: ${rootDir.absolutePath}"
        }
    }

    override fun serve(session: IHTTPSession): Response {
        _totalRequests.incrementAndGet()
        _activeConnections.incrementAndGet()

        val method = session.method.name.uppercase()
        val uri = session.uri ?: "/"

        Log.d(TAG, "$method $uri")

        // ─── Authentication Check ────────────────────────────
        if (!isAuthenticated(session)) {
            _activeConnections.decrementAndGet()
            Log.w(TAG, "Unauthorized request: $method $uri")
            val response = newFixedLengthResponse(
                Response.Status.UNAUTHORIZED,
                "text/plain",
                "Authentication required"
            )
            response.addHeader("WWW-Authenticate", "Basic realm=\"PhoneBridge\"")
            return response
        }

        return try {
            val result = when (method) {
                "OPTIONS" -> handleOptions()
                "PROPFIND" -> handlePropfind(session)
                "GET" -> handleGet(session)
                "HEAD" -> handleHead(session)
                "PUT" -> handlePut(session)
                "DELETE" -> handleDelete(session)
                "MKCOL" -> handleMkcol(session)
                "MOVE" -> handleMove(session)
                "COPY" -> handleCopy(session)
                else -> newFixedLengthResponse(
                    Response.Status.METHOD_NOT_ALLOWED,
                    "text/plain",
                    "Method $method not supported"
                )
            }
            result
        } catch (e: Exception) {
            Log.e(TAG, "Error handling $method $uri", e)
            newFixedLengthResponse(
                Response.Status.INTERNAL_ERROR,
                "text/plain",
                "Internal server error: ${e.message}"
            )
        } finally {
            _activeConnections.decrementAndGet()
        }
    }

    /**
     * Validate HTTP Basic Auth credentials from the request.
     */
    private fun isAuthenticated(session: IHTTPSession): Boolean {
        val authHeader = session.headers["authorization"] ?: return false

        if (!authHeader.startsWith("Basic ", ignoreCase = true)) return false

        return try {
            val encoded = authHeader.substring(6)
            val decoded = String(Base64.decode(encoded, Base64.NO_WRAP))
            val parts = decoded.split(":", limit = 2)

            if (parts.size != 2) return false

            val username = parts[0]
            val password = parts[1]

            username == ServerConfig.AUTH_USERNAME && password == authPassword
        } catch (e: Exception) {
            Log.w(TAG, "Auth decode error: ${e.message}")
            false
        }
    }

    // ─── OPTIONS ─────────────────────────────────────────────────

    private fun handleOptions(): Response {
        val response = newFixedLengthResponse(Response.Status.OK, "text/plain", "")
        response.addHeader("Allow", "OPTIONS, GET, HEAD, PUT, DELETE, MKCOL, PROPFIND, MOVE, COPY")
        response.addHeader("DAV", "1, 2")
        response.addHeader("MS-Author-Via", "DAV")
        return response
    }

    // ─── PROPFIND ────────────────────────────────────────────────

    private fun handlePropfind(session: IHTTPSession): Response {
        val file = resolveFile(session.uri)

        if (!file.exists()) {
            return newFixedLengthResponse(Response.Status.NOT_FOUND, "text/plain", "Not Found")
        }

        val depth = session.headers["depth"] ?: "1"

        val xmlBody = XmlResponseBuilder.buildPropfindResponse(rootDir, file, depth)
        val bodyBytes = xmlBody.toByteArray(Charsets.UTF_8)

        val response = newFixedLengthResponse(
            Response.Status.lookup(207),
            "application/xml; charset=\"utf-8\"",
            ByteArrayInputStream(bodyBytes),
            bodyBytes.size.toLong()
        )
        response.addHeader("DAV", "1, 2")
        return response
    }

    // ─── GET ─────────────────────────────────────────────────────

    private fun handleGet(session: IHTTPSession): Response {
        val file = resolveFile(session.uri)

        if (!file.exists()) {
            return newFixedLengthResponse(Response.Status.NOT_FOUND, "text/plain", "Not Found")
        }

        if (file.isDirectory) {
            // Return HTML directory listing for browser access
            return serveDirectoryListing(file)
        }

        // Serve file with streaming + track bytes
        val fileSize = file.length()
        val mimeType = XmlResponseBuilder.guessMimeType(file)
        val fis = FileInputStream(file)
        val response = newFixedLengthResponse(
            Response.Status.OK, mimeType, fis, fileSize
        )
        response.addHeader("Accept-Ranges", "bytes")
        _bytesServed.addAndGet(fileSize)
        return response
    }

    // ─── HEAD ────────────────────────────────────────────────────

    private fun handleHead(session: IHTTPSession): Response {
        val file = resolveFile(session.uri)

        if (!file.exists()) {
            return newFixedLengthResponse(Response.Status.NOT_FOUND, "text/plain", "")
        }

        val mimeType = if (file.isDirectory) "httpd/unix-directory" else XmlResponseBuilder.guessMimeType(file)
        val response = newFixedLengthResponse(Response.Status.OK, mimeType, "")
        if (file.isFile) {
            response.addHeader("Content-Length", file.length().toString())
        }
        return response
    }

    // ─── PUT ─────────────────────────────────────────────────────

    private fun handlePut(session: IHTTPSession): Response {
        val file = resolveFile(session.uri)

        // Create parent directories if needed
        file.parentFile?.mkdirs()

        val contentLength = session.headers["content-length"]?.toLongOrNull() ?: 0L
        val isNew = !file.exists()

        try {
            FileOutputStream(file).use { fos ->
                val input = session.inputStream
                val buffer = ByteArray(ServerConfig.UPLOAD_CHUNK_SIZE)
                var remaining = contentLength
                while (remaining > 0) {
                    val toRead = minOf(remaining, buffer.size.toLong()).toInt()
                    val bytesRead = input.read(buffer, 0, toRead)
                    if (bytesRead == -1) break
                    fos.write(buffer, 0, bytesRead)
                    remaining -= bytesRead
                    _bytesReceived.addAndGet(bytesRead.toLong())
                }
                fos.flush()
            }

            Log.i(TAG, "File ${if (isNew) "created" else "updated"}: ${file.absolutePath} ($contentLength bytes)")

            return newFixedLengthResponse(
                if (isNew) Response.Status.CREATED else Response.Status.NO_CONTENT,
                "text/plain",
                ""
            )
        } catch (e: IOException) {
            Log.e(TAG, "PUT error: ${e.message}", e)
            return newFixedLengthResponse(
                Response.Status.INTERNAL_ERROR,
                "text/plain",
                "Write error: ${e.message}"
            )
        }
    }

    // ─── DELETE ──────────────────────────────────────────────────

    private fun handleDelete(session: IHTTPSession): Response {
        val file = resolveFile(session.uri)

        if (!file.exists()) {
            return newFixedLengthResponse(Response.Status.NOT_FOUND, "text/plain", "Not Found")
        }

        val deleted = if (file.isDirectory) {
            file.deleteRecursively()
        } else {
            file.delete()
        }

        return if (deleted) {
            Log.i(TAG, "Deleted: ${file.absolutePath}")
            newFixedLengthResponse(Response.Status.NO_CONTENT, "text/plain", "")
        } else {
            newFixedLengthResponse(
                Response.Status.INTERNAL_ERROR,
                "text/plain",
                "Could not delete"
            )
        }
    }

    // ─── MKCOL ───────────────────────────────────────────────────

    private fun handleMkcol(session: IHTTPSession): Response {
        val file = resolveFile(session.uri)

        if (file.exists()) {
            return newFixedLengthResponse(
                Response.Status.METHOD_NOT_ALLOWED,
                "text/plain",
                "Already exists"
            )
        }

        return if (file.mkdirs()) {
            Log.i(TAG, "Directory created: ${file.absolutePath}")
            newFixedLengthResponse(Response.Status.CREATED, "text/plain", "")
        } else {
            newFixedLengthResponse(
                Response.Status.INTERNAL_ERROR,
                "text/plain",
                "Could not create directory"
            )
        }
    }

    // ─── MOVE ────────────────────────────────────────────────────

    private fun handleMove(session: IHTTPSession): Response {
        val src = resolveFile(session.uri)

        if (!src.exists()) {
            return newFixedLengthResponse(Response.Status.NOT_FOUND, "text/plain", "Source not found")
        }

        val destHeader = session.headers["destination"] ?: return newFixedLengthResponse(
            Response.Status.BAD_REQUEST, "text/plain", "No Destination header"
        )

        val destUri = extractPathFromUrl(destHeader)
        val dest = resolveFile(destUri)

        dest.parentFile?.mkdirs()

        return if (src.renameTo(dest)) {
            Log.i(TAG, "Moved: ${src.absolutePath} → ${dest.absolutePath}")
            newFixedLengthResponse(Response.Status.CREATED, "text/plain", "")
        } else {
            newFixedLengthResponse(
                Response.Status.INTERNAL_ERROR,
                "text/plain",
                "Move failed"
            )
        }
    }

    // ─── COPY ────────────────────────────────────────────────────

    private fun handleCopy(session: IHTTPSession): Response {
        val src = resolveFile(session.uri)

        if (!src.exists()) {
            return newFixedLengthResponse(Response.Status.NOT_FOUND, "text/plain", "Source not found")
        }

        val destHeader = session.headers["destination"] ?: return newFixedLengthResponse(
            Response.Status.BAD_REQUEST, "text/plain", "No Destination header"
        )

        val destUri = extractPathFromUrl(destHeader)
        val dest = resolveFile(destUri)

        dest.parentFile?.mkdirs()

        return try {
            if (src.isDirectory) {
                src.copyRecursively(dest, overwrite = true)
            } else {
                src.copyTo(dest, overwrite = true)
            }
            Log.i(TAG, "Copied: ${src.absolutePath} → ${dest.absolutePath}")
            newFixedLengthResponse(Response.Status.CREATED, "text/plain", "")
        } catch (e: Exception) {
            newFixedLengthResponse(
                Response.Status.INTERNAL_ERROR,
                "text/plain",
                "Copy failed: ${e.message}"
            )
        }
    }

    // ─── Helpers ─────────────────────────────────────────────────

    /**
     * Resolve a URI path to a local file, preventing path traversal attacks.
     */
    private fun resolveFile(uri: String): File {
        val decoded = URLDecoder.decode(uri, "UTF-8").trimStart('/')
        val resolved = File(rootDir, decoded).canonicalFile

        // Security: ensure resolved path is within root
        if (!resolved.absolutePath.startsWith(rootDir.canonicalPath)) {
            return rootDir
        }
        return resolved
    }

    /**
     * Extract the path component from a full URL (used for Destination headers).
     */
    private fun extractPathFromUrl(url: String): String {
        return try {
            val uri = java.net.URI(url)
            uri.path ?: "/"
        } catch (e: Exception) {
            url
        }
    }

    /**
     * Serve a simple HTML directory listing for browser access.
     */
    private fun serveDirectoryListing(dir: File): Response {
        val relativePath = "/" + dir.toRelativeString(rootDir).replace("\\", "/")
        val sb = StringBuilder()
        sb.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
        sb.append("<title>PhoneBridge — $relativePath</title>")
        sb.append("<style>")
        sb.append("body{font-family:system-ui,sans-serif;margin:2em;background:#1a1a2e;color:#e0e0e0;}")
        sb.append("h1{color:#4CAF50;} a{color:#64B5F6;text-decoration:none;}")
        sb.append("a:hover{text-decoration:underline;} li{margin:0.3em 0;}")
        sb.append("</style></head><body>")
        sb.append("<h1>📁 $relativePath</h1><ul>")

        if (dir != rootDir) {
            sb.append("<li>⬆️ <a href=\"..\">..</a></li>")
        }

        val children = dir.listFiles()?.sortedBy { it.name.lowercase() } ?: emptyList()
        for (child in children) {
            if (child.name.startsWith(".")) continue
            val icon = if (child.isDirectory) "📁" else "📄"
            val name = child.name + if (child.isDirectory) "/" else ""
            val size = if (child.isFile) " (${formatSize(child.length())})" else ""
            sb.append("<li>$icon <a href=\"${child.name}${if (child.isDirectory) "/" else ""}\">$name</a>$size</li>")
        }

        sb.append("</ul><hr><p style='color:#888;'>PhoneBridge v${ServerConfig.VERSION}</p>")
        sb.append("</body></html>")

        return newFixedLengthResponse(Response.Status.OK, "text/html", sb.toString())
    }

    private fun formatSize(bytes: Long): String {
        val units = arrayOf("B", "KB", "MB", "GB")
        var size = bytes.toDouble()
        for (unit in units) {
            if (size < 1024) return "%.1f %s".format(size, unit)
            size /= 1024
        }
        return "%.1f TB".format(size)
    }
}
