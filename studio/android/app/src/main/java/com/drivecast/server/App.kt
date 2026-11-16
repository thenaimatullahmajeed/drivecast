package com.drivecast.server

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "Server status", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Shows that the DriveCast server is running"
                setShowBadge(false)
            }
        )
    }

    companion object {
        const val CHANNEL = "server"
    }
}
