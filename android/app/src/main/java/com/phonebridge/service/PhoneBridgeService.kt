package com.phonebridge.service

import android.app.*
import android.content.Context
import android.content.Intent
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Environment
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import com.phonebridge.MainActivity
import com.phonebridge.R
import com.phonebridge.discovery.NsdAdvertiser
import com.phonebridge.server.ServerConfig
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

        const val BROADCAST_STATUS = "com.phonebridge.broadcast.STATUS"
        const val EXTRA_IS_RUNNING = "is_running"
        const val EXTRA_IP_ADDRESS = "ip_address"
        const val EXTRA_PORT = "port"
    }

    private var webDavServer: WebDavServer? = null
    private var nsdAdvertiser: NsdAdvertiser? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var isRunning = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
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

            // Start WebDAV server
            val port = ServerConfig.DEFAULT_PORT
            webDavServer = WebDavServer(port, storageRoot)
            webDavServer?.start()

            // Start mDNS advertiser
            val deviceName = getDeviceName()
            nsdAdvertiser = NsdAdvertiser(this)
            nsdAdvertiser?.register(port, deviceName)

            isRunning = true
            val ip = getLocalIpAddress()

            Log.i(TAG, "🚀 Server started on http://$ip:$port")
            Log.i(TAG, "   Serving: ${storageRoot.absolutePath}")
            Log.i(TAG, "   Device: $deviceName")

            // Show foreground notification
            startForeground(NOTIFICATION_ID, buildNotification(ip, port))

            broadcastStatus()

        } catch (e: Exception) {
            Log.e(TAG, "Failed to start server", e)
            stopServer()
        }
    }

    private fun stopServer() {
        Log.i(TAG, "Stopping server...")

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

        return NotificationCompat.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setContentTitle("PhoneBridge Active")
            .setContentText("Sharing storage at $ip:$port")
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
        val intent = Intent(BROADCAST_STATUS).apply {
            putExtra(EXTRA_IS_RUNNING, isRunning)
            putExtra(EXTRA_IP_ADDRESS, getLocalIpAddress())
            putExtra(EXTRA_PORT, ServerConfig.DEFAULT_PORT)
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

    // ─── Helpers ───────────────────────────────────────────────

    private fun getStorageRoot(): File {
        // Use the primary external storage root (sdcard)
        return Environment.getExternalStorageDirectory()
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
