package com.phonebridge

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.phonebridge.databinding.ActivityMainBinding
import com.phonebridge.service.PhoneBridgeService

/**
 * Main activity with a simple toggle to start/stop the PhoneBridge server.
 */
class MainActivity : AppCompatActivity() {

    companion object {
        private const val PERMISSION_REQUEST_CODE = 100
    }

    private lateinit var binding: ActivityMainBinding
    private var isServerRunning = false

    private val statusReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent?.action == PhoneBridgeService.BROADCAST_STATUS) {
                val running = intent.getBooleanExtra(PhoneBridgeService.EXTRA_IS_RUNNING, false)
                val ip = intent.getStringExtra(PhoneBridgeService.EXTRA_IP_ADDRESS) ?: "0.0.0.0"
                val port = intent.getIntExtra(PhoneBridgeService.EXTRA_PORT, 8273)
                updateUI(running, ip, port)
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        setupUI()
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

        // Request current status
        val statusIntent = Intent(this, PhoneBridgeService::class.java).apply {
            action = PhoneBridgeService.ACTION_STATUS
        }
        startService(statusIntent)
    }

    override fun onPause() {
        super.onPause()
        try {
            unregisterReceiver(statusReceiver)
        } catch (_: Exception) {}
    }

    private fun setupUI() {
        binding.btnToggle.setOnClickListener {
            if (isServerRunning) {
                stopPhoneBridge()
            } else {
                startPhoneBridge()
            }
        }

        // Initial state
        updateUI(false, "—", 0)
    }

    private fun startPhoneBridge() {
        if (!hasAllPermissions()) {
            requestPermissions()
            return
        }

        val intent = Intent(this, PhoneBridgeService::class.java).apply {
            action = PhoneBridgeService.ACTION_START
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }

        Toast.makeText(this, "Starting PhoneBridge...", Toast.LENGTH_SHORT).show()
    }

    private fun stopPhoneBridge() {
        val intent = Intent(this, PhoneBridgeService::class.java).apply {
            action = PhoneBridgeService.ACTION_STOP
        }
        startService(intent)
    }

    private fun updateUI(running: Boolean, ip: String, port: Int) {
        isServerRunning = running

        runOnUiThread {
            if (running) {
                binding.btnToggle.text = "Stop Server"
                binding.btnToggle.setBackgroundColor(
                    ContextCompat.getColor(this, android.R.color.holo_red_dark)
                )
                binding.tvStatus.text = "🟢 Server Running"
                binding.tvAddress.text = "http://$ip:$port"
                binding.tvInfo.text = "Your PC can now discover and mount this phone's storage.\n\nMake sure PhoneBridge is running on your PC."
                binding.statusIndicator.setBackgroundColor(
                    ContextCompat.getColor(this, android.R.color.holo_green_dark)
                )
            } else {
                binding.btnToggle.text = "Start Server"
                binding.btnToggle.setBackgroundColor(
                    ContextCompat.getColor(this, android.R.color.holo_green_dark)
                )
                binding.tvStatus.text = "⚪ Server Stopped"
                binding.tvAddress.text = "—"
                binding.tvInfo.text = "Tap 'Start Server' to share your phone's storage with your PC wirelessly."
                binding.statusIndicator.setBackgroundColor(
                    ContextCompat.getColor(this, android.R.color.darker_gray)
                )
            }
        }
    }

    // ─── Permissions ──────────────────────────────────────────

    private fun hasAllPermissions(): Boolean {
        // Check MANAGE_EXTERNAL_STORAGE for Android 11+
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            if (!Environment.isExternalStorageManager()) return false
        } else {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.READ_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED) return false
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED) return false
        }

        // Check notification permission for Android 13+
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) return false
        }

        return true
    }

    private fun checkPermissions() {
        if (!hasAllPermissions()) {
            binding.tvInfo.text = "⚠️ Storage and notification permissions are required.\nTap 'Start Server' to grant permissions."
        }
    }

    private fun requestPermissions() {
        val permissions = mutableListOf<String>()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            if (!Environment.isExternalStorageManager()) {
                // Need to open settings for MANAGE_EXTERNAL_STORAGE
                try {
                    val intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION).apply {
                        data = Uri.parse("package:$packageName")
                    }
                    startActivity(intent)
                } catch (_: Exception) {
                    val intent = Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION)
                    startActivity(intent)
                }
                Toast.makeText(this, "Please grant 'All Files Access' permission", Toast.LENGTH_LONG).show()
                return
            }
        } else {
            permissions.add(Manifest.permission.READ_EXTERNAL_STORAGE)
            permissions.add(Manifest.permission.WRITE_EXTERNAL_STORAGE)
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            permissions.add(Manifest.permission.POST_NOTIFICATIONS)
        }

        if (permissions.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, permissions.toTypedArray(), PERMISSION_REQUEST_CODE)
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == PERMISSION_REQUEST_CODE) {
            if (grantResults.all { it == PackageManager.PERMISSION_GRANTED }) {
                startPhoneBridge()
            } else {
                Toast.makeText(this, "Permissions required to share storage", Toast.LENGTH_LONG).show()
            }
        }
    }
}
