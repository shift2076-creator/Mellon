package mellon;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.view.View;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * The PC's GPU and CPU rings, same drawing as the Pi kiosk: usage fills the top half inward from
 * 9 and 3 o'clock (meeting at 12 at 100%), memory (VRAM, or RAM for the CPU) is liquid in the bottom
 * half, name and temperature sit inside. Layout follows the kiosk by GPU count: one GPU sits
 * diagonal to the CPU, two GPUs side by side with the CPU centred under them, three GPUs and the
 * CPU form a 2x2 grid. Every ring is {@link #D} dp across.
 */
class Rings extends View {
    static final float D = 140f; // ring diameter, dp; the dials use the same size
    private static final int GPU_MAX = 3;
    // Dial centres in the kiosk's own units (a ring is 148 wide), relative to the first GPU.
    private static final float[][][] GPU_AT = {
            {}, {{0, 0}}, {{0, 0}, {160, 0}}, {{0, 0}, {160, 0}, {0, 158}}};
    private static final float[][] CPU_AT = {{0, 0}, {100, 122}, {80, 132}, {160, 158}};
    private static final int EMPTY = 0xFF1F1F1F, TEXT = 0xFFF1F5F9, DIM = 0xFF94A3B8,
            WARN = 0xFFFBBF24, BAD = 0xFFF87171, CPU = 0xFF4ADE80;
    private static final int[] GPU_COLOR = {0xFF7DD3FC, 0xFFC4B5FD, 0xFFF9A8D4};
    private static final float SIDE = 87.5f; // degrees each half spans either side of 12 / 6 o'clock

    /** What one ring shows; -1 means unknown (drawn as an empty ring and "--"). */
    static final class Gauge {
        String name = "--";
        int usage = -1, temp = -1, mem = -1, color;
    }

    private Gauge[] gpus = new Gauge[0];
    private Gauge cpu = new Gauge();
    private final Paint arc = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint label = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint temp = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF oval = new RectF();

    Rings(Context c) {
        super(c);
        cpu.color = CPU;
        gpus = new Gauge[] {gauge(null, GPU_COLOR[0]), gauge(null, GPU_COLOR[1])}; // until the PC answers
        arc.setStyle(Paint.Style.STROKE);
        label.setTextAlign(Paint.Align.CENTER);
        label.setTypeface(Typeface.MONOSPACE);
        temp.setTextAlign(Paint.Align.CENTER);
        temp.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        setContentDescription("GPU and CPU load");
    }

    /** Takes the PC's STATS json: {"gpus":[{name,usage,temp,mem}],"cpu":{name,usage,temp,mem}}. */
    void setData(JSONObject o) {
        JSONArray a = o.optJSONArray("gpus");
        int n = a == null ? 0 : Math.min(a.length(), GPU_MAX);
        Gauge[] g = new Gauge[n];
        for (int i = 0; i < n; i++) {
            g[i] = gauge(a.optJSONObject(i), GPU_COLOR[i]);
        }
        Gauge c = gauge(o.optJSONObject("cpu"), CPU);
        boolean relayout = g.length != gpus.length;
        gpus = g;
        cpu = c;
        setContentDescription(describe());
        if (relayout) requestLayout();
        invalidate();
    }

    private static Gauge gauge(JSONObject j, int color) {
        Gauge g = new Gauge();
        g.color = color;
        if (j != null) {
            String name = j.optString("name", "--");
            g.name = name.length() > 12 ? name.substring(0, 12) : name;
            g.usage = j.optInt("usage", -1);
            g.temp = j.optInt("temp", -1);
            g.mem = j.optInt("mem", -1);
        }
        return g;
    }

    private String describe() {
        StringBuilder b = new StringBuilder();
        for (Gauge g : gpus) b.append(say(g)).append(". ");
        return b.append(say(cpu)).toString();
    }

    private static String say(Gauge g) {
        return g.name + ": usage " + (g.usage < 0 ? "unknown" : g.usage + " percent")
                + ", memory " + (g.mem < 0 ? "unknown" : g.mem + " percent")
                + ", " + (g.temp < 0 ? "temperature unknown" : g.temp + " degrees Celsius");
    }

    private float density() {
        return getResources().getDisplayMetrics().density;
    }

    /** Height of the ring block, px, for the current GPU count. */
    private int blockHeight() {
        int n = gpus.length;
        float k = D / 148f, maxY = CPU_AT[n][1];
        for (float[] p : GPU_AT[n]) maxY = Math.max(maxY, p[1]);
        return Math.round((maxY * k + D) * density());
    }

    @Override
    protected void onMeasure(int wSpec, int hSpec) {
        setMeasuredDimension(MeasureSpec.getSize(wSpec), blockHeight());
    }

    @Override
    protected void onDraw(Canvas c) {
        int n = gpus.length;
        float d = D * density(), k = d / 148f;
        float maxX = CPU_AT[n][0];
        for (float[] p : GPU_AT[n]) maxX = Math.max(maxX, p[0]);
        float left = (getWidth() - (maxX * k + d)) / 2f;
        for (int i = 0; i < n; i++) {
            ring(c, left + GPU_AT[n][i][0] * k + d / 2, GPU_AT[n][i][1] * k + d / 2, k, gpus[i]);
        }
        ring(c, left + CPU_AT[n][0] * k + d / 2, CPU_AT[n][1] * k + d / 2, k, cpu);
    }

    /** One ring centred on (cx, cy); k = px per kiosk unit. */
    private void ring(Canvas c, float cx, float cy, float k, Gauge g) {
        float out = 74 * k, thick = 12 * k, mid = out - thick / 2;
        oval.set(cx - mid, cy - mid, cx + mid, cy + mid);
        arc.setStrokeWidth(thick);
        // top half: usage, filling from both sides (Android angles: 0 = 3 o'clock, clockwise)
        arc.setColor(EMPTY);
        c.drawArc(oval, -90 - SIDE, 2 * SIDE, false, arc);
        if (g.usage > 0) {
            float sweep = SIDE * Math.min(g.usage, 100) / 100f;
            arc.setColor(g.color);
            c.drawArc(oval, -90 - SIDE, sweep, false, arc);
            c.drawArc(oval, -90 + SIDE - sweep, sweep, false, arc);
        }
        // bottom half: memory, a flat liquid surface rising from the bottom to the rim at 100%
        arc.setColor(EMPTY);
        c.drawArc(oval, 90 - SIDE, 2 * SIDE, false, arc);
        if (g.mem > 0) {
            float level = cy + out * (1 - Math.min(g.mem, 100) / 100f);
            c.save();
            c.clipRect(0, level, getWidth(), getHeight());
            arc.setColor(g.color);
            c.drawArc(oval, 90 - SIDE, 2 * SIDE, false, arc);
            c.restore();
        }
        label.setTextSize(16 * k);
        label.setColor(g.color);
        c.drawText(g.name, cx, cy - 6 * k, label);
        temp.setTextSize(23 * k);
        temp.setColor(g.temp < 0 ? DIM : g.temp >= 85 ? BAD : g.temp >= 70 ? WARN : TEXT);
        c.drawText(g.temp < 0 ? "--" : g.temp + " °C", cx, cy + 18 * k, temp);
    }
}
