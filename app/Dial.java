package mellon;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.MotionEvent;
import android.view.View;

/**
 * A 270-degree level dial with its icon in the middle.
 * Drag around the ring to set the level; tap the icon to mute (the click listener).
 */
class Dial extends View {
    interface Listener {
        void onLevel(int value, boolean done);
    }

    private static final float START = 135f, SPAN = 270f, HUB = 0.28f; // tap-to-mute radius, of the size

    private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF r = new RectF();
    private Listener listener;
    private int value, icon = Icons.SPK;
    private float iconPx;
    private boolean muted, dragging, hub;

    Dial(Context c) {
        super(c);
        p.setStrokeCap(Paint.Cap.ROUND);
        setClickable(true);
    }

    void setListener(Listener l) {
        listener = l;
    }

    void setIcon(int id) {
        icon = id;
        invalidate();
    }

    /** Glyph size in px; 0 = scale with the dial. */
    void setIconPx(float px) {
        iconPx = px;
        invalidate();
    }

    /** Follows the PC, but never fights the finger. */
    void setValue(int v) {
        if (dragging) return;
        value = Math.max(0, Math.min(100, v));
        invalidate();
    }

    void setMuted(boolean m) {
        muted = m;
        invalidate();
    }

    int getValue() {
        return value;
    }

    @Override
    protected void onDraw(Canvas c) {
        float s = Math.min(getWidth(), getHeight()), cx = getWidth() / 2f, cy = getHeight() / 2f;
        float ring = 0.4f * s;
        r.set(cx - ring, cy - ring, cx + ring, cy + ring);
        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(0.07f * s);
        p.setColor(Icons.LINE);
        c.drawArc(r, START, SPAN, false, p);
        if (value > 0) {
            p.setColor(muted ? Icons.MUTED_FILL : Icons.ACCENT);
            c.drawArc(r, START, SPAN * value / 100f, false, p);
        }
        double a = Math.toRadians(START + SPAN * value / 100f);
        p.setStyle(Paint.Style.FILL);
        p.setColor(muted ? Icons.DIM : Icons.TEXT);
        c.drawCircle(cx + ring * (float) Math.cos(a), cy + ring * (float) Math.sin(a), 0.055f * s, p);
        Icons.draw(c, icon, cx, cy, iconPx > 0 ? iconPx : 0.34f * s, muted ? Icons.DIM : Icons.TEXT);
    }

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        float dx = e.getX() - getWidth() / 2f, dy = e.getY() - getHeight() / 2f;
        boolean inHub = Math.hypot(dx, dy) < HUB * Math.min(getWidth(), getHeight());
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                hub = inHub;
                dragging = !inHub;
                if (dragging) { // a drag on the ring is ours, not the pager's
                    getParent().requestDisallowInterceptTouchEvent(true);
                    set(dx, dy, false);
                }
                return true;
            case MotionEvent.ACTION_MOVE:
                if (dragging) set(dx, dy, false);
                return true;
            case MotionEvent.ACTION_UP:
                if (dragging) {
                    set(dx, dy, true);
                } else if (hub && inHub) {
                    performClick();
                }
                dragging = false;
                return true;
            case MotionEvent.ACTION_CANCEL:
                dragging = false;
                return true;
            default:
                return super.onTouchEvent(e);
        }
    }

    @Override
    public boolean performClick() {
        return super.performClick();
    }

    private void set(float dx, float dy, boolean done) {
        float a = (float) Math.toDegrees(Math.atan2(dy, dx)) - START;
        a = ((a % 360f) + 360f) % 360f;
        if (a > SPAN) a = a < SPAN + (360f - SPAN) / 2f ? SPAN : 0f; // the gap at the bottom snaps to an end
        value = Math.round(a / SPAN * 100f);
        invalidate();
        if (listener != null) listener.onLevel(value, done);
    }
}
