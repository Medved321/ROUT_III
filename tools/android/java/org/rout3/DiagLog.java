package org.rout3;

import android.content.ContentResolver;
import android.content.ContentUris;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
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
 * <p>Three sinks are kept on purpose.  {@code getExternalFilesDir(null)} lives at
 * {@code Android/data/<package>/files/rout3-java.log}, which is readable over
 * USB/MTP without adb and without any storage permission; {@code getFilesDir()}
 * is app-private and always present, so the journal survives a phone where the
 * external volume is unmounted.  Since Android 11 many phones hide Android/data
 * from both the file manager and MTP, so a MediaStore row in
 * {@code Downloads/ROUT3/rout3-java.log} is appended as well: it is visible in
 * the ordinary Files app with no adb, no MTP and no permission request.  The two
 * file copies are trimmed once per launch; the Downloads copy is appended.
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
    /**
     * MediaStore row in Downloads/ROUT3. Android 11+ hides Android/data both
     * from the file manager and, on some phones, from MTP; this copy is visible
     * in the ordinary "Files" app without adb and without any permission.
     */
    private static Uri sDownloads;
    private static ContentResolver sResolver;
    private static boolean sReady;
    private static boolean sHandler;

    private DiagLog() {
    }

    static synchronized void init(Context context) {
        if (sReady) {
            return;
        }
        sReady = true;
        sResolver = context.getContentResolver();
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
        if (Build.VERSION.SDK_INT >= 29) {
            try {
                String relative = Environment.DIRECTORY_DOWNLOADS + "/ROUT3";
                // Reuse the existing row: a fresh insert would give each launch
                // its own "(1).log", and the path on screen would point at an old
                // report instead of the failure being diagnosed.
                sDownloads = findDownloads(relative);
                if (sDownloads == null) {
                    ContentValues values = new ContentValues();
                    values.put(MediaStore.MediaColumns.DISPLAY_NAME, LOG_NAME);
                    values.put(MediaStore.MediaColumns.MIME_TYPE, "text/plain");
                    values.put(MediaStore.MediaColumns.RELATIVE_PATH, relative);
                    sDownloads = sResolver.insert(
                            MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
                }
            } catch (Throwable ignored) {
                // No Downloads volume: the two file sinks and logcat remain.
                sDownloads = null;
            }
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

    /** Find the Downloads row left by an earlier launch, if there is one. */
    private static Uri findDownloads(String relative) {
        Cursor cursor = null;
        try {
            String[] projection = {MediaStore.MediaColumns._ID};
            String selection = MediaStore.MediaColumns.DISPLAY_NAME + "=? AND "
                    + MediaStore.MediaColumns.RELATIVE_PATH + "=?";
            cursor = sResolver.query(MediaStore.Downloads.EXTERNAL_CONTENT_URI,
                    projection, selection, new String[] {LOG_NAME, relative}, null);
            if (cursor != null && cursor.moveToFirst()) {
                long id = cursor.getLong(
                        cursor.getColumnIndexOrThrow(MediaStore.MediaColumns._ID));
                return ContentUris.withAppendedId(
                        MediaStore.Downloads.EXTERNAL_CONTENT_URI, id);
            }
        } catch (Throwable ignored) {
            // A query we cannot run only costs a new row; the journal still works.
        } finally {
            if (cursor != null) {
                try {
                    cursor.close();
                } catch (Throwable ignored) {
                    // Closing a cursor we could not read is not an error.
                }
            }
        }
        return null;
    }

    /** Where a person can open the journal with the ordinary Files app. */
    static String publicPath() {
        return sDownloads != null
                ? "Загрузки/ROUT3/" + LOG_NAME
                : path();
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
        if (sDownloads != null && sResolver != null) {
            OutputStream out = null;
            try {
                out = sResolver.openOutputStream(sDownloads, "wa");
                if (out != null) {
                    out.write(bytes);
                    out.flush();
                }
            } catch (Throwable ignored) {
                // MediaStore is a convenience: logcat and the files still work.
            } finally {
                if (out != null) {
                    try {
                        out.close();
                    } catch (Throwable ignored) {
                        // Closing a stream we could not write is not an error.
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
                            "ROUT III: сбой запуска\n" + publicPath(),
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
