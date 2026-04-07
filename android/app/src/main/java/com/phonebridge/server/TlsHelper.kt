package com.phonebridge.server

import android.content.Context
import android.util.Log
import org.bouncycastle.asn1.x500.X500Name
import org.bouncycastle.cert.X509v3CertificateBuilder
import org.bouncycastle.cert.jcajce.JcaX509CertificateConverter
import org.bouncycastle.cert.jcajce.JcaX509v3CertificateBuilder
import org.bouncycastle.jce.provider.BouncyCastleProvider
import org.bouncycastle.operator.jcajce.JcaContentSignerBuilder
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.math.BigInteger
import java.security.*
import java.security.cert.X509Certificate
import java.util.*
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLServerSocketFactory

/**
 * Generates and manages a self-signed TLS certificate for the WebDAV server.
 *
 * The certificate is persisted to the app's private storage so its fingerprint
 * remains stable across server restarts (important for pinning on the desktop side).
 */
object TlsHelper {

    private const val TAG = "TlsHelper"
    private const val KEYSTORE_PASSWORD = "phonebridge-internal"

    init {
        // Android ships a stripped-down BouncyCastle provider that shadows ours.
        // Remove it first, then add the full version from our dependency.
        try {
            Security.removeProvider(BouncyCastleProvider.PROVIDER_NAME)
        } catch (_: Exception) {}
        Security.addProvider(BouncyCastleProvider())
        Log.d(TAG, "Bouncy Castle provider registered: ${BouncyCastleProvider.PROVIDER_NAME}")
    }

    /**
     * Get or create an SSLServerSocketFactory for NanoHTTPD.
     *
     * On first call, generates a self-signed certificate and stores it
     * in a PKCS12 keystore in the app's files directory. On subsequent
     * calls, loads the existing keystore.
     *
     * @param context Application context for accessing private file storage
     * @return SSLServerSocketFactory suitable for NanoHTTPD.makeSecure()
     */
    fun getSSLServerSocketFactory(context: Context): SSLServerSocketFactory {
        val keystoreFile = File(context.filesDir, ServerConfig.KEYSTORE_FILENAME)
        val keyStore = loadOrCreateKeyStore(keystoreFile)

        val kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
        kmf.init(keyStore, KEYSTORE_PASSWORD.toCharArray())

        val sslContext = SSLContext.getInstance("TLS")
        sslContext.init(kmf.keyManagers, null, SecureRandom())

        Log.i(TAG, "SSL context initialized successfully")
        return sslContext.serverSocketFactory
    }

    /**
     * Load an existing PKCS12 keystore or create a new one with a self-signed cert.
     */
    private fun loadOrCreateKeyStore(keystoreFile: File): KeyStore {
        val keyStore = KeyStore.getInstance("PKCS12")

        if (keystoreFile.exists()) {
            try {
                FileInputStream(keystoreFile).use { fis ->
                    keyStore.load(fis, KEYSTORE_PASSWORD.toCharArray())
                }
                // Verify the alias exists
                if (keyStore.containsAlias(ServerConfig.KEYSTORE_ALIAS)) {
                    Log.i(TAG, "Loaded existing keystore from ${keystoreFile.absolutePath}")
                    return keyStore
                }
            } catch (e: Exception) {
                Log.w(TAG, "Failed to load existing keystore, generating new one: ${e.message}")
            }
        }

        // Generate a new self-signed certificate
        Log.i(TAG, "Generating new self-signed certificate...")
        return generateKeyStore(keystoreFile)
    }

    /**
     * Generate a new PKCS12 keystore with a self-signed RSA certificate.
     */
    private fun generateKeyStore(keystoreFile: File): KeyStore {
        // Generate RSA key pair
        val keyPairGenerator = KeyPairGenerator.getInstance("RSA")
        keyPairGenerator.initialize(2048, SecureRandom())
        val keyPair = keyPairGenerator.generateKeyPair()

        // Build self-signed certificate
        val now = Date()
        val calendar = Calendar.getInstance()
        calendar.time = now
        calendar.add(Calendar.YEAR, ServerConfig.CERT_VALIDITY_YEARS)
        val expiry = calendar.time

        val issuer = X500Name("CN=PhoneBridge Server, O=PhoneBridge, L=Local")
        val serial = BigInteger.valueOf(System.currentTimeMillis())

        val certBuilder: X509v3CertificateBuilder = JcaX509v3CertificateBuilder(
            issuer,
            serial,
            now,
            expiry,
            issuer, // Self-signed: subject = issuer
            keyPair.public
        )

        val signer = JcaContentSignerBuilder("SHA256WithRSA")
            .setProvider(BouncyCastleProvider.PROVIDER_NAME)
            .build(keyPair.private)

        val certificate: X509Certificate = JcaX509CertificateConverter()
            .setProvider(BouncyCastleProvider.PROVIDER_NAME)
            .getCertificate(certBuilder.build(signer))

        // Store in PKCS12 keystore
        val keyStore = KeyStore.getInstance("PKCS12")
        keyStore.load(null, null) // Initialize empty keystore
        keyStore.setKeyEntry(
            ServerConfig.KEYSTORE_ALIAS,
            keyPair.private,
            KEYSTORE_PASSWORD.toCharArray(),
            arrayOf(certificate)
        )

        // Persist to disk
        keystoreFile.parentFile?.mkdirs()
        FileOutputStream(keystoreFile).use { fos ->
            keyStore.store(fos, KEYSTORE_PASSWORD.toCharArray())
        }

        Log.i(TAG, "✅ Self-signed certificate generated and saved")
        Log.i(TAG, "   Valid until: $expiry")
        Log.i(TAG, "   Keystore: ${keystoreFile.absolutePath}")

        return keyStore
    }
}
