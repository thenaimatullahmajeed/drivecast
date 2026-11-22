package com.drivecast.server

import android.app.AlarmManager
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
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/** Foreground service in its own process: runs the Python server and the tunnel. */
class ServerService : Service() {
    private var wake: PowerManager.WakeLock? = null
    private var wifi: WifiManager.WifiLock? = null
    private val tunnel by lazy { Tunnel(this) }
    private val cf by lazy { CfTunnel(this) }
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var started = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                Store.setPref(this, "running", false)
                disarm(this)
                tunnel.stop()
                cf.stop()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                main.postDelayed({ Process.killProcess(Process.myPid()) }, 800)
                return START_NOT_STICKY
            }
            ACTION_TUNNEL -> if (started) tunnel.restart()
            ACTION_CF -> if (started) cf.restart()
        }
        Store.setPref(this, "running", true)
        goForeground()
        if (!started) {
            started = true
            takeLocks()
            thread(name = "dc-python", isDaemon = true) { startPython() }
            tunnel.start()
            cf.start()
            main.postDelayed(reporter, 15_000)
        }
        arm(this, KEEPALIVE_MS)
        return START_STICKY
    }

    private fun goForeground() {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val n: Notification = NotificationCompat.Builder(this, App.CHANNEL)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("DriveCast Studio is on air")
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
            val env = Python.getInstance().getModule("os").get("environ")
            Phone.ffmpeg(this)?.let { env?.callAttr("__setitem__", "DC_FFMPEG", it) }
            Phone.mediaDir(this)?.let { env?.callAttr("__setitem__", "DC_MEDIA_DIR", it) }
            env?.callAttr("__setitem__", "DC_SHELL_API", SHELL_API.toString())
            try { env?.callAttr("__setitem__", "DC_APP_VERSION", packageManager.getPackageInfo(packageName, 0).versionName ?: "") } catch (e: Exception) { }
            val data = File(filesDir, "data").absolutePath
            // dcboot picks the newest verified server code (hot update) or the one inside this APK
            Python.getInstance().getModule("dcboot").callAttr("start", data, "127.0.0.1", Local.PORT)
            File(filesDir, "server_error.txt").delete()
        } catch (t: Throwable) {
            File(filesDir, "server_error.txt").writeText(t.toString())
        }
    }

    private val reporter = object : Runnable {
        override fun run() {
            val info = deviceInfo(this@ServerService)
            thread(isDaemon = true) { Local.post(this@ServerService, "/local/device", info) }
            val url = Store.tunnelState(this@ServerService).optString("url", "")
            if (url.isNotBlank()) thread(isDaemon = true) { Local.post(this@ServerService, "/local/public-url", JSONObject().put("url", url)) }
            if (++ticks % 5 == 0) thread(isDaemon = true) { notifyAlerts() }
            arm(this@ServerService, KEEPALIVE_MS) // if this process ever dies, the alarm brings the server back within minutes
            thread(isDaemon = true) { watchdog() }
            main.postDelayed(this, 60_000L)
        }
    }
    private var ticks = 0
    private var localFails = 0
    private var publicFails = 0
    private val bornAt = System.currentTimeMillis()

    private fun probe(url: String, ms: Int): Boolean = try {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = ms
        c.readTimeout = ms
        val ok = c.responseCode in 200..299
        c.disconnect()
        ok
    } catch (e: Exception) {
        false
    }

    private fun note(msg: String) {
        try { File(filesDir, "watchdog.txt").appendText("${java.util.Date()} $msg\n") } catch (_: Exception) {}
    }

    /** Every minute: is the server answering on the phone, and does the public address really answer? Fix what is not. */
    private fun watchdog() {
        if (System.currentTimeMillis() - bornAt < 180_000) return // give a fresh start time to boot
        if (probe("${Local.BASE}/healthz", 4000)) localFails = 0
        else if (++localFails >= 3) {
            note("local server did not answer 3 times — restarting the whole server process")
            arm(this, 3_000)
            Process.killProcess(Process.myPid())
            return
        }
        val host = Store.prefs(this).optString("cf_host", "")
        if (host.isNotBlank() && Store.prefs(this).optString("cf_token", "").isNotBlank()) {
            if (probe("https://$host/healthz", 8000)) publicFails = 0
            else {
                publicFails++
                if (publicFails == 2 || publicFails % 5 == 0) {
                    note("public address $host did not answer $publicFails times — restarting the Cloudflare tunnel")
                    cf.restart()
                }
                if (publicFails == 4 || publicFails % 10 == 4) {
                    note("still not answering — restarting the Tailscale link too (a stuck VPN is the usual cause)")
                    tunnel.restart()
                }
            }
        }
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        if (Store.running(this)) arm(this, 2_000)
        super.onTaskRemoved(rootIntent)
    }

    /** Owner alerts from the server (phone off, battery, replaced, quota…) as phone notifications, once per problem. */
    private fun notifyAlerts() {
        val res = Local.get(this, "/local/alerts") ?: return
        val arr = res.optJSONArray("alerts") ?: return
        val seen = Store.read(this, "alerts_seen.json")
        val now = JSONObject()
        val nm = getSystemService(NOTIFICATION_SERVICE) as android.app.NotificationManager
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel("dc_alerts") == null) {
            nm.createNotificationChannel(android.app.NotificationChannel("dc_alerts", "DriveCast alerts", android.app.NotificationManager.IMPORTANCE_HIGH))
        }
        val open = PendingIntent.getActivity(this, 2, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        for (i in 0 until arr.length()) {
            val a = arr.getJSONObject(i)
            val key = a.optString("key")
            now.put(key, true)
            if (seen.has(key)) continue
            val n = NotificationCompat.Builder(this, "dc_alerts")
                .setSmallIcon(android.R.drawable.stat_notify_error)
                .setContentTitle(a.optString("title"))
                .setContentText(a.optString("message"))
                .setStyle(NotificationCompat.BigTextStyle().bigText(a.optString("message")))
                .setContentIntent(open)
                .setAutoCancel(true)
                .build()
            try { nm.notify(1000 + (key.hashCode() and 0xffff), n) } catch (_: Exception) {}
        }
        Store.write(this, "alerts_seen.json", now)
    }

    override fun onDestroy() {
        main.removeCallbacks(reporter)
        try { wake?.release() } catch (_: Exception) {}
        try { wifi?.release() } catch (_: Exception) {}
        super.onDestroy()
    }

    companion object {
        const val ACTION_STOP = "com.drivecast.server.STOP"
        const val ACTION_KEEPALIVE = "com.drivecast.server.KEEPALIVE"
        const val KEEPALIVE_MS = 5 * 60_000L

        private fun alarmIntent(ctx: Context) = PendingIntent.getBroadcast(
            ctx, 9, Intent(ctx, BootReceiver::class.java).setAction(ACTION_KEEPALIVE),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        /** One alarm that restarts the server if it is not running when it fires; every healthy cycle pushes it forward. */
        fun arm(ctx: Context, delayMs: Long) {
            try {
                val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
                am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, System.currentTimeMillis() + delayMs, alarmIntent(ctx))
            } catch (_: Exception) {}
        }

        fun disarm(ctx: Context) {
            try { (ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager).cancel(alarmIntent(ctx)) } catch (_: Exception) {}
        }
        const val ACTION_TUNNEL = "com.drivecast.server.TUNNEL"
        const val ACTION_CF = "com.drivecast.server.CF"
        /** Bump when the Android shell changes in a way server updates depend on (hot updates check it). */
        const val SHELL_API = 2

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
                .put("quiet", try { Phone.quietOn(ctx) } catch (e: Exception) { false })
                .put("dedicated", try { Phone.isOwner(ctx) } catch (e: Exception) { false })
                .put("storage", Phone.storageGranted(ctx))
                .put("ffmpeg", Phone.ffmpeg(ctx) != null)
        }
    }
}
