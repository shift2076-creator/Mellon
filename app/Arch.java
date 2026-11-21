package mellon;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Shader;
import android.view.View;
import android.view.animation.AccelerateDecelerateInterpolator;
import android.view.animation.DecelerateInterpolator;
import java.util.ArrayList;

/**
 * A glowing white arch that frames the whole window while the fingerprint prompt is open. The system
 * prompt cannot be restyled (it is drawn by the system, not the app), so this view lies over the
 * controls in the app's own window, behind that prompt. {@link #show()} fades it in and pulses it
 * dimly while we wait for a finger; {@link #flare()} lights it fully for a moment, then fades it out
 * (fingerprint accepted); {@link #fail()} flickers it out (error or cancel). Call from the UI thread.
 * <p>
 * Two concentric arches stand on a base line: the outer {@link #EDGE} dp in from the window edge, the
 * inner {@link #GAP} dp inside that, the doorway between them filled with a faint light. The dome is a
 * circle in portrait and a flattened ellipse in landscape. The glow is a stack of translucent strokes
 * of growing width, not a blur, so it costs nothing beyond a few path draws per frame.
 */
class Arch extends View {
    private static final float EDGE = 12f, GAP = 16f; // dp
    private static final float[] WIDTH = {26f, 18f, 12f, 7f, 3.4f}; // dp, outer glow in to core
    private static final float[] ALPHA = {0.05f, 0.09f, 0.16f, 0.35f, 1f};
    private static final float INNER = 0.7f; // inner arch strokes are this much thinner
    private static final float SCRIM = 0.85f; // how far the controls are dimmed behind the arch
    private static final float LOW = 0.38f, HIGH = 0.72f, DIP = 0.12f; // glow: pulse range, failure dip

    private final Paint stroke = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint dot = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path outer = new Path(), inner = new Path(), door = new Path(), base = new Path();
    private final RectF oval = new RectF();
    private final ArrayList<ValueAnimator> live = new ArrayList<>();
    private final float d; // dp -> px
    private float shown, glow = LOW; // overall fade 0..1, arch brightness 0..1
    private float dotX, dotY;

    Arch(Context c) {
        super(c);
        d = getResources().getDisplayMetrics().density;
        stroke.setStyle(Paint.Style.STROKE);
        stroke.setStrokeCap(Paint.Cap.ROUND);
        stroke.setStrokeJoin(Paint.Join.ROUND);
        stroke.setColor(0xFFFFFFFF);
        fill.setStyle(Paint.Style.FILL);
        dot.setStyle(Paint.Style.FILL);
        dot.setColor(0xFFFFFFFF);
        setVisibility(GONE);
        setClickable(false);
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
    }

    /** The prompt opened: fade in and pulse until a result arrives. */
    void show() {
        stop();
        setVisibility(VISIBLE);
        glow = LOW;
        ValueAnimator p = ValueAnimator.ofFloat(LOW, HIGH);
        p.setDuration(1300);
        p.setRepeatMode(ValueAnimator.REVERSE);
        p.setRepeatCount(ValueAnimator.INFINITE);
        p.setInterpolator(new AccelerateDecelerateInterpolator());
        p.addUpdateListener(a -> {
            glow = (Float) a.getAnimatedValue();
            invalidate();
        });
        run(p);
        fade(shown, 1f, 250, 0);
    }

    /** Fingerprint accepted: full glow, hold a moment, fade out. */
    void flare() {
        stop();
        setVisibility(VISIBLE);
        brighten(1f, 220);
        fade(shown, 1f, 150, 0);
        fade(1f, 0f, 600, 900);
    }

    /** Error or cancel: dip the glow and fade out. Does nothing if the arch is not showing. */
    void fail() {
        if (getVisibility() != VISIBLE) return;
        stop();
        brighten(DIP, 120);
        fade(shown, 0f, 380, 120);
    }

    private void brighten(float to, long ms) {
        ValueAnimator v = ValueAnimator.ofFloat(glow, to);
        v.setDuration(ms);
        v.setInterpolator(new DecelerateInterpolator());
        v.addUpdateListener(a -> {
            glow = (Float) a.getAnimatedValue();
            invalidate();
        });
        run(v);
    }

    private void fade(float from, float to, long ms, long delay) {
        ValueAnimator v = ValueAnimator.ofFloat(from, to);
        v.setDuration(ms);
        v.setStartDelay(delay);
        v.addUpdateListener(a -> {
            shown = (Float) a.getAnimatedValue();
            invalidate();
        });
        if (to == 0f) {
            v.addListener(new AnimatorListenerAdapter() {
                private boolean cancelled;

                @Override
                public void onAnimationCancel(Animator a) {
                    cancelled = true;
                }

                @Override
                public void onAnimationEnd(Animator a) {
                    if (!cancelled) setVisibility(GONE);
                }
            });
        }
        run(v);
    }

    private void run(ValueAnimator v) {
        live.add(v);
        v.start();
    }

    private void stop() {
        ArrayList<ValueAnimator> old = new ArrayList<>(live);
        live.clear();
        for (ValueAnimator v : old) v.cancel();
    }

    @Override
    protected void onDetachedFromWindow() {
        stop();
        super.onDetachedFromWindow();
    }

    @Override
    protected void onSizeChanged(int w, int h, int ow, int oh) {
        super.onSizeChanged(w, h, ow, oh);
        float x0 = EDGE * d, x1 = w - EDGE * d, top = EDGE * d, bottom = h - EDGE * d, gap = GAP * d;
        float dome = Math.min((x1 - x0) / 2f, 0.45f * h), spring = top + dome;
        float rise = Math.max(1f, dome - gap);
        arch(outer, x0, x1, spring, dome, bottom);
        arch(inner, x0 + gap, x1 - gap, spring, rise, bottom);
        door.set(inner);
        door.close();
        base.rewind();
        base.moveTo(0, bottom);
        base.lineTo(w, bottom);
        dotX = w / 2f;
        dotY = top + gap / 2f;
        fill.setShader(new LinearGradient(0, spring - rise, 0, bottom,
                Color.argb(10, 255, 255, 255), Color.argb(66, 255, 255, 255), Shader.TileMode.CLAMP));
    }

    /** Two pillars up to the spring line, joined by a half-oval {@code rise} high. */
    private void arch(Path p, float x0, float x1, float spring, float rise, float bottom) {
        p.rewind();
        p.moveTo(x0, bottom);
        p.lineTo(x0, spring);
        oval.set(x0, spring - rise, x1, spring + rise);
        p.arcTo(oval, 180f, 180f); // left to right over the top
        p.lineTo(x1, bottom);
    }

    @Override
    protected void onDraw(Canvas c) {
        if (shown <= 0f) return;
        float g = glow * shown;
        c.drawColor(Color.argb((int) (255 * SCRIM * shown), 0, 0, 0));
        fill.setAlpha((int) (255 * g));
        c.drawPath(door, fill);
        for (int i = 0; i < WIDTH.length; i++) {
            stroke.setAlpha((int) (255 * ALPHA[i] * g));
            stroke.setStrokeWidth(WIDTH[i] * d);
            c.drawPath(outer, stroke);
            c.drawPath(base, stroke);
            stroke.setStrokeWidth(WIDTH[i] * INNER * d);
            c.drawPath(inner, stroke);
        }
        dot.setAlpha((int) (255 * 0.25f * g));
        c.drawCircle(dotX, dotY, 9 * d, dot);
        dot.setAlpha((int) (255 * g));
        c.drawCircle(dotX, dotY, 3.4f * d, dot);
    }
}
