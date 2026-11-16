package mellon;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.view.View;

/** A flat icon button: an icon, optionally on a filled circle. The view may be bigger than the circle. */
class IconBtn extends View {
    private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
    private int icon, fill, color = Icons.TEXT;
    private float glyph, dia;

    IconBtn(Context c, int icon) {
        super(c);
        this.icon = icon;
        setClickable(true);
    }

    void setIcon(int id) {
        icon = id;
        invalidate();
    }

    /** Glyph and circle sizes in px; fill 0 = no circle. */
    void set(float glyphPx, float circlePx, int fillColor, int iconColor) {
        glyph = glyphPx;
        dia = circlePx;
        fill = fillColor;
        color = iconColor;
        invalidate();
    }

    void setColor(int iconColor) {
        color = iconColor;
        invalidate();
    }

    @Override
    protected void drawableStateChanged() {
        super.drawableStateChanged();
        invalidate();
    }

    @Override
    protected void onDraw(Canvas c) {
        float cx = getWidth() / 2f, cy = getHeight() / 2f;
        int fade = isPressed() ? 0xA0000000 : 0xFF000000;
        if (fill != 0 && dia > 0) {
            p.setStyle(Paint.Style.FILL);
            p.setColor((fill & 0x00FFFFFF) | fade);
            c.drawCircle(cx, cy, dia / 2f, p);
        }
        Icons.draw(c, icon, cx, cy, glyph, (color & 0x00FFFFFF) | fade);
    }
}
