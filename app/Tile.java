package mellon;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.util.TypedValue;
import android.view.View;

/**
 * A round action button: a ring with the icon inside and the label underneath, drawn in the same
 * ring proportions as {@link Dial} (outer diameter 0.87 of the box). Accent for everyday actions,
 * red for the ones that end the session.
 */
class Tile extends View {
    private static final float LABEL_DP = 34; // room under the ring for the label

    private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final int icon, color;
    private final String label;

    Tile(Context c, int icon, String label, boolean danger) {
        super(c);
        this.icon = icon;
        this.label = label;
        color = danger ? Icons.DANGER : Icons.ACCENT;
        p.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        p.setTextAlign(Paint.Align.CENTER);
        p.setTextSize(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, 15, c.getResources().getDisplayMetrics()));
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
        float labelH = Icons.dp(getContext(), LABEL_DP);
        float s = Math.min(getWidth(), getHeight() - labelH);
        float top = (getHeight() - (s + labelH)) / 2f, cx = getWidth() / 2f, cy = top + s / 2f;
        int fade = isPressed() ? 0xA0000000 : 0xFF000000;
        int col = (color & 0x00FFFFFF) | fade;
        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(0.035f * s);
        p.setColor(col);
        c.drawCircle(cx, cy, 0.4f * s, p);
        Icons.draw(c, icon, cx, cy, 0.34f * s, col);
        p.setStyle(Paint.Style.FILL);
        p.setColor((Icons.TEXT & 0x00FFFFFF) | fade); // labels are white; only the ring and icon carry the colour
        c.drawText(label, cx, top + s + Icons.dp(getContext(), 22), p);
    }
}
