package org.rout3;

import android.content.Context;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * The one place that writes the startup journal before Python exists.
 *
 * <p>Everything in the Android startup gap is best-effort by construction: the
 * process may die on the next line, so a diagnostic that throws would replace the
 * failure it is supposed to report.  Every file operation here is wrapped, and a
 * missing or read-only storage directory only costs the file copy -- logcat still
 * receives every line.
 *
 * <p>Two sinks are kept on purpose.  {@code getExternalFilesDir(null)} lives at
 * {@code Android/data/<package>/files/rout3-java.log}, which is readable over
 * USB/MTP without adb and without any storage permission; {@code getFilesDir()}
 * is app-private and always present, so the journal survives a phone where the
 * external volume is unmounted.  Both are trimmed once per launch.
 *
 * <p>Each line is flushed <em>and synced</em>: the case this file exists for is
 * the process dying immediately, and buffering would lose exactly the last marker,
 * the one that says how far the launch got.
 *
 * <p>Called from {@link DiagApp} (before any activity) and {@link DiagActivity}
 * (before {@code PythonActivity.onCreate}); {@link #init(Context)} is idempotent.
 */
final class DiagLog {
    static final String TAG = "rout3-diag";
    static final String LOG_NAME = "rout3-java.log";

    /** Keep the journal small enough to read on a phone and to mail. */
    private static final long LIMIT = 96 * 1024;

    /** Reachable over USB/MTP; null when the volume is not mounted. */
    private static File sExternal;
    /** Always present; null only if the app has no files dir at all. */
    private static File sInternal;
    private static boolean sReady;
    private static boolean sHandler;

    private DiagLog() {
    }

    static synchronized void init(Context context) {
        if (sReady) {
            return;
        }
        sReady = true;
        try {
            File dir = context.getExternalFilesDir(null);
            if (dir != null) {
                sExternal = new File(dir, LOG_NAME);
            }
        } catch (Throwable ignored) {
            // External storage may be absent; the internal copy still exists.
        }
        try {
            sInternal = new File(context.getFilesDir(), LOG_NAME);
        } catch (Throwable ignored) {
            // Nothing to do: logcat still gets every line.
        }
        for (File target : new File[] {sExternal, sInternal}) {
            try {
                if (target != null && target.length() > LIMIT) {
                    target.delete();
                }
            } catch (Throwable ignored) {
                // A full disk must not stop the launch.
            }
        }
    }

    static String path() {
        if (sExternal != null) {
            return sExternal.getAbsolutePath();
        }
        if (sInternal != null) {
            return sInternal.getAbsolutePath();
        }
        return "нет";
    }

    static synchronized void mark(String line) {
        Log.i(TAG, line);
        String stamp = new SimpleDateFormat("MM-dd HH:mm:ss.SSS", Locale.US).format(new Date());
        byte[] bytes = (stamp + "  " + line + "\n").getBytes(StandardCharsets.UTF_8);
        for (File target : new File[] {sExternal, sInternal}) {
            if (target == null) {
                continue;
            }
            FileOutputStream out = null;
            try {
                out = new FileOutputStream(target, true);
                out.write(bytes);
                out.flush();
                out.getFD().sync();
            } catch (Throwable ignored) {
                // Read-only or missing storage: logcat already has the line.
            } finally {
                if (out != null) {
                    try {
                        out.close();
                    } catch (Throwable ignored) {
                        // Closing a file we could not write is not an error.
                    }
                }
            }
        }
    }

    /** Write a stack trace to the journal and logcat; never rethrow. */
    static void crash(String where, Throwable failure) {
        StringWriter buffer = new StringWriter();
        try {
            failure.printStackTrace(new PrintWriter(buffer));
        } catch (Throwable ignored) {
            // Never let the reporter replace the failure it is reporting.
        }
        mark("FAILED " + where + "\n" + buffer);
        try {
            Log.e(TAG, where, failure);
        } catch (Throwable ignored) {
            // Logging a failure must not fail.
        }
    }

    /**
     * Record any exception that reaches the default handler.
     *
     * <p>This is the only hook that also sees a failure in the activity's own
     * class loading ({@code NoClassDefFoundError} during {@code newInstance}),
     * which happens before {@code DiagActivity.onCreate} and is therefore outside
     * the {@code try} there.  The platform's handler is chained, not replaced, so
     * the process still dies the way Android expects; the journal simply gets the
     * trace first.
     */
    static synchronized void installHandler(final Context context) {
        if (sHandler) {
            return;
        }
        sHandler = true;
        final Thread.UncaughtExceptionHandler previous =
                Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler(new Thread.UncaughtExceptionHandler() {
            @Override
            public void uncaughtException(Thread thread, Throwable failure) {
                crash("необработанное исключение в потоке " + thread.getName(), failure);
                try {
                    android.widget.Toast.makeText(context,
                            "ROUT III: сбой запуска\n" + path(),
                            android.widget.Toast.LENGTH_LONG).show();
                } catch (Throwable ignored) {
                    // A toast is a bonus; the journal is the contract.
                }
                if (previous != null) {
                    previous.uncaughtException(thread, failure);
                }
            }
        });
    }
}
