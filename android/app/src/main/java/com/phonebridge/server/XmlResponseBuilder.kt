package com.phonebridge.server

import java.io.File
import java.net.URLDecoder
import java.net.URLEncoder
import java.text.SimpleDateFormat
import java.util.*

/**
 * Builds WebDAV-compliant XML responses for PROPFIND requests.
 */
object XmlResponseBuilder {

    private val httpDateFormat = SimpleDateFormat("EEE, dd MMM yyyy HH:mm:ss 'GMT'", Locale.US).apply {
        timeZone = TimeZone.getTimeZone("GMT")
    }

    /**
     * Build a complete multistatus XML response for a PROPFIND request.
     *
     * @param rootDir The root directory being served
     * @param targetFile The file/directory requested
     * @param depth The Depth header value ("0" or "1")
     * @return Complete XML string for the 207 Multi-Status response
     */
    fun buildPropfindResponse(rootDir: File, targetFile: File, depth: String): String {
        val sb = StringBuilder()
        sb.append("<?xml version=\"1.0\" encoding=\"utf-8\" ?>\n")
        sb.append("<D:multistatus xmlns:D=\"DAV:\">\n")

        // Always include the target itself
        sb.append(buildResponseElement(rootDir, targetFile))

        // If depth is "1" and target is a directory, include children
        if (depth != "0" && targetFile.isDirectory) {
            val children = targetFile.listFiles()
            if (children != null) {
                children.sortBy { it.name.lowercase() }
                for (child in children) {
                    // Skip hidden files
                    if (child.name.startsWith(".")) continue
                    sb.append(buildResponseElement(rootDir, child))
                }
            }
        }

        sb.append("</D:multistatus>")
        return sb.toString()
    }

    /**
     * Build a single <D:response> element for a file or directory.
     */
    private fun buildResponseElement(rootDir: File, file: File): String {
        val relativePath = file.toRelativeString(rootDir).replace("\\", "/")
        val href = if (relativePath.isEmpty()) {
            "/"
        } else {
            "/" + encodePath(relativePath) + if (file.isDirectory) "/" else ""
        }

        val lastModified = httpDateFormat.format(Date(file.lastModified()))

        return if (file.isDirectory) {
            """
            |<D:response>
            |  <D:href>$href</D:href>
            |  <D:propstat>
            |    <D:prop>
            |      <D:displayname>${escapeXml(file.name)}</D:displayname>
            |      <D:getlastmodified>$lastModified</D:getlastmodified>
            |      <D:resourcetype><D:collection/></D:resourcetype>
            |    </D:prop>
            |    <D:status>HTTP/1.1 200 OK</D:status>
            |  </D:propstat>
            |</D:response>
            """.trimMargin() + "\n"
        } else {
            """
            |<D:response>
            |  <D:href>$href</D:href>
            |  <D:propstat>
            |    <D:prop>
            |      <D:displayname>${escapeXml(file.name)}</D:displayname>
            |      <D:getcontentlength>${file.length()}</D:getcontentlength>
            |      <D:getlastmodified>$lastModified</D:getlastmodified>
            |      <D:getcontenttype>${guessMimeType(file)}</D:getcontenttype>
            |      <D:resourcetype/>
            |    </D:prop>
            |    <D:status>HTTP/1.1 200 OK</D:status>
            |  </D:propstat>
            |</D:response>
            """.trimMargin() + "\n"
        }
    }

    /**
     * URL-encode each segment of the path, preserving "/" separators.
     */
    private fun encodePath(path: String): String {
        return path.split("/").joinToString("/") { segment ->
            URLEncoder.encode(segment, "UTF-8")
                .replace("+", "%20")
        }
    }

    /**
     * Escape special XML characters.
     */
    private fun escapeXml(text: String): String {
        return text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;")
            .replace("'", "&apos;")
    }

    /**
     * Guess MIME type from file extension.
     */
    fun guessMimeType(file: File): String {
        val ext = file.extension.lowercase()
        return when (ext) {
            "txt" -> "text/plain"
            "html", "htm" -> "text/html"
            "css" -> "text/css"
            "js" -> "application/javascript"
            "json" -> "application/json"
            "xml" -> "application/xml"
            "jpg", "jpeg" -> "image/jpeg"
            "png" -> "image/png"
            "gif" -> "image/gif"
            "webp" -> "image/webp"
            "svg" -> "image/svg+xml"
            "mp4" -> "video/mp4"
            "mp3" -> "audio/mpeg"
            "wav" -> "audio/wav"
            "ogg" -> "audio/ogg"
            "pdf" -> "application/pdf"
            "zip" -> "application/zip"
            "apk" -> "application/vnd.android.package-archive"
            "doc" -> "application/msword"
            "docx" -> "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            "xls" -> "application/vnd.ms-excel"
            "xlsx" -> "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            "ppt" -> "application/vnd.ms-powerpoint"
            "pptx" -> "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            else -> "application/octet-stream"
        }
    }
}
