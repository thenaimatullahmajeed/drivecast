package com.drivecast.server

import android.Manifest
import android.app.NotificationManager
import android.app.admin.DeviceAdminReceiver
import android.app.admin.DevicePolicyManager
import android.app.admin.SystemUpdatePolicy
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.os.Build
import android.os.Environment
import android.provider.Settings
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Device Owner receiver. Activated once with:
 *  adb shell dpm set-device-owner com.drivecast.server/.AdminReceiver */
class AdminReceiver : DeviceAdminReceiver()

/** Phone-level helpers: the shared media folder, Quiet mode and Dedicated phone mode. */
object Phone {
    const val FOLDER = "Shared with my tool DriveCast"

    fun storageGranted(ctx: Context): Boolean =
        ctx.checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED

    /** The visible phone folder when allowed (hidden from the Gallery by .nomedia), else the app's private folder. */
    fun mediaDir(ctx: Context): String? {
        if (!storageGranted(ctx)) return null
        @Suppress("DEPRECATION")
        val d = File(Environment.getExternalStorageDirectory(), FOLDER)
        return try {
            d.mkdirs()
            File(d, ".nomedia").createNewFile()
            if (d.canWrite()) d.absolutePath else null
        } catch (e: Exception) {
            null
        }
    }

    fun ffmpeg(ctx: Context): String? {
        val f = File(ctx.applicationInfo.nativeLibraryDir, "libffmpeg.so")
        return if (f.exists()) f.absolutePath else null
    }

    // ---------------------------------------------------------------- Quiet mode
    fun quietAccess(ctx: Context): Boolean =
        (ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).isNotificationPolicyAccessGranted

    fun quietOn(ctx: Context): Boolean {
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        return nm.currentInterruptionFilter == NotificationManager.INTERRUPTION_FILTER_NONE
    }

    /** Returns false when the user still has to allow Do Not Disturb access (settings are opened). */
    fun setQuiet(ctx: Context, on: Boolean): Boolean {
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (!nm.isNotificationPolicyAccessGranted) {
            ctx.startActivity(Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return false
        }
        nm.setInterruptionFilter(if (on) NotificationManager.INTERRUPTION_FILTER_NONE else NotificationManager.INTERRUPTION_FILTER_ALL)
        Store.setPref(ctx, "quiet", on)
        return true
    }

    // ---------------------------------------------------------------- Dedicated phone mode (Device Owner)
    private fun dpm(ctx: Context) = ctx.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
    private fun admin(ctx: Context) = ComponentName(ctx, AdminReceiver::class.java)

    fun isOwner(ctx: Context): Boolean = dpm(ctx).isDeviceOwnerApp(ctx.packageName)

    /** Apps that would be paused (user-installed, not us). System apps are never touched. */
    private fun otherApps(ctx: Context): List<String> = ctx.packageManager.getInstalledApplications(0)
        .filter { (it.flags and ApplicationInfo.FLAG_SYSTEM) == 0 && (it.flags and ApplicationInfo.FLAG_UPDATED_SYSTEM_APP) == 0 }
        .map { it.packageName }
        .filter { it != ctx.packageName }

    fun applyDedicated(ctx: Context): JSONObject {
        val out = JSONObject()
        if (!isOwner(ctx)) return out.put("ok", false).put("error", "Dedicated mode is not switched on yet — follow the guide (one command from a laptop).")
        val d = dpm(ctx)
        val a = admin(ctx)
        val pkg = ctx.packageName
        val done = JSONArray()
        fun step(name: String, fn: () -> Unit) = try {
            fn(); done.put(name)
        } catch (e: Exception) {
            out.put("warn_$name", e.message ?: e.toString())
        }
        step("permissions") {
            val perms = mutableListOf(Manifest.permission.WRITE_EXTERNAL_STORAGE, Manifest.permission.READ_EXTERNAL_STORAGE)
            if (Build.VERSION.SDK_INT >= 33) perms += Manifest.permission.POST_NOTIFICATIONS
            perms.forEach { d.setPermissionGrantState(a, pkg, it, DevicePolicyManager.PERMISSION_GRANT_STATE_GRANTED) }
        }
        step("stay_awake") { d.setGlobalSetting(a, Settings.Global.STAY_ON_WHILE_PLUGGED_IN, "7") }
        step("no_lockscreen") { d.setKeyguardDisabled(a, true) }
        step("night_updates") { d.setSystemUpdatePolicy(a, SystemUpdatePolicy.createWindowedInstallPolicy(120, 300)) }
        step("no_uninstall") { d.setUninstallBlocked(a, pkg, true) }
        step("pause_other_apps") {
            val others = otherApps(ctx)
            val failed = d.setPackagesSuspended(a, others.toTypedArray(), true).toSet()
            Store.setPref(ctx, "suspended", JSONArray(others.filter { it !in failed }))
        }
        Store.setPref(ctx, "dedicated", true)
        return out.put("ok", true).put("done", done)
    }

    fun releaseDedicated(ctx: Context): JSONObject {
        val out = JSONObject()
        if (!isOwner(ctx)) return out.put("ok", true)
        val d = dpm(ctx)
        val a = admin(ctx)
        val pkg = ctx.packageName
        try {
            val s = Store.prefs(ctx).optJSONArray("suspended") ?: JSONArray()
            val list = (0 until s.length()).map { s.getString(it) }
            if (list.isNotEmpty()) d.setPackagesSuspended(a, list.toTypedArray(), false)
        } catch (_: Exception) {
        }
        try { d.setUninstallBlocked(a, pkg, false) } catch (_: Exception) {}
        try { d.setKeyguardDisabled(a, false) } catch (_: Exception) {}
        try { d.setSystemUpdatePolicy(a, null) } catch (_: Exception) {}
        try { d.setGlobalSetting(a, Settings.Global.STAY_ON_WHILE_PLUGGED_IN, "0") } catch (_: Exception) {}
        try {
            @Suppress("DEPRECATION")
            d.clearDeviceOwnerApp(pkg)
        } catch (e: Exception) {
            out.put("warn", e.message)
        }
        Store.setPref(ctx, "dedicated", false)
        Store.setPref(ctx, "suspended", JSONArray())
        return out.put("ok", true)
    }

    fun status(ctx: Context): JSONObject = JSONObject()
        .put("storage", storageGranted(ctx))
        .put("media_dir", mediaDir(ctx))
        .put("ffmpeg", ffmpeg(ctx) != null)
        .put("quiet_access", quietAccess(ctx))
        .put("quiet", quietOn(ctx))
        .put("dedicated", isOwner(ctx))
        .put("dedicated_applied", Store.prefs(ctx).optBoolean("dedicated"))
        .put("paused_apps", (Store.prefs(ctx).optJSONArray("suspended") ?: JSONArray()).length())
        .put("command", "adb shell dpm set-device-owner ${ctx.packageName}/.AdminReceiver")
}
