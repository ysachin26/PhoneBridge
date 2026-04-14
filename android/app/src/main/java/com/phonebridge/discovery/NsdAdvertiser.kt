package com.phonebridge.discovery

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import android.util.Log

/**
 * Advertises the PhoneBridge WebDAV server via mDNS/DNS-SD
 * so the PC app can discover this phone on the network.
 */
class NsdAdvertiser(private val context: Context) {

    companion object {
        private const val TAG = "NsdAdvertiser"
        private const val SERVICE_TYPE = "_phonebridge._tcp."
    }

    private var nsdManager: NsdManager? = null
    private var registrationListener: NsdManager.RegistrationListener? = null
    private var isRegistered = false

    /**
     * Register the PhoneBridge service for discovery.
     *
     * @param port The port the WebDAV server is running on
     * @param deviceName A human-readable name for this device
     * @param authRequired Whether Basic Auth is required to connect
     * @param authUser The username for Basic Auth (if required)
     * @param protocol The protocol ("http" or "https")
     */
    fun register(
        port: Int,
        deviceName: String,
        authRequired: Boolean = true,
        authUser: String = "phonebridge",
        protocol: String = "https",
        tailscaleIp: String? = null
    ) {
        if (isRegistered) {
            Log.w(TAG, "Already registered — unregister first")
            return
        }

        nsdManager = context.getSystemService(Context.NSD_SERVICE) as NsdManager

        val serviceInfo = NsdServiceInfo().apply {
            serviceName = deviceName
            serviceType = SERVICE_TYPE
            setPort(port)

            // Add TXT records with metadata
            setAttribute("version", com.phonebridge.server.ServerConfig.VERSION)
            setAttribute("deviceName", deviceName)
            setAttribute("model", Build.MODEL)
            setAttribute("brand", Build.BRAND)
            setAttribute("sdk", Build.VERSION.SDK_INT.toString())

            // Auth and protocol TXT records
            setAttribute("auth_required", authRequired.toString())
            setAttribute("auth_user", authUser)
            setAttribute("protocol", protocol)

            // Remote access: Tailscale IP (if detected)
            if (!tailscaleIp.isNullOrEmpty()) {
                setAttribute("tailscale_ip", tailscaleIp)
                Log.i(TAG, "🌐 Advertising Tailscale IP in mDNS: $tailscaleIp")
            }
        }

        registrationListener = object : NsdManager.RegistrationListener {
            override fun onServiceRegistered(info: NsdServiceInfo) {
                isRegistered = true
                Log.i(TAG, "✅ mDNS service registered: ${info.serviceName}")
            }

            override fun onRegistrationFailed(info: NsdServiceInfo, errorCode: Int) {
                isRegistered = false
                Log.e(TAG, "❌ mDNS registration failed (error $errorCode)")
            }

            override fun onServiceUnregistered(info: NsdServiceInfo) {
                isRegistered = false
                Log.i(TAG, "mDNS service unregistered: ${info.serviceName}")
            }

            override fun onUnregistrationFailed(info: NsdServiceInfo, errorCode: Int) {
                Log.e(TAG, "❌ mDNS unregistration failed (error $errorCode)")
            }
        }

        try {
            nsdManager?.registerService(
                serviceInfo,
                NsdManager.PROTOCOL_DNS_SD,
                registrationListener
            )
            Log.i(TAG, "Registering mDNS service: $deviceName on port $port (protocol=$protocol, auth=$authRequired)")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to register mDNS service", e)
        }
    }

    /**
     * Unregister the mDNS service.
     */
    fun unregister() {
        if (!isRegistered || registrationListener == null) {
            return
        }

        try {
            nsdManager?.unregisterService(registrationListener)
        } catch (e: Exception) {
            Log.e(TAG, "Error unregistering mDNS service", e)
        } finally {
            isRegistered = false
            registrationListener = null
        }
    }

    fun isRegistered(): Boolean = isRegistered
}

