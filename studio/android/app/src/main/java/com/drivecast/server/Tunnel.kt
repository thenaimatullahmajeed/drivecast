package com.drivecast.server

import android.content.Context
import org.json.JSONObject
import java.io.File
import kotlin.concurrent.thread

/** Runs the bundled Tailscale Funnel binary and keeps it alive with back-off. */
class Tunnel(private val ctx: Context) {
    @Volatile private var proc: Process? = null
    @Volatile private var stopped = false
    private var worker: Thread? = null
    @Volatile private var backoff = 5_000L

    fun start() {
        stopped = false
        if (worker?.isAlive == true) return
        worker = thread(name = "dc-tunnel", isDaemon = true) { loop() }
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
            val key = Store.tunnelKey(ctx)
            val hasState = File(ctx.filesDir, "ts/tailscaled.state").exists()
            val wanted = Store.prefs(ctx).optBoolean("tunnel_wanted", false)
            if (key == null && !hasState && !wanted) {
                Store.setTunnelState(ctx, "needs_key", null, null)
                sleep(5_000)
                continue
            }
            val bin = File(ctx.applicationInfo.nativeLibraryDir, "libdctunnel.so")
            if (!bin.exists()) {
                Store.setTunnelState(ctx, "error", null, "Tunnel component missing from this build")
                sleep(60_000)
                continue
            }
            val dir = File(ctx.filesDir, "ts").apply { mkdirs() }
            try {
                val pb = ProcessBuilder(
                    bin.absolutePath, "-dir", dir.absolutePath,
                    "-hostname", Store.tunnelHost(ctx), "-target", Local.BASE
                )
                pb.environment()["TS_AUTHKEY"] = key ?: ""
                pb.environment()["HOME"] = ctx.filesDir.absolutePath
                pb.environment()["TMPDIR"] = ctx.cacheDir.absolutePath
                pb.redirectErrorStream(true)
                val p = pb.start()
                proc = p
                Store.setTunnelState(ctx, "starting", null, null)
                p.inputStream.bufferedReader().forEachLine { handle(it) }
                p.waitFor()
            } catch (e: Exception) {
                Store.setTunnelState(ctx, "error", null, e.message ?: "Tunnel failed to start")
            }
            if (!stopped) {
                sleep(backoff)
                backoff = (backoff * 2).coerceAtMost(300_000L)
            }
        }
    }

    private fun handle(line: String) {
        when {
            line.startsWith("PUBLIC_URL=") -> {
                val url = line.removePrefix("PUBLIC_URL=").trim()
                Store.setTunnelState(ctx, "online", url, null)
                backoff = 5_000L
                // the panel may still be starting; retry a few times
                thread(isDaemon = true) {
                    repeat(30) {
                        if (Local.post(ctx, "/local/public-url", JSONObject().put("url", url))) return@thread
                        sleep(2_000)
                    }
                }
            }
            line.startsWith("AUTH_URL=") -> {
                val o = Store.tunnelState(ctx)
                o.put("state", "needs_login").put("auth_url", line.removePrefix("AUTH_URL=").trim()).remove("error")
                Store.write(ctx, "tunnel.json", o)
            }
            line.startsWith("ERROR=") -> Store.setTunnelState(ctx, "error", null, line.removePrefix("ERROR="))
            line.startsWith("STATE=") -> {
                val s = line.removePrefix("STATE=").trim()
                if (s != "online" && s != "needs_login") Store.setTunnelState(ctx, s, null, null)
            }
            else -> Unit
        }
        appendLog(line)
    }

    private val logFile = File(ctx.filesDir, "tunnel.log")

    @Synchronized
    private fun appendLog(line: String) {
        try {
            logFile.appendText(line.take(600) + "\n")
            if (logFile.length() > 300_000) {
                val keep = logFile.readLines().takeLast(800)
                logFile.writeText(keep.joinToString("\n") + "\n")
            }
        } catch (_: Exception) {
        }
    }

    private fun sleep(ms: Long) = try {
        Thread.sleep(ms)
    } catch (e: InterruptedException) {
    }
}
