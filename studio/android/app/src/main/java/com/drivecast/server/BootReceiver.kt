package com.drivecast.server

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Starts the server after a reboot or an app update, unless the user stopped it. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (Store.running(ctx)) {
            try {
                ServerService.start(ctx)
            } catch (_: Exception) {
            }
        }
    }
}
