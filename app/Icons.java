package mellon;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;

/** The few line icons the UI needs, drawn on a 24x24 grid (same geometry as the design mockups). */
final class Icons {
    static final int SPK = 0, MIC = 1, MICOFF = 2, PREV = 3, PLAY = 4, PAUSE = 5, NEXT = 6,
            LOCK = 7, MOON = 8, LOGOFF = 9, RESTART = 10, POWER = 11;

    static final int ACCENT = 0xFF4FD1B5, TEXT = 0xFFE8ECF2, DIM = 0xFF9AA4B2, SUB = 0xFFC4CCD8,
            LINE = 0xFF2A313C, MUTED_FILL = 0xFF5B6574, DANGER = 0xFFFFA396;
    /** Main screen: ultraviolet play button, cyan dial arcs and page dot. */
    static final int PLAY_BG = 0xFF7C4DFF, KNOB = 0xFF22D3EE;

    private static final Paint P = new Paint(Paint.ANTI_ALIAS_FLAG);

    static {
        P.setStrokeCap(Paint.Cap.ROUND);
        P.setStrokeJoin(Paint.Join.ROUND);
        P.setStrokeWidth(1.75f);
    }

    private Icons() {
    }

    static float dp(Context c, float v) {
        return v * c.getResources().getDisplayMetrics().density;
    }

    /** Draws icon {@code id} centred on (cx, cy), {@code size} px square. UI thread only. */
    static void draw(Canvas c, int id, float cx, float cy, float size, int color) {
        c.save();
        c.translate(cx - size / 2, cy - size / 2);
        c.scale(size / 24f, size / 24f);
        P.setColor(color);
        Path p = new Path();
        boolean solid = false;
        switch (id) {
            case SPK:
                poly(p, 11, 5, 6, 9, 2, 9, 2, 15, 6, 15, 11, 19);
                p.close();
                arc(p, 12, 12, 5, -45, 90);
                arc(p, 12, 12, 10, -45, 90);
                break;
            case MIC:
            case MICOFF:
                p.addRoundRect(new RectF(9, 2, 15, 15), 3, 3, Path.Direction.CW);
                p.moveTo(19, 10);
                p.lineTo(19, 12);
                p.arcTo(oval(12, 12, 7), 0, 180);
                p.lineTo(5, 10);
                p.moveTo(12, 19);
                p.lineTo(12, 22);
                if (id == MICOFF) {
                    p.moveTo(3, 3);
                    p.lineTo(21, 21);
                }
                break;
            case PREV:
                solid = true;
                poly(p, 19, 20, 9, 12, 19, 4);
                p.close();
                p.moveTo(5, 19);
                p.lineTo(5, 5);
                break;
            case NEXT:
                solid = true;
                poly(p, 5, 4, 15, 12, 5, 20);
                p.close();
                p.moveTo(19, 5);
                p.lineTo(19, 19);
                break;
            case PLAY:
                solid = true;
                poly(p, 7, 4, 20, 12, 7, 20);
                p.close();
                break;
            case PAUSE:
                solid = true;
                p.addRoundRect(new RectF(6, 4, 10, 20), 1, 1, Path.Direction.CW);
                p.addRoundRect(new RectF(14, 4, 18, 20), 1, 1, Path.Direction.CW);
                break;
            case LOCK:
                p.addRoundRect(new RectF(3, 11, 21, 22), 2, 2, Path.Direction.CW);
                p.moveTo(7, 11);
                p.lineTo(7, 7);
                p.arcTo(oval(12, 7, 5), 180, 180);
                p.lineTo(17, 11);
                break;
            case MOON:
                p.moveTo(21, 12.79f);
                p.arcTo(oval(12, 12, 9), 5, 260);
                p.arcTo(oval(16.1f, 7.9f, 6.93f), 225, -180);
                p.close();
                break;
            case LOGOFF:
                p.moveTo(9, 21);
                p.lineTo(5, 21);
                p.quadTo(3, 21, 3, 19);
                p.lineTo(3, 5);
                p.quadTo(3, 3, 5, 3);
                p.lineTo(9, 3);
                poly(p, 16, 17, 21, 12, 16, 7);
                p.moveTo(21, 12);
                p.lineTo(9, 12);
                break;
            case RESTART:
                poly(p, 23, 4, 23, 10, 17, 10);
                p.moveTo(20.49f, 15);
                p.arcTo(oval(12, 12, 9), 19.45f, 295.5f);
                p.lineTo(23, 10);
                break;
            case POWER:
                p.moveTo(18.36f, 6.64f);
                p.arcTo(oval(12, 12, 9), -45, 270);
                p.moveTo(12, 2);
                p.lineTo(12, 12);
                break;
            default:
                break;
        }
        P.setStyle(solid ? Paint.Style.FILL_AND_STROKE : Paint.Style.STROKE);
        c.drawPath(p, P);
        c.restore();
    }

    private static RectF oval(float cx, float cy, float r) {
        return new RectF(cx - r, cy - r, cx + r, cy + r);
    }

    private static void arc(Path p, float cx, float cy, float r, float start, float sweep) {
        p.arcTo(oval(cx, cy, r), start, sweep, true); // true: begin a new contour
    }

    private static void poly(Path p, float... xy) {
        p.moveTo(xy[0], xy[1]);
        for (int i = 2; i < xy.length; i += 2) p.lineTo(xy[i], xy[i + 1]);
    }
}
