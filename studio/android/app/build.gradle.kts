plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

val versionNameProp = (project.findProperty("dcVersionName") as String?) ?: "1.0.0"
val versionCodeProp = ((project.findProperty("dcVersionCode") as String?) ?: "1").toInt()

android {
    namespace = "com.drivecast.server"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.drivecast.server"
        minSdk = 26
        targetSdk = 29 // keeps netlink access for the Tailscale tunnel (Android blocks it for apps targeting 30+)
        versionCode = versionCodeProp
        versionName = versionNameProp
        ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a") }
    }

    signingConfigs {
        create("release") {
            val ks = System.getenv("DC_KEYSTORE_FILE")
            if (ks != null && file(ks).exists()) {
                storeFile = file(ks)
                storePassword = System.getenv("DC_KEYSTORE_PASS")
                keyAlias = System.getenv("DC_KEY_ALIAS") ?: "drivecast"
                keyPassword = System.getenv("DC_KEYSTORE_PASS")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            val rel = signingConfigs.getByName("release")
            signingConfig = if (rel.storeFile != null) rel else signingConfigs.getByName("debug")
        }
    }

    lint {
        // Sideloaded app: the Play Store target-SDK rule does not apply.
        disable += setOf("ExpiredTargetSdkVersion", "OldTargetApi")
        checkReleaseBuilds = false
    }

    packaging {
        jniLibs { useLegacyPackaging = true } // keep the tunnel binary extracted and executable
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

chaquopy {
    defaultConfig {
        version = "3.11"
        pip {
            install("requests")
            install("PySocks")
            install("tzdata")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir("../../server")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
}
