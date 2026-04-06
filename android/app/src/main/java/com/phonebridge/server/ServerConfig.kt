package com.phonebridge.server

/**
 * Server configuration constants and settings.
 */
object ServerConfig {
    /** Default port for the WebDAV server */
    const val DEFAULT_PORT = 8273

    /** mDNS service type for PhoneBridge discovery */
    const val SERVICE_TYPE = "_phonebridge._tcp."

    /** Protocol version */
    const val VERSION = "1"

    /** Maximum upload chunk size (64KB) */
    const val UPLOAD_CHUNK_SIZE = 65536

    /** Maximum request body size for PROPFIND (1MB) */
    const val MAX_PROPFIND_BODY = 1048576
}
