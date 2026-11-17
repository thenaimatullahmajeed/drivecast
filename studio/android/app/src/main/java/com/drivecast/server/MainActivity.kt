package com.drivecast.server

import android.Manifest
import android.annotation.SuppressLint
import android.app.KeyguardManager
import android.content.ClipData
import android.content.ClipboardManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationManagerCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import kotlin.concurrent.thread

class MainActivity : AppCompatActivity() {
    private lateinit var web: WebView
    private val main = Handler(Looper.getMainLooper())
    private var fileCb: ValueCallback<Array<Uri>>? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        setContentView(web)
        web.setBackgroundColor(0xFF0E1116.toInt())
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.webChromeClient = object : WebChromeClient() {
            // "Restore a backup" on the phone screen: lets the user pick the .dcbackup file
            override fun onShowFileChooser(view: WebView, cb: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                fileCb?.onReceiveValue(null)
                fileCb = cb
                return try {
                    startActivityForResult(Intent(Intent.ACTION_GET_CONTENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*"), 77)
                    true
                } catch (e: Exception) {
                    fileCb = null
                    false
                }
            }
        }
        web.addJavascriptInterface(Bridge(this), "DC")
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
                val u = req.url
                if (u.host == "127.0.0.1") return false
                startActivity(Intent(Intent.ACTION_VIEW, u)) // the bridge is never exposed to other sites
                return true
            }

            override fun onReceivedError(view: WebView, req: WebResourceRequest, err: WebResourceError) {
                if (req.isForMainFrame) showStarting()
            }
        }
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        // Dedicated phone mode: re-apply its settings each time the app opens (cheap, idempotent)
        if (Phone.isOwner(this) && Store.prefs(this).optBoolean("dedicated")) thread(isDaemon = true) { Phone.applyDedicated(this) }
        // Opening the app always (re)starts the server; "Stop server" only lasts until the app is opened again.
        Store.setPref(this, "running", true)
        ServerService.start(this)
        showStarting()
    }

    @Deprecated("simple file chooser result")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 77) {
            val u = data?.data
            fileCb?.onReceiveValue(if (resultCode == RESULT_OK && u != null) arrayOf(u) else null)
            fileCb = null
        }
    }

    private fun showStarting() {
        val err = File(filesDir, "server_error.txt").takeIf { it.exists() }?.readText()
        val msg = if (err != null) "The server could not start: ${err.take(300)}" else "Starting the server…"
        web.loadDataWithBaseURL(
            null,
            "<html><body style='background:#0E1116;color:#fff;font-family:sans-serif;padding:28px'>" +
                "<h2 style='margin:0'>DriveCast</h2><div style='color:#8E94A0;font-size:12px;letter-spacing:.08em;margin:2px 0 10px'>Studio &#124; By Naimat</div><p style='color:#8E94A0'>${android.text.Html.escapeHtml(msg)}</p></body></html>",
            "text/html", "utf-8", null
        )
        waitForServer()
    }

    private fun waitForServer() {
        thread(isDaemon = true) {
            repeat(900) {
                if (Local.up()) {
                    main.post { web.loadUrl("${Local.BASE}/phone") }
                    return@thread
                }
                Thread.sleep(1000)
            }
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 2) {
            // tell the running server about the shared folder right away (no restart needed)
            Phone.mediaDir(this)?.let { dir ->
                thread(isDaemon = true) { Local.post(this, "/local/media-dir", JSONObject().put("path", dir)) }
            }
            web.evaluateJavascript("typeof draw==='function'&&draw()", null)
        }
    }

    override fun onResume() {
        super.onResume()
        if (::web.isInitialized) web.evaluateJavascript("typeof draw==='function'&&draw()", null)
    }

    /** Functions the phone screen can call. Only the local page is ever loaded. */
    class Bridge(private val act: MainActivity) {
        private val ctx: Context = act.applicationContext

        @JavascriptInterface
        fun checks(): String {
            val pm = ctx.getSystemService(Context.POWER_SERVICE) as PowerManager
            val km = ctx.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
            val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val caps = cm.getNetworkCapabilities(cm.activeNetwork)
            val dev = ServerService.deviceInfo(ctx)
            val confirmed = Store.prefs(ctx).optJSONObject("confirmed") ?: JSONObject()
            val tunnel = Store.tunnelState(ctx)
            val items = JSONArray()
            fun add(id: String, ok: Boolean, title: String, help: String, manual: Boolean = false) =
                items.put(JSONObject().put("id", id).put("ok", ok).put("title", title).put("help", help).put("manual", manual))
            add("battery", pm.isIgnoringBatteryOptimizations(ctx.packageName), "Battery: Unrestricted", "Android won’t stop the server")
            add("notify", NotificationManagerCompat.from(ctx).areNotificationsEnabled(), "Notifications allowed", "Shows the “server running” notice")
            add("autostart", confirmed.optBoolean("autostart"), "Auto-start after reboot", "Allow auto-start / background activity for this app", true)
            add("lock", !km.isDeviceSecure, "Screen lock: None or Swipe", "A PIN stops the server from starting after a reboot")
            add("wifi", caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true, "Connected to Wi-Fi", "Keep Wi-Fi on all the time")
            add("power", dev.optBoolean("charging"), "Charger connected", "Use a wall charger, not a power bank")
            add("chargelimit", confirmed.optBoolean("chargelimit"), "Charging limit 80–85%", "Protects the battery on 24/7 charge", true)
            add("tunnel", tunnel.optString("state") == "online", "Public link connected", "Gives you a fixed https link for your laptop")
            add("storage", Phone.storageGranted(ctx), "Storage folder allowed", "Instagram downloads go to “${Phone.FOLDER}” (hidden from Gallery)")
            return JSONObject()
                .put("items", items)
                .put("running", Store.running(ctx))
                .put("tunnel", tunnel)
                .put("device", dev)
                .toString()
        }

        @JavascriptInterface
        fun fix(id: String) = act.runOnUiThread {
            val pkg = Uri.parse("package:${ctx.packageName}")
            val tries: List<Intent> = when (id) {
                "battery" -> listOf(
                    Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, pkg),
                    Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
                )
                "notify" -> listOf(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, ctx.packageName))
                "autostart" -> autostartIntents() + Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, pkg)
                "lock" -> listOf(Intent(Settings.ACTION_SECURITY_SETTINGS))
                "wifi" -> listOf(Intent(Settings.ACTION_WIFI_SETTINGS))
                "power", "chargelimit" -> listOf(Intent(Intent.ACTION_POWER_USAGE_SUMMARY), Intent(Settings.ACTION_BATTERY_SAVER_SETTINGS))
                "tunnel" -> listOf(Intent(Intent.ACTION_VIEW, Uri.parse("https://login.tailscale.com/admin/settings/keys")))
                "storage" -> {
                    ActivityCompat.requestPermissions(act, arrayOf(Manifest.permission.WRITE_EXTERNAL_STORAGE, Manifest.permission.READ_EXTERNAL_STORAGE), 2)
                    emptyList()
                }
                else -> emptyList()
            }
            for (i in tries) {
                try {
                    act.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    return@runOnUiThread
                } catch (_: Exception) {
                }
            }
        }

        private fun autostartIntents(): List<Intent> = listOf(
            "com.miui.securitycenter" to "com.miui.permcenter.autostart.AutoStartManagementActivity",
            "com.coloros.safecenter" to "com.coloros.safecenter.permission.startup.StartupAppListActivity",
            "com.oplus.safecenter" to "com.oplus.safecenter.permission.startup.StartupAppListActivity",
            "com.vivo.permissionmanager" to "com.vivo.permissionmanager.activity.BgStartUpManagerActivity",
            "com.iqoo.secure" to "com.iqoo.secure.ui.phoneoptimize.AddWhiteListActivity",
            "com.huawei.systemmanager" to "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity",
            "com.transsion.phonemaster" to "com.cyin.himgr.autostart.AutoStartActivity",
            "com.samsung.android.lool" to "com.samsung.android.sm.ui.battery.BatteryActivity",
        ).map { (p, c) -> Intent().setComponent(ComponentName(p, c)) }

        @JavascriptInterface
        fun localToken(): String = Local.token(ctx)

        @JavascriptInterface
        fun modes(): String = Phone.status(ctx).toString()

        @JavascriptInterface
        fun quiet(on: Boolean): Boolean = Phone.setQuiet(ctx, on)

        @JavascriptInterface
        fun dedicated(on: Boolean): String = (if (on) Phone.applyDedicated(ctx) else Phone.releaseDedicated(ctx)).toString()

        /** Erase this server: accounts, channels, settings and its public link identity. */
        @JavascriptInterface
        fun factoryReset() {
            thread(isDaemon = true) {
                ServerService.stop(ctx)
                Thread.sleep(3000) // let the server process exit before removing its files
                listOf("data", "ts", "prefs.json", "tunnel.json", "tunnel.log", "server_error.txt").forEach {
                    File(ctx.filesDir, it).deleteRecursively()
                }
                ctx.cacheDir.listFiles()?.forEach { it.deleteRecursively() }
                Store.setPref(ctx, "running", true)
                ServerService.start(ctx)
                act.runOnUiThread { act.showStarting() }
            }
        }

        @JavascriptInterface
        fun tunnelLog(): String = try {
            val f = File(ctx.filesDir, "tunnel.log")
            if (f.exists()) f.readLines().takeLast(80).joinToString("\n") else "No tunnel output yet."
        } catch (e: Exception) {
            e.toString()
        }

        @JavascriptInterface
        fun confirm(id: String) {
            val p = Store.prefs(ctx)
            val c = p.optJSONObject("confirmed") ?: JSONObject()
            c.put(id, true)
            Store.setPref(ctx, "confirmed", c)
        }

        @JavascriptInterface
        fun copy(text: String) = act.runOnUiThread {
            val cb = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cb.setPrimaryClip(ClipData.newPlainText("DriveCast", text))
        }

        @JavascriptInterface
        fun open(url: String) = act.runOnUiThread {
            if (url.startsWith("https://")) act.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        }

        /** Own domain through Cloudflare: token from Cloudflare (Tunnels → install command), host like s1.drivecast.site. */
        @JavascriptInterface
        fun setCloudflare(token: String, host: String) {
            val raw = token.trim()
            val t = if (raw == "__keep__") Store.prefs(ctx).optString("cf_token", "") else raw.substringAfterLast(" ").trim()
            val h = host.trim().lowercase().removePrefix("https://").removePrefix("http://").trimEnd('/')
            Store.setPref(ctx, "cf_token", t.ifBlank { null })
            Store.setPref(ctx, "cf_host", h.ifBlank { null })
            if (t.isBlank()) Local.post(ctx, "/local/own-domain", JSONObject().put("url", ""))
            ServerService.start(ctx, ServerService.ACTION_CF)
        }

        @JavascriptInterface
        fun cloudflare(): String {
            val o = Store.read(ctx, "cf.json")
            o.put("configured", Store.prefs(ctx).optString("cf_token", "").isNotBlank())
            o.put("host", Store.prefs(ctx).optString("cf_host", ""))
            return o.toString()
        }

        @JavascriptInterface
        fun cfLog(): String = try {
            val f = File(ctx.filesDir, "cf.log")
            if (f.exists()) f.readLines().takeLast(60).joinToString("\n") else "No output yet."
        } catch (e: Exception) {
            e.toString()
        }

        @JavascriptInterface
        fun setTunnel(key: String, host: String) {
            val clean = host.lowercase().replace(Regex("[^a-z0-9-]"), "-").trim('-').ifBlank { "drivecast" }.take(40)
            File(ctx.filesDir, "ts").deleteRecursively() // a new key means a fresh Tailscale login
            Store.setPref(ctx, "tunnel_key", key.trim().ifBlank { null })
            Store.setPref(ctx, "tunnel_wanted", true)
            Store.setPref(ctx, "tunnel_host", clean)
            Store.setTunnelState(ctx, "starting", null, null)
            ServerService.start(ctx, ServerService.ACTION_TUNNEL)
        }

        @JavascriptInterface
        fun stop() {
            ServerService.stop(ctx)
        }

        @JavascriptInterface
        fun start() {
            Store.setPref(ctx, "running", true)
            ServerService.start(ctx)
            act.runOnUiThread { act.showStarting() }
        }
    }
}
