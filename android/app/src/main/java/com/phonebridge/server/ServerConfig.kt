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
    const val VERSION = "2"

    /** Maximum upload chunk size (64KB) */
    const val UPLOAD_CHUNK_SIZE = 65536

    /** Maximum request body size for PROPFIND (1MB) */
    const val MAX_PROPFIND_BODY = 1048576

    /** Default username for Basic Auth */
    const val AUTH_USERNAME = "phonebridge"

    /** Whether HTTPS/TLS is enabled */
    const val HTTPS_ENABLED = true

    /** Keystore filename for the self-signed certificate */
    const val KEYSTORE_FILENAME = "phonebridge_keystore.p12"

    /** Keystore alias for the server certificate */
    const val KEYSTORE_ALIAS = "phonebridge-server"

    /** Certificate validity in years */
    const val CERT_VALIDITY_YEARS = 10
}
