package com.phonebridge

import android.Manifest
import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import android.view.View
import android.view.animation.AnimationUtils
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.phonebridge.databinding.ActivityMainBinding
import com.phonebridge.receiver.BootReceiver
import com.phonebridge.service.PhoneBridgeService

class MainActivity : AppCompatActivity() {

    companion object {
        private const val PERMISSION_REQUEST_CODE = 100
        private const val PREFS_NAME = "phonebridge_prefs"
        private const val PREF_SHARED_FOLDER = "shared_folder"

        const val FOLDER_ALL = "all"
        const val FOLDER_DCIM = "dcim"
        const val FOLDER_DOWNLOADS = "downloads"
        const val FOLDER_MUSIC = "music"
    }

    private lateinit var binding: ActivityMainBinding
    private var isServerRunning = false
    private var pulseAnimation: android.view.animation.Animation? = null
    private val chipMap = mutableMapOf<String, TextView>()

    private val statusReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent?.action == PhoneBridgeService.BROADCAST_STATUS) {
                val running = intent.getBooleanExtra(PhoneBridgeService.EXTRA_IS_RUNNING, false)
                val ip = intent.getStringExtra(PhoneBridgeService.EXTRA_IP_ADDRESS) ?: "0.0.0.0"
                val port = intent.getIntExtra(PhoneBridgeService.EXTRA_PORT, 8273)
                val password = intent.getStringExtra(PhoneBridgeService.EXTRA_PASSWORD) ?: ""
                val protocol = intent.getStringExtra(PhoneBridgeService.EXTRA_PROTOCOL) ?: "http"
                val bytesServed = intent.getLongExtra(PhoneBridgeService.EXTRA_BYTES_SERVED, 0)
                val bytesReceived = intent.getLongExtra(PhoneBridgeService.EXTRA_BYTES_RECEIVED, 0)
                val totalRequests = intent.getLongExtra(PhoneBridgeService.EXTRA_TOTAL_REQUESTS, 0)
                val activeConnections = intent.getIntExtra(PhoneBridgeService.EXTRA_ACTIVE_CONNECTIONS, 0)
                val uptimeSeconds = intent.getLongExtra(PhoneBridgeService.EXTRA_UPTIME_SECONDS, 0)
                val storageTotal = intent.getLongExtra(PhoneBridgeService.EXTRA_STORAGE_TOTAL, 0)
                val storageUsed = intent.getLongExtra(PhoneBridgeService.EXTRA_STORAGE_USED, 0)
                val tailscaleIp = intent.getStringExtra(PhoneBridgeService.EXTRA_TAILSCALE_IP) ?: ""
                val isRemoteAvailable = intent.getBooleanExtra(PhoneBridgeService.EXTRA_IS_REMOTE_AVAILABLE, false)

                updateUI(running, ip, port, password, protocol)
                updateStats(bytesServed, bytesReceived, totalRequests, activeConnections, uptimeSeconds)
                updateStorage(storageTotal, storageUsed)
                updateRemoteAccess(running, tailscaleIp, isRemoteAvailable, port)
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        pulseAnimation = AnimationUtils.loadAnimation(this, R.anim.pulse)
        setupToggle()
        setupCopyPassword()
        setupAutoStartToggle()
        setupFolderChips()
        setupRemoteAccess()
        checkPermissions()
    }

    override fun onResume() {
        super.onResume()
        val filter = IntentFilter(PhoneBridgeService.BROADCAST_STATUS)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(statusReceiver, filter, RECEIVER_NOT_EXPORTED)
        } else {
            registerReceiver(statusReceiver, filter)
        }
        try {
            startService(Intent(this, PhoneBridgeService::class.java).apply {
                action = PhoneBridgeService.ACTION_STATUS
            })
        } catch (_: Exception) {}
    }

    override fun onPause() {
        super.onPause()
        try { unregisterReceiver(statusReceiver) } catch (_: Exception) {}
    }

    // ─── Setup ─────────────────────────────────────────────

    private fun setupToggle() {
        binding.btnToggle.setOnClickListener {
            if (isServerRunning) stopPhoneBridge() else startPhoneBridge()
        }
    }

    private fun setupCopyPassword() {
        binding.btnCopyPassword.setOnClickListener {
            val pw = binding.tvPassword.text.toString()
            if (pw.isNotBlank() && pw != "— — — —") {
                val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                clipboard.setPrimaryClip(ClipData.newPlainText("PhoneBridge Password", pw))
                Toast.makeText(this, "Copied", Toast.LENGTH_SHORT).show()
            }
        }

        // Regenerate password with confirmation
        binding.btnRegenPassword.setOnClickListener {
            if (!isServerRunning) {
                Toast.makeText(this, "Start the server first", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            android.app.AlertDialog.Builder(this)
                .setTitle("Regenerate Password")
                .setMessage("This will create a new connection code.\n\nYour PC will need to re-enter the new code to reconnect.")
                .setPositiveButton("Regenerate") { _, _ ->
                    startService(Intent(this, PhoneBridgeService::class.java).apply {
                        action = PhoneBridgeService.ACTION_REGENERATE_PASSWORD
                    })
                    Toast.makeText(this, "Password regenerated", Toast.LENGTH_SHORT).show()
                }
                .setNegativeButton("Cancel", null)
                .show()
        }
    }

    private fun setupAutoStartToggle() {
        val prefs = getSharedPreferences(BootReceiver.PREFS_NAME, Context.MODE_PRIVATE)
        binding.switchAutoStart.isChecked = prefs.getBoolean(BootReceiver.PREF_AUTO_START, false)
        binding.switchAutoStart.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean(BootReceiver.PREF_AUTO_START, checked).apply()
        }

        // Info tooltip
        binding.btnAutoStartInfo.setOnClickListener {
            Toast.makeText(
                this,
                "Auto-start: Automatically start the server when your phone boots up",
                Toast.LENGTH_LONG
            ).show()
        }
    }

    private fun setupFolderChips() {
        chipMap[FOLDER_ALL] = binding.chipAllStorage
        chipMap[FOLDER_DCIM] = binding.chipDCIM
        chipMap[FOLDER_DOWNLOADS] = binding.chipDownloads
        chipMap[FOLDER_MUSIC] = binding.chipMusic

        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        selectChip(prefs.getString(PREF_SHARED_FOLDER, FOLDER_ALL) ?: FOLDER_ALL)

        chipMap.forEach { (id, view) ->
            view.setOnClickListener {
                selectChip(id)
                prefs.edit().putString(PREF_SHARED_FOLDER, id).apply()
                if (isServerRunning) {
                    Toast.makeText(this, "Restart to apply", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }

    private fun selectChip(selectedId: String) {
        chipMap.forEach { (id, chip) ->
            if (id == selectedId) {
                chip.setBackgroundResource(R.drawable.bg_chip_selected)
                chip.setTextColor(ContextCompat.getColor(this, R.color.bg_card))
            } else {
                chip.setBackgroundResource(R.drawable.bg_chip)
                chip.setTextColor(ContextCompat.getColor(this, R.color.text_secondary))
            }
        }
    }

    // ─── Service Control ───────────────────────────────────

    private fun startPhoneBridge() {
        if (!hasAllPermissions()) { requestPermissions(); return }
        val intent = Intent(this, PhoneBridgeService::class.java).apply {
            action = PhoneBridgeService.ACTION_START
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(intent)
        else startService(intent)
    }

    private fun stopPhoneBridge() {
        startService(Intent(this, PhoneBridgeService::class.java).apply {
            action = PhoneBridgeService.ACTION_STOP
        })
    }

    // ─── UI Updates ────────────────────────────────────────

    private fun updateUI(running: Boolean, ip: String, port: Int, password: String, protocol: String) {
        isServerRunning = running
        runOnUiThread {
            if (running) {
                // Toggle → green with pulse + glow ring
                binding.btnToggle.setBackgroundResource(R.drawable.bg_toggle_active)
                binding.btnToggle.setImageResource(R.drawable.ic_stop)
                binding.btnToggle.imageTintList = android.content.res.ColorStateList.valueOf(0xFFFFFFFF.toInt())
                binding.btnToggle.startAnimation(pulseAnimation)
                binding.viewGlowRing.visibility = View.VISIBLE
                binding.viewGlowRing.startAnimation(pulseAnimation)

                binding.tvStatus.text = "Running"
                binding.tvStatus.setTextColor(ContextCompat.getColor(this, R.color.active_green))

                // Show cards
                binding.cardConnection.visibility = View.VISIBLE
                binding.cardStats.visibility = View.VISIBLE
                binding.cardStorage.visibility = View.VISIBLE
                binding.tvInfo.visibility = View.GONE

                binding.tvAddress.text = "$protocol://$ip:$port"
                val securedLabel = if (protocol == "https") "Secured · HTTPS" else "HTTP"
                binding.tvProtocolBadge.text = securedLabel
                binding.tvPassword.text = password
            } else {
                // Toggle → grey, no animation, no glow
                binding.btnToggle.clearAnimation()
                binding.viewGlowRing.clearAnimation()
                binding.viewGlowRing.visibility = View.GONE
                binding.btnToggle.setBackgroundResource(R.drawable.bg_toggle_inactive)
                binding.btnToggle.setImageResource(R.drawable.ic_power)
                binding.btnToggle.imageTintList = android.content.res.ColorStateList.valueOf(
                    ContextCompat.getColor(this, R.color.text_muted)
                )

                binding.tvStatus.text = "Tap to start"
                binding.tvStatus.setTextColor(ContextCompat.getColor(this, R.color.text_muted))

                // Hide cards
                binding.cardConnection.visibility = View.GONE
                binding.cardStats.visibility = View.GONE
                binding.cardStorage.visibility = View.GONE
                binding.tvInfo.visibility = View.VISIBLE
            }
        }
    }

    private fun updateStats(bytesServed: Long, bytesReceived: Long, totalRequests: Long, connections: Int, uptime: Long) {
        runOnUiThread {
            binding.tvBytesServed.text = formatBytes(bytesServed)
            binding.tvBytesReceived.text = formatBytes(bytesReceived)
            binding.tvUptime.text = formatUptime(uptime)
            binding.tvConnections.text = connections.toString()
            binding.tvTotalRequests.text = "${formatNumber(totalRequests)} requests"
        }
    }

    private fun updateStorage(total: Long, used: Long) {
        if (total <= 0) return
        runOnUiThread {
            val pct = ((used.toDouble() / total) * 100).toInt().coerceIn(0, 100)
            binding.progressStorage.progress = pct
            binding.tvStoragePercent.text = "$pct%"
            binding.tvStorageUsed.text = "Used: ${formatBytes(used)}"
            binding.tvStorageTotal.text = "Total: ${formatBytes(total)}"
        }
    }

    // ─── Remote Access ─────────────────────────────────────

    private fun setupRemoteAccess() {
        // Copy Tailscale IP button
        binding.btnCopyTailscaleIp.setOnClickListener {
            val ip = binding.tvTailscaleIp.text.toString()
            if (ip.isNotBlank()) {
                val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                clipboard.setPrimaryClip(ClipData.newPlainText("PhoneBridge Remote Address", ip))
                Toast.makeText(this, "Remote address copied", Toast.LENGTH_SHORT).show()
            }
        }

        // Get Tailscale button → Play Store
        binding.btnGetTailscale.setOnClickListener {
            try {
                // Try Play Store app first
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=com.tailscale.ipn")))
            } catch (_: Exception) {
                // Fall back to Play Store website
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://play.google.com/store/apps/details?id=com.tailscale.ipn")))
            }
        }
    }

    private fun updateRemoteAccess(running: Boolean, tailscaleIp: String, isRemoteAvailable: Boolean, port: Int) {
        runOnUiThread {
            if (!running) {
                binding.cardRemoteAccess.visibility = View.GONE
                return@runOnUiThread
            }

            binding.cardRemoteAccess.visibility = View.VISIBLE

            if (isRemoteAvailable && tailscaleIp.isNotEmpty()) {
                // Tailscale is active — show the IP
                binding.layoutTailscaleAvailable.visibility = View.VISIBLE
                binding.layoutTailscaleNotInstalled.visibility = View.GONE
                binding.tvTailscaleIp.text = "$tailscaleIp:$port"
                binding.tvRemoteStatus.text = "● Available"
                binding.tvRemoteStatus.setTextColor(ContextCompat.getColor(this, R.color.active_green))
            } else {
                // Tailscale not detected — show install prompt
                binding.layoutTailscaleAvailable.visibility = View.GONE
                binding.layoutTailscaleNotInstalled.visibility = View.VISIBLE
                binding.tvRemoteStatus.text = "● Not configured"
                binding.tvRemoteStatus.setTextColor(ContextCompat.getColor(this, R.color.text_muted))
            }
        }
    }

    // ─── Formatting ────────────────────────────────────────

    private fun formatBytes(bytes: Long): String = when {
        bytes < 1024 -> "$bytes B"
        bytes < 1024 * 1024 -> String.format("%.1f KB", bytes / 1024.0)
        bytes < 1024 * 1024 * 1024 -> String.format("%.1f MB", bytes / (1024.0 * 1024))
        else -> String.format("%.2f GB", bytes / (1024.0 * 1024 * 1024))
    }

    private fun formatUptime(s: Long) = String.format("%02d:%02d:%02d", s / 3600, (s % 3600) / 60, s % 60)

    private fun formatNumber(n: Long): String = when {
        n >= 1_000_000 -> String.format("%.1fM", n / 1_000_000.0)
        n >= 1_000 -> String.format("%.1fK", n / 1_000.0)
        else -> n.toString()
    }

    // ─── Permissions ──────────────────────────────────────

    private fun hasAllPermissions(): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            if (!Environment.isExternalStorageManager()) return false
        } else {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.READ_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) return false
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) return false
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return false
        }
        return true
    }

    private fun checkPermissions() {
        if (!hasAllPermissions()) {
            binding.tvInfo.text = "Storage and notification permissions are needed.\nTap the button to grant them."
        }
    }

    private fun requestPermissions() {
        val perms = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            if (!Environment.isExternalStorageManager()) {
                try {
                    startActivity(Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION).apply {
                        data = Uri.parse("package:$packageName")
                    })
                } catch (_: Exception) {
                    startActivity(Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
                }
                return
            }
        } else {
            perms.add(Manifest.permission.READ_EXTERNAL_STORAGE)
            perms.add(Manifest.permission.WRITE_EXTERNAL_STORAGE)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) perms.add(Manifest.permission.POST_NOTIFICATIONS)
        if (perms.isNotEmpty()) ActivityCompat.requestPermissions(this, perms.toTypedArray(), PERMISSION_REQUEST_CODE)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == PERMISSION_REQUEST_CODE && grantResults.all { it == PackageManager.PERMISSION_GRANTED }) {
            startPhoneBridge()
        }
    }
}
