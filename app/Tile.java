package mellon;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.util.TypedValue;
import android.view.View;

/** A flat labelled action: a square-ish tile (icon over label) or a full-width row (icon beside label). */
class Tile extends View {
    private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final int icon, iconColor, textColor;
    private final String label;
    private final boolean row;

    Tile(Context c, int icon, String label, boolean row) {
        super(c);
        this.icon = icon;
        this.label = label;
        this.row = row;
        iconColor = row ? Icons.DANGER : Icons.ACCENT;
        textColor = row ? Icons.DANGER : Icons.TEXT;
        p.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        p.setTextSize(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, row ? 16 : 17,
                c.getResources().getDisplayMetrics()));
        setClickable(true);
        setContentDescription(label);
    }

    @Override
    protected void drawableStateChanged() {
        super.drawableStateChanged();
        invalidate();
    }

    @Override
    protected void onDraw(Canvas c) {
        Context x = getContext();
        int fade = isPressed() ? 0xA0000000 : 0xFF000000;
        p.setColor((textColor & 0x00FFFFFF) | fade);
        Paint.FontMetrics m = p.getFontMetrics();
        if (row) {
            float pad = Icons.dp(x, 18), g = Icons.dp(x, 22);
            Icons.draw(c, icon, pad + g / 2, getHeight() / 2f, g, (iconColor & 0x00FFFFFF) | fade);
            c.drawText(label, pad + g + Icons.dp(x, 14), getHeight() / 2f - (m.ascent + m.descent) / 2, p);
        } else {
            float pad = Icons.dp(x, 16), g = Icons.dp(x, 28);
            Icons.draw(c, icon, pad + g / 2, pad + g / 2, g, (iconColor & 0x00FFFFFF) | fade);
            c.drawText(label, pad, getHeight() - pad - m.descent, p);
        }
    }
}
