package org.rout3;

import android.os.Build;
import android.os.Bundle;
import android.text.TextUtils;
import android.widget.FrameLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.File;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.util.Arrays;

/**
 * The launcher activity, wrapped so a failure before Python leaves a report.
 *
 * <p>python-for-android's activity does its real work in {@code onCreate}: it builds
 * its resource manager, calls {@code super.onCreate} (which loads every native
 * library), and only then shows the presplash.  A failure anywhere in that window
 * closes the window with no splash and no dialog, and because Python has not started
 * yet the boot marker in {@code client/fatal.py} does not exist either -- on a phone
 * without {@code adb} that is a completely silent failure.
 *
 * <p>This subclass exists for exactly that window.  It runs {@code super.onCreate}
 * inside {@code Throwable}, so:
 *
 * <ul>
 *   <li>a failure is written to the journal in {@link DiagLog} (visible at
 *       {@code Android/data/org.rout3.rout3/files/rout3-java.log} over USB, no
 *       permission needed), mirrored to app-private storage and logcat;</li>
 *   <li>the stack trace is shown on screen as selectable white-on-black text, so it
 *       can be read, copied or photographed instead of the window simply
 *       vanishing;</li>
 *   <li>the device facts we cannot get without {@code adb} -- model, Android
 *       version, ABI list, APK version, native library directory -- are recorded
 *       on every launch (the application class does it even earlier);</li>
 *   <li>when SDL reports {@code mBrokenLibraries} it names the library that
 *       failed, because SDL's own dialog only carries the raw linker message.</li>
 * </ul>
 *
 * <p>Everything here is best-effort: the activity behaves exactly like the stock
 * one when nothing fails, and a diagnostic that throws would be a second bug, so
 * every screen is wrapped.
 *
 * <p>Selected by {@code android.entrypoint} in {@code buildozer.spec}; the Java
 * sources live in {@code tools/android/java} and are compiled into the APK, never
 * shipped as files inside it.
 */
public class DiagActivity extends org.kivy.android.PythonActivity {
    /** Set when startup failed: a half-built SDL must not be resumed. */
    private static boolean sFailed;
    /** The text shown the first time; a re-created activity shows it again. */
    private static String sReport;

    @Override
    protected void onCreate(Bundle state) {
        DiagLog.init(this);
        DiagLog.mark("activity: onCreate");

        if (sFailed) {
            // A relaunch from Recents must not call the broken super.onCreate again.
            screen(sReport);
            return;
        }

        DiagLog.mark("launch: " + apkVersion());
        DiagLog.mark("device: " + Build.MANUFACTURER + " " + Build.MODEL
                + " | Android " + Build.VERSION.RELEASE + " (API " + Build.VERSION.SDK_INT + ")"
                + " | abis: " + TextUtils.join(",", Build.SUPPORTED_ABIS));
        DiagLog.mark("apk: " + getApplicationInfo().sourceDir);
        DiagLog.mark("libs: " + libraryDir());

        // Load every library here first, one marker per name.
        //
        // The failure this activity exists for is not necessarily a Java
        // exception: a SIGSEGV inside a library's JNI_OnLoad kills the process
        // between two statements, and no catch can see it.  In that case the last
        // journal line is the only evidence, so it has to name the library being
        // loaded.  The order is PythonUtil's, and the loads PythonActivity
        // performs afterwards are no-ops (same class loader).
        preloadLibraries();

        try {
            DiagLog.mark("PythonActivity.onCreate: enter");
            super.onCreate(state);
            DiagLog.mark("PythonActivity.onCreate: done, brokenLibraries=" + mBrokenLibraries);
        } catch (Throwable failure) {
            fail("PythonActivity.onCreate", failure);
            return;
        }

        if (mBrokenLibraries) {
            // SDL caught the link error itself and showed a bare "Exit" dialog.
            // Ask each name again: this says which one failed and why, next to
            // SDL's raw linker message.
            fail("native libraries failed", new IllegalStateException(probeLibraries()));
            return;
        }

        DiagLog.mark("startup handed to Python");
    }

    // PythonActivity keeps unpacking and starting Python after super.onCreate, so
    // these guards only keep a half-built SDL from being started or resumed.
    @Override
    protected void onStart() {
        if (sFailed) {
            return;
        }
        super.onStart();
    }

    @Override
    protected void onResume() {
        if (sFailed) {
            return;
        }
        super.onResume();
    }

    @Override
    protected void onPause() {
        if (sFailed) {
            return;
        }
        super.onPause();
    }

    @Override
    protected void onStop() {
        if (sFailed) {
            return;
        }
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        DiagLog.mark(sFailed ? "report closed" : "activity destroyed");
        if (sFailed) {
            return;
        }
        super.onDestroy();
    }

    // ── reporting ────────────────────────────────────────────────────

    /** Record a startup failure and keep it on screen. */
    private void fail(String where, Throwable failure) {
        sFailed = true;
        DiagLog.crash(where, failure);
        if (sReport != null) {
            return;
        }
        String detail;
        try {
            detail = trace(failure);
        } catch (Throwable ignored) {
            detail = String.valueOf(failure);
        }
        sReport = "ROUT III не смог запуститься\n\n" + where + "\n\n" + detail
                + "\nжурнал: " + DiagLog.path();
        screen(sReport);
    }

    private static String trace(Throwable failure) {
        StringWriter buffer = new StringWriter();
        failure.printStackTrace(new PrintWriter(buffer));
        return buffer.toString();
    }

    /** The traceback as selectable text: a crash that can be photographed is a bug report. */
    private void screen(String text) {
        if (text == null) {
            text = "ROUT III не смог запуститься\n\nжурнал: " + DiagLog.path();
        }
        try {
            TextView view = new TextView(this);
            view.setText(text);
            view.setTextIsSelectable(true);
            view.setTextSize(11f);
            view.setTextColor(0xFFFFFFFF);
            view.setBackgroundColor(0xFF000000);
            int pad = (int) (12 * getResources().getDisplayMetrics().density);
            view.setPadding(pad, pad, pad, pad);
            ScrollView scroll = new ScrollView(this);
            scroll.setBackgroundColor(0xFF000000);
            // ScrollView is a FrameLayout: it has no LayoutParams of its own.
            scroll.addView(view, new FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.WRAP_CONTENT));
            setContentView(scroll);
        } catch (Throwable ignored) {
            // A display that cannot draw is already in the journal.
        }
    }

    // ── facts ────────────────────────────────────────────────────────

    /**
     * The libraries python-for-android loads, in its own order.
     *
     * Kept here as well because the value is a fact about the APK, and the
     * probe must name exactly the files that exist in it.
     */
    private static final String[] LIBRARIES = {
        "sqlite3", "ffi", "png16", "ssl", "crypto",
        "SDL2", "SDL2_image", "SDL2_mixer", "SDL2_ttf",
        "python3.12", "main",
    };

    /**
     * Load each library before the activity does, logging the name first.
     *
     * A Java-level failure is recorded and the loop continues (the journal then
     * lists every missing file); a native crash stops here, and the last line
     * names the library that caused it.
     */
    private void preloadLibraries() {
        for (String name : LIBRARIES) {
            DiagLog.mark("native: loading " + name);
            try {
                System.loadLibrary(name);
            } catch (Throwable failure) {
                DiagLog.mark("native: " + name + " failed: " + failure);
            }
        }
        DiagLog.mark("native: load pass finished");
    }

    private String probeLibraries() {
        StringBuilder out = new StringBuilder();
        for (String name : LIBRARIES) {
            try {
                System.loadLibrary(name);
                out.append("ok      ").append(name).append('\n');
            } catch (Throwable failure) {
                out.append("ОШИБКА  ").append(name).append(": ")
                        .append(failure.getMessage() == null ? String.valueOf(failure)
                                : failure.getMessage())
                        .append('\n');
            }
        }
        if (out.length() == 0) {
            out.append("нет данных");
        }
        return out.toString();
    }

    private String libraryDir() {
        try {
            File dir = new File(getApplicationInfo().nativeLibraryDir);
            String[] names = dir.list();
            if (names == null) {
                return dir.getAbsolutePath() + " (не читается)";
            }
            Arrays.sort(names);
            StringBuilder out = new StringBuilder(dir.getAbsolutePath())
                    .append(" (").append(names.length).append("):");
            for (String name : names) {
                out.append(' ').append(name);
            }
            return out.toString();
        } catch (Throwable failure) {
            return "ошибка: " + failure;
        }
    }

    private String apkVersion() {
        try {
            return getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Throwable failure) {
            return "?";
        }
    }
}
