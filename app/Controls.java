package mellon;

import android.app.Activity;
import android.app.AlertDialog;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.SystemClock;
import android.util.Log;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.TextView;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * PC controls: volume, mic, lock, sleep, log off, restart, shut down.
 * The PC's mellon-ctl connects to the abstract socket @mellon-ctl (adb forward);
 * we send it one command per line (GET on connect) and it answers "S vol muted mic muted",
 * "OK" or "ERR". The PC only ever runs its own fixed list of commands.
 */
class Controls {
    private final Activity act;
    private final ExecutorService tx = Executors.newSingleThreadExecutor();
    private volatile OutputStream out; // current PC connection, null when none
    private volatile boolean dragging;
    private TextView status;
    private SeekBar vol, mic;
    private Button volMute, micMute;

    Controls(Activity a) {
        act = a;
    }

    View build() {
        LinearLayout v = new LinearLayout(act);
        v.setOrientation(LinearLayout.VERTICAL);
        v.setPadding(32, 32, 32, 32);
        status = new TextView(act);
        status.setText("U2F key ready on @mellon. PC controls: waiting");
        v.addView(status);
        vol = slider(v, "Volume", "VOL");
        volMute = button(v, "Mute", () -> send("VOL MUTE"));
        mic = slider(v, "Microphone", "MIC");
        micMute = button(v, "Mute", () -> send("MIC MUTE"));
        button(v, "Lock", () -> send("LOCK"));
        button(v, "Sleep", () -> send("SLEEP"));
        button(v, "Log off", () -> confirm("Log off the PC?", "LOGOFF"));
        button(v, "Restart", () -> confirm("Restart the PC?", "REBOOT"));
        button(v, "Shut down", () -> confirm("Shut down the PC?", "SHUTDOWN"));
        ScrollView s = new ScrollView(act);
        s.addView(v);
        return s;
    }

    private SeekBar slider(LinearLayout v, String label, String cmd) {
        TextView t = new TextView(act);
        t.setText(label);
        t.setPadding(0, 32, 0, 0);
        v.addView(t);
        SeekBar s = new SeekBar(act);
        s.setMax(100);
        s.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            private long last;

            @Override
            public void onProgressChanged(SeekBar b, int p, boolean fromUser) {
                long now = SystemClock.uptimeMillis();
                if (fromUser && now - last > 150) { // follow the finger, gently
                    last = now;
                    send(cmd + " " + p);
                }
            }

            @Override
            public void onStartTrackingTouch(SeekBar b) {
                dragging = true;
            }

            @Override
            public void onStopTrackingTouch(SeekBar b) {
                dragging = false;
                send(cmd + " " + b.getProgress());
            }
        });
        v.addView(s);
        return s;
    }

    private Button button(LinearLayout v, String text, Runnable click) {
        Button b = new Button(act);
        b.setText(text);
        b.setOnClickListener(x -> click.run());
        v.addView(b);
        return b;
    }

    private void confirm(String question, String cmd) {
        new AlertDialog.Builder(act)
                .setMessage(question)
                .setPositiveButton("Yes", (d, w) -> send(cmd))
                .setNegativeButton("No", null)
                .show();
    }

    private void send(String cmd) {
        tx.execute(() -> {
            OutputStream o = out;
            if (o == null) return;
            try {
                o.write((cmd + "\n").getBytes(StandardCharsets.US_ASCII));
                o.flush();
            } catch (IOException e) {
                // connection dropped; serve() notices and resets
            }
        });
    }

    /** Runs on its own thread: accept the PC, then read its replies until it leaves. */
    void serve() {
        try {
            LocalServerSocket srv = new LocalServerSocket("mellon-ctl");
            while (true) {
                try (LocalSocket c = srv.accept()) {
                    out = c.getOutputStream();
                    send("GET"); // ask for the current levels
                    BufferedReader in = new BufferedReader(
                            new InputStreamReader(c.getInputStream(), StandardCharsets.US_ASCII));
                    for (String l; (l = in.readLine()) != null; ) reply(l);
                } catch (IOException e) {
                    // PC went away; wait for the next one
                } finally {
                    out = null;
                    act.runOnUiThread(() -> status.setText("PC controls: waiting"));
                }
            }
        } catch (IOException e) {
            Log.e("mellon", "controls stopped", e);
        }
    }

    private void reply(String l) {
        String[] w = l.split(" ");
        if (w.length == 5 && w[0].equals("S")) {
            try {
                apply(Integer.parseInt(w[1]), w[2].equals("1"), Integer.parseInt(w[3]), w[4].equals("1"));
            } catch (NumberFormatException e) {
                Log.w("mellon", "bad state line: " + l);
            }
        } else if (l.equals("ERR")) {
            act.runOnUiThread(() -> status.setText("PC: command failed"));
        }
    }

    private void apply(int v, boolean vm, int m, boolean mm) {
        act.runOnUiThread(() -> {
            status.setText("PC controls: connected");
            if (!dragging) { // do not fight the finger
                vol.setProgress(v);
                mic.setProgress(m);
            }
            volMute.setText(vm ? "Unmute" : "Mute");
            micMute.setText(mm ? "Unmute" : "Mute");
        });
    }
}
