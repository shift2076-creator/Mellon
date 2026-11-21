package mellon;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.view.MotionEvent;
import android.view.View;

/** Two page dots at the bottom of the screen (they replace the old navigation bar). Tap a half to go there. */
class PageDots extends View {
    interface OnPage {
        void go(int page);
    }

    private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
    private int page;
    private OnPage onPage;

    PageDots(Context c) {
        super(c);
        setClickable(true);
        setContentDescription("Pages: sound and now playing, session and power");
    }

    void setOnPage(OnPage l) {
        onPage = l;
    }

    void setPage(int i) {
        page = i;
        invalidate();
    }

    @Override
    protected void onDraw(Canvas c) {
        float d = getResources().getDisplayMetrics().density, r = 3.5f * d, gap = 8f * d;
        float cx = getWidth() / 2f, cy = getHeight() / 2f;
        for (int i = 0; i < 2; i++) {
            p.setColor(i == page ? Icons.KNOB : Icons.MUTED_FILL);
            c.drawCircle(cx + (i - 0.5f) * (2 * r + gap), cy, r, p);
        }
    }

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        if (e.getActionMasked() == MotionEvent.ACTION_UP && onPage != null) {
            onPage.go(e.getX() < getWidth() / 2f ? 0 : 1);
            performClick();
        }
        return true;
    }

    @Override
    public boolean performClick() {
        return super.performClick();
    }
}
