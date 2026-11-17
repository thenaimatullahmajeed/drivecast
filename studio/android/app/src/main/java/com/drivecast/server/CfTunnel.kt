package com.drivecast.server

import android.content.Context
import org.json.JSONObject
import java.io.File
import kotlin.concurrent.thread

/**
 * Own domain through Cloudflare Tunnel (e.g. s1.drivecast.site).
 * Runs the bundled cloudflared with the tunnel token and keeps it alive with back-off.
 * Runs next to the Tailscale link, which stays as a backup way in.
 */
class CfTunnel(private val ctx: Context) {
    @Volatile private var proc: Process? = null
    @Volatile private var stopped = false
    private var worker: Thread? = null
    @Volatile private var backoff = 5_000L
    private val logFile = File(ctx.filesDir, "cf.log")

    fun start() {
        stopped = false
        if (worker?.isAlive == true) return
        worker = thread(name = "dc-cf", isDaemon = true) { loop() }
    }

    fun stop() {
        stopped = true
        proc?.destroy()
    }

    fun restart() {
        backoff = 2_000L
        proc?.destroy()
        start()
    }

    private fun loop() {
        while (!stopped) {
            val token = Store.prefs(ctx).optString("cf_token", "")
            if (token.isBlank()) {
                setState("off", null)
                sleep(5_000)
                continue
            }
            val bin = File(ctx.applicationInfo.nativeLibraryDir, "libcloudflared.so")
            if (!bin.exists()) {
                setState("error", "Cloudflare component missing from this build")
                sleep(60_000)
                continue
            }
            try {
                val pb = ProcessBuilder(bin.absolutePath, "tunnel", "--no-autoupdate", "--loglevel", "info", "run")
                pb.environment()["TUNNEL_TOKEN"] = token // never on the command line
                pb.environment()["HOME"] = ctx.filesDir.absolutePath
                pb.environment()["TMPDIR"] = ctx.cacheDir.absolutePath
                pb.redirectErrorStream(true)
                val p = pb.start()
                proc = p
                setState("starting", null)
                p.inputStream.bufferedReader().forEachLine { handle(it) }
                p.waitFor()
                if (!stopped) setState("reconnecting", null)
            } catch (e: Exception) {
                setState("error", e.message ?: "Cloudflare tunnel failed to start")
            }
            if (!stopped) {
                sleep(backoff)
                backoff = (backoff * 2).coerceAtMost(300_000L)
            }
        }
    }

    private fun handle(line: String) {
        val low = line.lowercase()
        when {
            "registered tunnel connection" in low -> {
                backoff = 5_000L
                setState("online", null)
                announce()
            }
            "unauthorized" in low || "invalid token" in low || "token is not valid" in low || "provided tunnel token is not valid" in low ->
                setState("error", "The tunnel token was not accepted — copy it again from Cloudflare")
            else -> Unit
        }
        appendLog(line)
    }

    /** Tell the panel its public address is now the own domain (sign-in links, redirect URIs). */
    private fun announce() {
        val host = Store.prefs(ctx).optString("cf_host", "")
        if (host.isBlank()) return
        thread(isDaemon = true) {
            repeat(30) {
                if (Local.post(ctx, "/local/own-domain", JSONObject().put("url", "https://$host"))) return@thread
                sleep(2_000)
            }
        }
    }

    private fun setState(state: String, error: String?) {
        val o = Store.read(ctx, "cf.json")
        o.put("state", state).put("at", System.currentTimeMillis())
        if (error != null) o.put("error", error) else o.remove("error")
        o.put("host", Store.prefs(ctx).optString("cf_host", ""))
        Store.write(ctx, "cf.json", o)
    }

    @Synchronized
    private fun appendLog(line: String) {
        try {
            // the token never appears in cloudflared output, but strip anything that looks like one anyway
            logFile.appendText(line.replace(Regex("eyJ[A-Za-z0-9_\\-]{20,}"), "eyJ…").take(600) + "\n")
            if (logFile.length() > 300_000) logFile.writeText(logFile.readLines().takeLast(800).joinToString("\n") + "\n")
        } catch (_: Exception) {
        }
    }

    private fun sleep(ms: Long) = try {
        Thread.sleep(ms)
    } catch (e: InterruptedException) {
    }
}
