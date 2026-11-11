package org.rout3;

import android.app.Application;
import android.os.Build;
import android.text.TextUtils;

/**
 * The earliest Java code the app runs, before any activity is instantiated.
 *
 * <p>Selected by {@code android:name="org.rout3.DiagApp"} on the
 * {@code <application>} tag ({@code tools/android/manifest_application.xml}).
 * That position matters: a failure while the framework loads, verifies or
 * instantiates the launcher activity -- a missing class, a broken native library
 * needed during class verification, an {@code ExceptionInInitializerError} --
 * happens before {@code DiagActivity.onCreate} and would otherwise leave no trace
 * at all on a phone without {@code adb}.  Here the journal is already open and the
 * uncaught-exception handler is already installed, so that failure is written
 * down with the device facts next to it.
 *
 * <p>Kept deliberately tiny: this class runs so early that anything it throws
 * becomes "Unable to instantiate application" and the app never starts.  Every
 * risky call is wrapped.
 */
public class DiagApp extends Application {
    @Override
    public void onCreate() {
        super.onCreate();
        DiagLog.init(this);
        DiagLog.installHandler(this);

        String version = "?";
        try {
            version = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Throwable ignored) {
            // A missing package record must not stop the journal.
        }
        String abis = "?";
        try {
            abis = TextUtils.join(",", Build.SUPPORTED_ABIS);
        } catch (Throwable ignored) {
            // Very old devices have no SUPPORTED_ABIS; the field is optional.
        }
        DiagLog.mark("application: created"
                + " | version " + version
                + " | " + Build.MANUFACTURER + " " + Build.MODEL
                + " | Android " + Build.VERSION.RELEASE + " (API " + Build.VERSION.SDK_INT + ")"
                + " | abis: " + abis);
    }
}
