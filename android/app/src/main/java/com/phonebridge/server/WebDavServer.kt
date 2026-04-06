package com.phonebridge.server

import android.util.Log
import fi.iki.elonen.NanoHTTPD
import java.io.*
import java.net.URLDecoder

/**
 * WebDAV server built on NanoHTTPD.
 *
 * Serves the phone's storage via a subset of the WebDAV protocol,
 * supporting the methods needed by rclone: OPTIONS, PROPFIND, GET,
 * PUT, DELETE, MKCOL, MOVE, COPY.
 *
 * @param port The port to listen on (default: 8273)
 * @param rootDir The root directory to serve
 */
class WebDavServer(
    port: Int = ServerConfig.DEFAULT_PORT,
    private val rootDir: File
) : NanoHTTPD(port) {

    companion object {
        private const val TAG = "WebDavServer"
    }

    init {
        require(rootDir.exists() && rootDir.isDirectory) {
            "Root directory must exist and be a directory: ${rootDir.absolutePath}"
        }
    }

    override fun serve(session: IHTTPSession): Response {
        val method = session.method.name.uppercase()
        val uri = session.uri ?: "/"

        Log.d(TAG, "$method $uri")

        return try {
            when (method) {
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
        } catch (e: Exception) {
            Log.e(TAG, "Error handling $method $uri", e)
            newFixedLengthResponse(
                Response.Status.INTERNAL_ERROR,
                "text/plain",
                "Internal server error: ${e.message}"
            )
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

        // Serve file with streaming
        val mimeType = XmlResponseBuilder.guessMimeType(file)
        val fis = FileInputStream(file)
        val response = newFixedLengthResponse(
            Response.Status.OK, mimeType, fis, file.length()
        )
        response.addHeader("Accept-Ranges", "bytes")
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
