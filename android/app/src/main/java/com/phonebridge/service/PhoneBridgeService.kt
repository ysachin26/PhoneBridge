package com.phonebridge.service

import android.app.*
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.net.wifi.WifiManager
import android.os.*
import android.util.Log
import androidx.core.app.NotificationCompat
import com.phonebridge.MainActivity
import com.phonebridge.R
import com.phonebridge.discovery.NsdAdvertiser
import com.phonebridge.server.ServerConfig
import com.phonebridge.server.TlsHelper
import com.phonebridge.server.WebDavServer
import java.io.File
import java.net.Inet4Address
import java.net.NetworkInterface

/**
 * Foreground service that runs the WebDAV server and mDNS advertiser.
 *
 * Keeps the server alive even when the app is in the background by
 * showing a persistent notification. Also acquires Wi-Fi and wake
 * locks to prevent the connection from dropping during sleep.
 */
class PhoneBridgeService : Service() {

    companion object {
        private const val TAG = "PhoneBridgeService"
        private const val NOTIFICATION_CHANNEL_ID = "phonebridge_service"
        private const val NOTIFICATION_ID = 1001

        const val ACTION_START = "com.phonebridge.action.START"
        const val ACTION_STOP = "com.phonebridge.action.STOP"
        const val ACTION_STATUS = "com.phonebridge.action.STATUS"

        private const val PREFS_NAME = "phonebridge_prefs"
        private const val PREF_AUTH_PASSWORD = "auth_password"

        const val BROADCAST_STATUS = "com.phonebridge.broadcast.STATUS"
        const val EXTRA_IS_RUNNING = "is_running"
        const val EXTRA_IP_ADDRESS = "ip_address"
        const val EXTRA_PORT = "port"
        const val EXTRA_PASSWORD = "auth_password"
        const val EXTRA_PROTOCOL = "protocol"

        // Stats extras
        const val EXTRA_BYTES_SERVED = "bytes_served"
        const val EXTRA_BYTES_RECEIVED = "bytes_received"
        const val EXTRA_TOTAL_REQUESTS = "total_requests"
        const val EXTRA_ACTIVE_CONNECTIONS = "active_connections"
        const val EXTRA_UPTIME_SECONDS = "uptime_seconds"

        // Storage extras
        const val EXTRA_STORAGE_TOTAL = "storage_total"
        const val EXTRA_STORAGE_USED = "storage_used"
        const val EXTRA_STORAGE_FREE = "storage_free"

        private const val STATS_INTERVAL_MS = 2000L  // Broadcast stats every 2 seconds
    }

    private var webDavServer: WebDavServer? = null
    private var nsdAdvertiser: NsdAdvertiser? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var isRunning = false
    private var authPassword: String = ""
    private var protocol: String = "http"
    private val statsHandler = Handler(Looper.getMainLooper())
    private val statsBroadcaster = object : Runnable {
        override fun run() {
            if (isRunning) {
                broadcastStatus()
                statsHandler.postDelayed(this, STATS_INTERVAL_MS)
            }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        authPassword = loadOrGeneratePassword()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> startServer()
            ACTION_STOP -> stopServer()
            ACTION_STATUS -> broadcastStatus()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        stopServer()
        super.onDestroy()
    }

    // ─── Server Lifecycle ──────────────────────────────────────

    private fun startServer() {
        if (isRunning) {
            Log.w(TAG, "Server already running")
            return
        }

        try {
            // Get the storage root
            val storageRoot = getStorageRoot()
            if (!storageRoot.exists()) {
                Log.e(TAG, "Storage root does not exist: ${storageRoot.absolutePath}")
                stopSelf()
                return
            }

            // Acquire locks
            acquireWifiLock()
            acquireWakeLock()

            // Start WebDAV server with authentication
            val port = ServerConfig.DEFAULT_PORT
            webDavServer = WebDavServer(port, storageRoot, authPassword)

            // Enable HTTPS/TLS if configured
            if (ServerConfig.HTTPS_ENABLED) {
                try {
                    val sslFactory = TlsHelper.getSSLServerSocketFactory(applicationContext)
                    webDavServer?.makeSecure(sslFactory, null)
                    protocol = "https"
                    Log.i(TAG, "🔒 HTTPS/TLS enabled")
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to enable HTTPS, falling back to HTTP", e)
                    protocol = "http"
                }
            } else {
                protocol = "http"
            }

            webDavServer?.start()

            // Start mDNS advertiser with auth and protocol info
            val deviceName = getDeviceName()
            nsdAdvertiser = NsdAdvertiser(this)
            nsdAdvertiser?.register(
                port = port,
                deviceName = deviceName,
                authRequired = true,
                authUser = ServerConfig.AUTH_USERNAME,
                protocol = protocol
            )

            isRunning = true
            val ip = getLocalIpAddress()

            Log.i(TAG, "🚀 Server started on $protocol://$ip:$port")
            Log.i(TAG, "   Serving: ${storageRoot.absolutePath}")
            Log.i(TAG, "   Device: $deviceName")
            Log.i(TAG, "   Auth: Basic (user=${ServerConfig.AUTH_USERNAME})")

            // Show foreground notification
            startForeground(NOTIFICATION_ID, buildNotification(ip, port))

            broadcastStatus()

            // Start periodic stats broadcasting
            statsHandler.postDelayed(statsBroadcaster, STATS_INTERVAL_MS)

        } catch (e: Exception) {
            Log.e(TAG, "Failed to start server", e)
            stopServer()
        }
    }

    private fun stopServer() {
        Log.i(TAG, "Stopping server...")

        // Stop stats broadcasting
        statsHandler.removeCallbacks(statsBroadcaster)

        // Stop mDNS
        nsdAdvertiser?.unregister()
        nsdAdvertiser = null

        // Stop WebDAV server
        webDavServer?.stop()
        webDavServer = null

        // Release locks
        releaseWifiLock()
        releaseWakeLock()

        isRunning = false
        broadcastStatus()

        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()

        Log.i(TAG, "Server stopped")
    }

    // ─── Notification ──────────────────────────────────────────

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                NOTIFICATION_CHANNEL_ID,
                "PhoneBridge Server",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Shows when PhoneBridge is sharing your storage"
                setShowBadge(false)
            }
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(ip: String, port: Int): Notification {
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val stopIntent = PendingIntent.getService(
            this,
            1,
            Intent(this, PhoneBridgeService::class.java).apply {
                action = ACTION_STOP
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val protocolLabel = if (protocol == "https") "🔒 " else ""

        return NotificationCompat.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setContentTitle("PhoneBridge Active")
            .setContentText("${protocolLabel}Sharing storage at $protocol://$ip:$port")
            .setSmallIcon(android.R.drawable.stat_sys_upload_done)
            .setOngoing(true)
            .setContentIntent(pendingIntent)
            .addAction(
                android.R.drawable.ic_media_pause,
                "Stop",
                stopIntent
            )
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()
    }

    // ─── Broadcast ─────────────────────────────────────────────

    private fun broadcastStatus() {
        val stats = webDavServer?.getStats()
        val storageRoot = getStorageRoot()

        val intent = Intent(BROADCAST_STATUS).apply {
            putExtra(EXTRA_IS_RUNNING, isRunning)
            putExtra(EXTRA_IP_ADDRESS, getLocalIpAddress())
            putExtra(EXTRA_PORT, ServerConfig.DEFAULT_PORT)
            putExtra(EXTRA_PASSWORD, authPassword)
            putExtra(EXTRA_PROTOCOL, protocol)

            // Stats
            putExtra(EXTRA_BYTES_SERVED, stats?.bytesServed ?: 0L)
            putExtra(EXTRA_BYTES_RECEIVED, stats?.bytesReceived ?: 0L)
            putExtra(EXTRA_TOTAL_REQUESTS, stats?.totalRequests ?: 0L)
            putExtra(EXTRA_ACTIVE_CONNECTIONS, stats?.activeConnections ?: 0)
            putExtra(EXTRA_UPTIME_SECONDS, stats?.uptimeSeconds ?: 0L)

            // Storage info
            putExtra(EXTRA_STORAGE_TOTAL, storageRoot.totalSpace)
            putExtra(EXTRA_STORAGE_USED, storageRoot.totalSpace - storageRoot.freeSpace)
            putExtra(EXTRA_STORAGE_FREE, storageRoot.freeSpace)

            setPackage(packageName)
        }
        sendBroadcast(intent)
    }

    // ─── Locks ─────────────────────────────────────────────────

    private fun acquireWifiLock() {
        val wifiManager = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        wifiLock = wifiManager.createWifiLock(
            WifiManager.WIFI_MODE_FULL_HIGH_PERF,
            "PhoneBridge:WifiLock"
        )
        wifiLock?.setReferenceCounted(false)
        wifiLock?.acquire()
        Log.d(TAG, "Wi-Fi lock acquired")
    }

    private fun releaseWifiLock() {
        wifiLock?.let {
            if (it.isHeld) {
                it.release()
                Log.d(TAG, "Wi-Fi lock released")
            }
        }
        wifiLock = null
    }

    private fun acquireWakeLock() {
        val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = powerManager.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "PhoneBridge:WakeLock"
        )
        wakeLock?.setReferenceCounted(false)
        wakeLock?.acquire(10 * 60 * 60 * 1000L) // 10 hours max
        Log.d(TAG, "Wake lock acquired")
    }

    private fun releaseWakeLock() {
        wakeLock?.let {
            if (it.isHeld) {
                it.release()
                Log.d(TAG, "Wake lock released")
            }
        }
        wakeLock = null
    }

    // ─── Auth ──────────────────────────────────────────────────

    /**
     * Load existing password from SharedPreferences, or generate a new one.
     * Password persists across app restarts so the PC doesn't need to re-pair.
     */
    private fun loadOrGeneratePassword(): String {
        val prefs: SharedPreferences = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val existing = prefs.getString(PREF_AUTH_PASSWORD, null)
        if (!existing.isNullOrBlank()) {
            Log.i(TAG, "Loaded existing auth password")
            return existing
        }

        // Generate a random 8-character alphanumeric password
        val chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
        val password = (1..8).map { chars.random() }.joinToString("")

        prefs.edit().putString(PREF_AUTH_PASSWORD, password).apply()
        Log.i(TAG, "Generated new auth password")
        return password
    }

    // ─── Helpers ───────────────────────────────────────────────

    private fun getStorageRoot(): File {
        val prefs = getSharedPreferences("phonebridge_prefs", Context.MODE_PRIVATE)
        val folderPref = prefs.getString("shared_folder", "all") ?: "all"
        val externalRoot = Environment.getExternalStorageDirectory()

        val targetDir = when (folderPref) {
            "dcim" -> File(externalRoot, "DCIM")
            "downloads" -> File(externalRoot, "Download")
            "music" -> File(externalRoot, "Music")
            else -> externalRoot  // "all" = full storage
        }

        // Fall back to full storage if the subfolder doesn't exist
        return if (targetDir.exists() && targetDir.isDirectory) {
            Log.i(TAG, "Sharing folder: ${targetDir.absolutePath}")
            targetDir
        } else {
            Log.w(TAG, "Folder ${targetDir.absolutePath} not found, falling back to full storage")
            externalRoot
        }
    }

    private fun getDeviceName(): String {
        val manufacturer = Build.MANUFACTURER.replaceFirstChar { it.uppercase() }
        val model = Build.MODEL
        return if (model.startsWith(manufacturer, ignoreCase = true)) {
            model
        } else {
            "$manufacturer $model"
        }
    }

    private fun getLocalIpAddress(): String {
        try {
            val interfaces = NetworkInterface.getNetworkInterfaces()
            while (interfaces.hasMoreElements()) {
                val iface = interfaces.nextElement()
                if (iface.isLoopback || !iface.isUp) continue

                val addresses = iface.inetAddresses
                while (addresses.hasMoreElements()) {
                    val addr = addresses.nextElement()
                    if (addr is Inet4Address && !addr.isLoopbackAddress) {
                        return addr.hostAddress ?: "0.0.0.0"
                    }
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error getting IP address", e)
        }
        return "0.0.0.0"
    }
}
