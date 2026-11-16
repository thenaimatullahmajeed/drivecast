package com.drivecast.server

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.Process
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.File
import kotlin.concurrent.thread

/** Foreground service in its own process: runs the Python server and the tunnel. */
class ServerService : Service() {
    private var wake: PowerManager.WakeLock? = null
    private var wifi: WifiManager.WifiLock? = null
    private val tunnel by lazy { Tunnel(this) }
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var started = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                Store.setPref(this, "running", false)
                tunnel.stop()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                main.postDelayed({ Process.killProcess(Process.myPid()) }, 800)
                return START_NOT_STICKY
            }
            ACTION_TUNNEL -> if (started) tunnel.restart()
        }
        Store.setPref(this, "running", true)
        goForeground()
        if (!started) {
            started = true
            takeLocks()
            thread(name = "dc-python", isDaemon = true) { startPython() }
            tunnel.start()
            main.postDelayed(reporter, 15_000)
        }
        return START_STICKY
    }

    private fun goForeground() {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val n: Notification = NotificationCompat.Builder(this, App.CHANNEL)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("DriveCast server is running")
            .setContentText("Posting continues with the screen off. Tap to open.")
            .setOngoing(true)
            .setContentIntent(open)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(1, n)
        }
    }

    private fun takeLocks() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "DriveCast:server").apply {
            setReferenceCounted(false)
            acquire()
        }
        val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        @Suppress("DEPRECATION")
        val mode = if (Build.VERSION.SDK_INT >= 29) WifiManager.WIFI_MODE_FULL_LOW_LATENCY else WifiManager.WIFI_MODE_FULL_HIGH_PERF
        wifi = wm.createWifiLock(mode, "DriveCast:wifi").apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    private fun startPython() {
        try {
            if (!Python.isStarted()) Python.start(AndroidPlatform(this))
            val data = File(filesDir, "data").absolutePath
            Python.getInstance().getModule("drivecast.main").callAttr("start", data, "127.0.0.1", Local.PORT)
            File(filesDir, "server_error.txt").delete()
        } catch (t: Throwable) {
            File(filesDir, "server_error.txt").writeText(t.toString())
        }
    }

    private val reporter = object : Runnable {
        override fun run() {
            val info = deviceInfo(this@ServerService)
            thread(isDaemon = true) { Local.post("/local/device", info) }
            val url = Store.tunnelState(this@ServerService).optString("url", "")
            if (url.isNotBlank()) thread(isDaemon = true) { Local.post("/local/public-url", JSONObject().put("url", url)) }
            main.postDelayed(this, 5 * 60_000L)
        }
    }

    override fun onDestroy() {
        main.removeCallbacks(reporter)
        try { wake?.release() } catch (_: Exception) {}
        try { wifi?.release() } catch (_: Exception) {}
        super.onDestroy()
    }

    companion object {
        const val ACTION_STOP = "com.drivecast.server.STOP"
        const val ACTION_TUNNEL = "com.drivecast.server.TUNNEL"

        fun start(ctx: Context, action: String? = null) {
            val i = Intent(ctx, ServerService::class.java)
            if (action != null) i.action = action
            ContextCompat.startForegroundService(ctx, i)
        }

        fun stop(ctx: Context) {
            ctx.startService(Intent(ctx, ServerService::class.java).setAction(ACTION_STOP))
        }

        fun deviceInfo(ctx: Context): JSONObject {
            val b = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            val level = b?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
            val scale = b?.getIntExtra(BatteryManager.EXTRA_SCALE, 100) ?: 100
            val temp = (b?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) ?: 0) / 10.0
            val plugged = (b?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
            return JSONObject()
                .put("battery", if (level >= 0) (level * 100 / scale) else null)
                .put("temp", temp)
                .put("charging", plugged)
                .put("model", "${Build.MANUFACTURER} ${Build.MODEL}")
                .put("android", Build.VERSION.RELEASE)
        }
    }
}
