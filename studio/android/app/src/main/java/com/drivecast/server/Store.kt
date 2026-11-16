package com.drivecast.server

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

/** Small JSON files in the app folder. Both processes (screen + server) read them. */
object Store {
    private fun file(ctx: Context, name: String) = File(ctx.filesDir, name)

    @Synchronized
    fun read(ctx: Context, name: String): JSONObject = try {
        JSONObject(file(ctx, name).readText())
    } catch (e: Exception) {
        JSONObject()
    }

    @Synchronized
    fun write(ctx: Context, name: String, obj: JSONObject) {
        val tmp = file(ctx, "$name.tmp")
        tmp.writeText(obj.toString())
        tmp.renameTo(file(ctx, name))
    }

    fun prefs(ctx: Context) = read(ctx, "prefs.json")

    fun setPref(ctx: Context, key: String, value: Any?) {
        val p = prefs(ctx)
        if (value == null) p.remove(key) else p.put(key, value)
        write(ctx, "prefs.json", p)
    }

    fun running(ctx: Context) = prefs(ctx).optBoolean("running", true)
    fun tunnelKey(ctx: Context): String? = prefs(ctx).optString("tunnel_key", "").ifBlank { null }
    fun tunnelHost(ctx: Context): String = prefs(ctx).optString("tunnel_host", "drivecast").ifBlank { "drivecast" }

    fun tunnelState(ctx: Context) = read(ctx, "tunnel.json")

    fun setTunnelState(ctx: Context, state: String, url: String?, error: String?) {
        val o = tunnelState(ctx)
        o.put("state", state)
        o.put("hostname", tunnelHost(ctx))
        if (url != null) o.put("url", url)
        if (state != "needs_login") o.remove("auth_url")
        if (error != null) o.put("error", error) else if (state == "online") o.remove("error")
        o.put("at", System.currentTimeMillis() / 1000)
        write(ctx, "tunnel.json", o)
    }
}

/** Calls to the local Python server on 127.0.0.1. Never throws. */
object Local {
    const val PORT = 8765
    const val BASE = "http://127.0.0.1:$PORT"

    fun post(path: String, body: JSONObject): Boolean = try {
        val c = URL(BASE + path).openConnection() as HttpURLConnection
        c.connectTimeout = 3000
        c.readTimeout = 5000
        c.requestMethod = "POST"
        c.doOutput = true
        c.setRequestProperty("Content-Type", "application/json")
        OutputStreamWriter(c.outputStream).use { it.write(body.toString()) }
        val ok = c.responseCode in 200..299
        c.disconnect()
        ok
    } catch (e: Exception) {
        false
    }

    fun up(): Boolean = try {
        val c = URL("$BASE/healthz").openConnection() as HttpURLConnection
        c.connectTimeout = 1500
        c.readTimeout = 1500
        val ok = c.responseCode == 200
        c.disconnect()
        ok
    } catch (e: Exception) {
        false
    }
}
