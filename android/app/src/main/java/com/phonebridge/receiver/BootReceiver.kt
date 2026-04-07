package com.phonebridge.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import com.phonebridge.service.PhoneBridgeService

/**
 * BroadcastReceiver that listens for BOOT_COMPLETED to auto-start
 * the PhoneBridge server when the phone boots.
 *
 * Only starts the service if the user has enabled the auto-start
 * toggle in the app settings (stored in SharedPreferences).
 */
class BootReceiver : BroadcastReceiver() {

    companion object {
        private const val TAG = "BootReceiver"
        const val PREFS_NAME = "phonebridge_prefs"
        const val PREF_AUTO_START = "auto_start_on_boot"
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return

        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val autoStart = prefs.getBoolean(PREF_AUTO_START, false)

        if (!autoStart) {
            Log.i(TAG, "Boot completed, but auto-start is disabled — skipping")
            return
        }

        Log.i(TAG, "Boot completed — auto-starting PhoneBridge server")

        val serviceIntent = Intent(context, PhoneBridgeService::class.java).apply {
            action = PhoneBridgeService.ACTION_START
        }

        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(serviceIntent)
            } else {
                context.startService(serviceIntent)
            }
            Log.i(TAG, "✅ PhoneBridge service started on boot")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start service on boot", e)
        }
    }
}
