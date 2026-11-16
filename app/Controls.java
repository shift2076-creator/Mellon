package mellon;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.TextUtils;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * PC controls: now playing, volume, mic (page 1); lock, sleep, log off, restart, shut down (page 2).
 * The PC's mellon-ctl connects to the abstract socket @mellon-ctl (adb forward);
 * we send it one command per line and it answers "S vol muted mic muted", "M\tstatus\ttitle\tartist",
 * "OK" or "ERR". The PC only ever runs its own fixed list of commands.
 */
class Controls {
    private static final int MATCH = ViewGroup.LayoutParams.MATCH_PARENT, WRAP = ViewGroup.LayoutParams.WRAP_CONTENT;

    private final Activity act;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService tx = Executors.newSingleThreadExecutor();
    private volatile OutputStream out; // current PC connection, null when none

    private Pager pager;
    private LinearLayout page1, page2;
    private TextView title, artist;
    private IconBtn prev, play, next, navSound, navPower;
    private Dial vol, mic;
    private Tile lock, sleep, logoff, restart, shutdown;
    private String nowStatus = "none", nowTitle = "", nowArtist = "";
    private boolean nowCanNext;
    private boolean visible;

    private final Runnable poll = new Runnable() {
        @Override
        public void run() {
            if (!visible || pager.page != 0) return;
            send("NOW");
            ui.postDelayed(this, 2000);
        }
    };

    Controls(Activity a) {
        act = a;
    }

    // ---- building ----

    View build() {
        Root root = new Root(act);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.BLACK);
        // Android 15 draws apps edge-to-edge: keep the content clear of the status/nav bars.
        root.setOnApplyWindowInsetsListener((w, i) -> {
            w.setPadding(i.getSystemWindowInsetLeft(), i.getSystemWindowInsetTop(),
                    i.getSystemWindowInsetRight(), i.getSystemWindowInsetBottom());
            return i;
        });

        pager = new Pager(act);
        page1 = new LinearLayout(act);
        page1.setOrientation(LinearLayout.VERTICAL);
        page2 = new LinearLayout(act);
        page2.setOrientation(LinearLayout.VERTICAL);
        pager.addView(page1, new FrameLayout.LayoutParams(MATCH, MATCH));
        pager.addView(page2, new FrameLayout.LayoutParams(MATCH, MATCH));
        pager.onPage = this::pageChanged;
        root.addView(pager, new LinearLayout.LayoutParams(MATCH, 0, 1));

        View line = new View(act);
        line.setBackgroundColor(Icons.LINE);
        root.addView(line, new LinearLayout.LayoutParams(MATCH, 1));
        LinearLayout nav = new LinearLayout(act);
        nav.setGravity(Gravity.CENTER);
        navSound = navButton(Icons.SPK, "Sound and now playing", 0);
        navPower = navButton(Icons.POWER, "Session and power", 1);
        nav.addView(navSound);
        nav.addView(navPower);
        root.addView(nav, new LinearLayout.LayoutParams(MATCH, dp(52)));

        title = text(36, Icons.TEXT, true);
        title.setEllipsize(TextUtils.TruncateAt.END);
        artist = text(18, Icons.SUB, false);
        artist.setPadding(0, dp(8), 0, 0);
        prev = new IconBtn(act, Icons.PREV);
        prev.setContentDescription("Previous track");
        prev.setOnClickListener(v -> send("MEDIA PREV"));
        play = new IconBtn(act, Icons.PLAY);
        play.setContentDescription("Play or pause");
        play.setOnClickListener(v -> send("MEDIA PLAYPAUSE"));
        next = new IconBtn(act, Icons.NEXT);
        next.setContentDescription("Next track");
        next.setOnClickListener(v -> send("MEDIA NEXT"));

        vol = dial(Icons.SPK, "Volume", "VOL");
        mic = dial(Icons.MIC, "Microphone", "MIC");

        lock = tile(Icons.LOCK, "Lock", false, () -> send("LOCK"));
        sleep = tile(Icons.MOON, "Sleep", false, () -> send("SLEEP"));
        logoff = tile(Icons.LOGOFF, "Log off", true, () -> confirm("Log off the PC?", "LOGOFF"));
        restart = tile(Icons.RESTART, "Restart", true, () -> confirm("Restart the PC?", "REBOOT"));
        shutdown = tile(Icons.POWER, "Shut down", true, () -> confirm("Shut down the PC?", "SHUTDOWN"));

        root.arrange = this::arrange;
        arrange();
        pageChanged(0);
        return root;
    }

    /** Lays the (persistent) widgets out for the current orientation. Portrait and landscape differ. */
    private void arrange() {
        boolean land = act.getResources().getConfiguration().orientation == Configuration.ORIENTATION_LANDSCAPE;
        for (View v : new View[]{title, artist, prev, play, next, vol, mic, lock, sleep, logoff, restart, shutdown}) {
            if (v.getParent() instanceof ViewGroup) ((ViewGroup) v.getParent()).removeView(v);
        }
        page1.removeAllViews();
        page2.removeAllViews();
        page1.setPadding(dp(20), dp(land ? 14 : 20), dp(20), dp(land ? 22 : 8));
        page2.setPadding(dp(20), dp(land ? 12 : 20), dp(20), dp(land ? 4 : 8));
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, land ? 38 : 32);
        title.setMaxLines(land ? 2 : 3);

        if (land) {
            page1.addView(title, new LinearLayout.LayoutParams(MATCH, WRAP));
            page1.addView(artist, new LinearLayout.LayoutParams(MATCH, WRAP));
            page1.addView(new View(act), new LinearLayout.LayoutParams(MATCH, 0, 1));
            // five equal controls on one line: the play circle matches the dials' outer diameter
            int slot = 130, box = 110, glyph = dp(38);
            float ringDia = box * 0.87f;
            prev.set(glyph, 0, 0, Icons.TEXT);
            play.set(glyph, dp(ringDia), Icons.ACCENT, 0xFF0E1116);
            next.set(glyph, 0, 0, Icons.TEXT);
            vol.setIconPx(glyph);
            mic.setIconPx(glyph);
            LinearLayout row = new LinearLayout(act);
            row.setGravity(Gravity.CENTER);
            for (View v : new View[]{prev, play, next}) row.addView(v, new LinearLayout.LayoutParams(dp(slot), dp(box)));
            LinearLayout.LayoutParams first = new LinearLayout.LayoutParams(dp(slot), dp(box));
            first.leftMargin = dp(24);
            row.addView(vol, first);
            row.addView(mic, new LinearLayout.LayoutParams(dp(slot), dp(box)));
            page1.addView(row, new LinearLayout.LayoutParams(MATCH, WRAP));

            LinearLayout both = new LinearLayout(act);
            LinearLayout grid = new LinearLayout(act);
            grid.addView(lock, weighted(0, MATCH, 1, 0, 0, 6, 0));
            grid.addView(sleep, weighted(0, MATCH, 1, 6, 0, 0, 0));
            LinearLayout power = new LinearLayout(act);
            power.setOrientation(LinearLayout.VERTICAL);
            power.addView(logoff, weighted(MATCH, 0, 1, 0, 0, 0, 4));
            power.addView(restart, weighted(MATCH, 0, 1, 0, 4, 0, 4));
            power.addView(shutdown, weighted(MATCH, 0, 1, 0, 4, 0, 0));
            both.addView(grid, weighted(0, MATCH, 1, 0, 0, 6, 0));
            both.addView(power, weighted(0, MATCH, 1, 6, 0, 0, 0));
            page2.addView(both, new LinearLayout.LayoutParams(MATCH, MATCH));
        } else {
            LinearLayout media = new LinearLayout(act);
            media.setOrientation(LinearLayout.VERTICAL);
            media.addView(title, new LinearLayout.LayoutParams(MATCH, WRAP));
            media.addView(artist, new LinearLayout.LayoutParams(MATCH, WRAP));
            media.addView(new View(act), new LinearLayout.LayoutParams(MATCH, 0, 1));
            prev.set(dp(24), 0, 0, Icons.TEXT);
            play.set(dp(32), dp(88), Icons.ACCENT, 0xFF0E1116);
            next.set(dp(24), 0, 0, Icons.TEXT);
            vol.setIconPx(0);
            mic.setIconPx(0);
            LinearLayout transport = new LinearLayout(act);
            transport.setGravity(Gravity.CENTER);
            transport.addView(prev, weighted(dp(64), dp(64), 0, 9, 0, 9, 0));
            transport.addView(play, weighted(dp(88), dp(88), 0, 9, 0, 9, 0));
            transport.addView(next, weighted(dp(64), dp(64), 0, 9, 0, 9, 0));
            media.addView(transport, new LinearLayout.LayoutParams(MATCH, WRAP));
            page1.addView(media, new LinearLayout.LayoutParams(MATCH, 0, 1));
            LinearLayout dials = new LinearLayout(act);
            dials.addView(vol, weighted(0, dp(150), 1, 0, 12, 5, 0));
            dials.addView(mic, weighted(0, dp(150), 1, 5, 12, 0, 0));
            page1.addView(dials, new LinearLayout.LayoutParams(MATCH, WRAP));

            LinearLayout grid = new LinearLayout(act);
            grid.addView(lock, weighted(0, dp(128), 1, 0, 0, 6, 0));
            grid.addView(sleep, weighted(0, dp(128), 1, 6, 0, 0, 0));
            page2.addView(grid, new LinearLayout.LayoutParams(MATCH, WRAP));
            page2.addView(logoff, weighted(MATCH, dp(64), 0, 0, 24, 0, 5));
            page2.addView(restart, weighted(MATCH, dp(64), 0, 0, 5, 0, 5));
            page2.addView(shutdown, weighted(MATCH, dp(64), 0, 0, 5, 0, 0));
        }
        showNow();
    }

    /** Layout params with weight and margins in dp. */
    private LinearLayout.LayoutParams weighted(int w, int h, float weight, int l, int t, int r, int b) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(w, h, weight);
        p.setMargins(dp(l), dp(t), dp(r), dp(b));
        return p;
    }

    private int dp(float v) {
        return Math.round(Icons.dp(act, v));
    }

    private TextView text(int sp, int color, boolean bold) {
        TextView t = new TextView(act);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        t.setTypeface(Typeface.create("sans-serif-medium", bold ? Typeface.BOLD : Typeface.NORMAL));
        return t;
    }

    private IconBtn navButton(int icon, String name, int page) {
        IconBtn b = new IconBtn(act, icon);
        b.set(dp(28), 0, 0, Icons.DIM);
        b.setContentDescription(name);
        b.setOnClickListener(v -> pager.go(page));
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(dp(88), dp(52));
        p.setMargins(dp(12), 0, dp(12), 0);
        b.setLayoutParams(p);
        return b;
    }

    private Tile tile(int icon, String label, boolean row, Runnable click) {
        Tile t = new Tile(act, icon, label, row);
        t.setOnClickListener(v -> click.run());
        return t;
    }

    private Dial dial(int icon, String label, String cmd) {
        Dial d = new Dial(act);
        d.setIcon(icon);
        d.setContentDescription(label);
        d.setOnClickListener(v -> send(cmd + " MUTE"));
        long[] last = {0};
        d.setListener((value, done) -> {
            long now = SystemClock.uptimeMillis();
            if (done || now - last[0] > 150) { // follow the finger, gently
                last[0] = now;
                send(cmd + " " + value);
            }
        });
        return d;
    }

    private void confirm(String question, String cmd) {
        new AlertDialog.Builder(act)
                .setMessage(question)
                .setPositiveButton("Yes", (d, w) -> send(cmd))
                .setNegativeButton("No", null)
                .show();
    }

    // ---- state shown on screen (UI thread) ----

    private void pageChanged(int page) {
        navSound.setColor(page == 0 ? Icons.ACCENT : Icons.DIM);
        navPower.setColor(page == 1 ? Icons.ACCENT : Icons.DIM);
        startPolling();
    }

    /** Ask for the track every 2 s, but only while page 1 is on screen. */
    private void startPolling() {
        ui.removeCallbacks(poll);
        if (visible && pager.page == 0) {
            ui.post(poll);
        }
    }

    private void showNow() {
        boolean idle = !(nowStatus.equals("playing") || nowStatus.equals("paused"));
        title.setText(idle ? "Nothing playing" : nowTitle.isEmpty() ? "Unknown track" : nowTitle);
        title.setTextColor(idle ? Icons.DIM : Icons.TEXT);
        artist.setText(idle ? "Start music or a video on the PC." : nowArtist);
        artist.setTextColor(idle ? Icons.DIM : Icons.SUB);
        artist.setVisibility(!idle && nowArtist.isEmpty() ? View.GONE : View.VISIBLE);
        play.setIcon(nowStatus.equals("playing") ? Icons.PAUSE : Icons.PLAY);
        for (View v : new View[]{prev, play, next}) {
            boolean on = !idle && (v != next || nowCanNext); // no queue: next greys out
            v.setEnabled(on);
            v.setAlpha(on ? 1f : 0.4f);
        }
    }

    // ---- the PC connection ----

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
                    send("NOW");
                    BufferedReader in = new BufferedReader(
                            new InputStreamReader(c.getInputStream(), StandardCharsets.UTF_8));
                    for (String l; (l = in.readLine()) != null; ) reply(l);
                } catch (IOException e) {
                    // PC went away; wait for the next one
                } finally {
                    out = null;
                }
            }
        } catch (IOException e) {
            Log.e("mellon", "controls stopped", e);
        }
    }

    private void reply(String l) {
        if (l.startsWith("M\t")) {
            String[] w = l.split("\t", -1);
            if (w.length >= 4) act.runOnUiThread(() -> {
                nowStatus = w[1];
                nowTitle = w[2];
                nowArtist = w[3];
                nowCanNext = w.length < 5 || !w[4].equals("0");
                showNow();
            });
            return;
        }
        String[] w = l.split(" ");
        if (w.length == 5 && w[0].equals("S")) {
            try {
                int v = Integer.parseInt(w[1]), m = Integer.parseInt(w[3]);
                boolean vm = w[2].equals("1"), mm = w[4].equals("1");
                act.runOnUiThread(() -> {
                    vol.setValue(v);
                    vol.setMuted(vm);
                    mic.setValue(m);
                    mic.setMuted(mm);
                    mic.setIcon(mm ? Icons.MICOFF : Icons.MIC);
                });
            } catch (NumberFormatException e) {
                Log.w("mellon", "bad state line: " + l);
            }
        } else if (l.equals("ERR")) {
            act.runOnUiThread(() -> Toast.makeText(act, "PC: command failed", Toast.LENGTH_SHORT).show());
        }
    }

    // ---- views ----

    /** Root column: re-lays out on rotation and pauses polling when the window is hidden. */
    private class Root extends LinearLayout {
        Runnable arrange;

        Root(Context c) {
            super(c);
        }

        @Override
        protected void onConfigurationChanged(Configuration c) {
            super.onConfigurationChanged(c);
            if (arrange != null) arrange.run();
        }

        @Override
        protected void onWindowVisibilityChanged(int v) {
            super.onWindowVisibilityChanged(v);
            visible = v == View.VISIBLE;
            startPolling();
        }
    }

    /** Two full-size pages that follow a horizontal swipe and settle on one. */
    private static class Pager extends FrameLayout {
        interface OnPage {
            void run(int page);
        }

        int page;
        OnPage onPage;
        private float downX, downY, dragX;
        private boolean dragging;
        private final int slop;

        Pager(Context c) {
            super(c);
            slop = ViewConfiguration.get(c).getScaledTouchSlop();
        }

        void go(int p) {
            boolean changed = p != page;
            page = p;
            dragX = 0;
            for (int i = 0; i < getChildCount(); i++) {
                getChildAt(i).animate().translationX((i - page) * (float) getWidth()).setDuration(180).start();
            }
            if (changed && onPage != null) onPage.run(page);
        }

        @Override
        protected void onSizeChanged(int w, int h, int ow, int oh) {
            super.onSizeChanged(w, h, ow, oh);
            for (int i = 0; i < getChildCount(); i++) {
                getChildAt(i).animate().cancel();
                getChildAt(i).setTranslationX((i - page) * (float) w);
            }
        }

        @Override
        public boolean onInterceptTouchEvent(MotionEvent e) {
            switch (e.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    downX = e.getX();
                    downY = e.getY();
                    dragging = false;
                    break;
                case MotionEvent.ACTION_MOVE:
                    float dx = e.getX() - downX;
                    if (!dragging && Math.abs(dx) > slop && Math.abs(dx) > Math.abs(e.getY() - downY)) {
                        dragging = true;
                        downX = e.getX();
                    }
                    break;
                default:
                    break;
            }
            return dragging;
        }

        @Override
        public boolean onTouchEvent(MotionEvent e) {
            switch (e.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    downX = e.getX();
                    downY = e.getY();
                    return true;
                case MotionEvent.ACTION_MOVE:
                    if (!dragging && Math.abs(e.getX() - downX) > slop
                            && Math.abs(e.getX() - downX) > Math.abs(e.getY() - downY)) {
                        dragging = true;
                        downX = e.getX();
                    }
                    if (dragging) {
                        dragX = e.getX() - downX;
                        if ((page == 0 && dragX > 0) || (page == 1 && dragX < 0)) dragX /= 4; // resist at the ends
                        for (int i = 0; i < getChildCount(); i++) {
                            getChildAt(i).animate().cancel();
                            getChildAt(i).setTranslationX((i - page) * (float) getWidth() + dragX);
                        }
                    }
                    return true;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    boolean was = dragging;
                    dragging = false;
                    if (was && dragX < -getWidth() / 6f && page == 0) go(1);
                    else if (was && dragX > getWidth() / 6f && page == 1) go(0);
                    else go(page);
                    return true;
                default:
                    return super.onTouchEvent(e);
            }
        }
    }
}
