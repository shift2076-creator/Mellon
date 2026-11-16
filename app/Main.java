package mellon;

import android.app.Activity;
import android.content.SharedPreferences;
import android.hardware.biometrics.BiometricManager;
import android.hardware.biometrics.BiometricPrompt;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Bundle;
import android.os.CancellationSignal;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Log;
import android.view.WindowManager;
import android.widget.TextView;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.cert.Certificate;
import java.security.spec.ECGenParameterSpec;
import java.util.Arrays;
import javax.security.auth.x500.X500Principal;

/**
 * Minimal U2F authenticator. Each credential is its own Android Keystore key
 * that needs a fresh fingerprint per signature. Serves "2-byte length + APDU"
 * requests on the abstract socket @mellon (reach it with adb forward).
 * U2F hosts poll: we answer 0x6985 while the fingerprint prompt is open.
 */
public class Main extends Activity {
    private static final byte[] OK = {(byte) 0x90, 0};
    private KeyStore ks;
    private SharedPreferences prefs;
    private byte[] pending;          // request waiting on a fingerprint
    private int state;               // 0 waiting, 1 approved, -1 refused
    private Signature approved;      // signature unlocked by the fingerprint
    private CancellationSignal cancel;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        TextView t = new TextView(this);
        t.setText("Mellon: listening on @mellon");
        setContentView(t);
        prefs = getSharedPreferences("m", MODE_PRIVATE);
        new Thread(this::serve).start();
    }

    private void serve() {
        try {
            ks = KeyStore.getInstance("AndroidKeyStore");
            ks.load(null);
            LocalServerSocket srv = new LocalServerSocket("mellon");
            while (true) {
                try (LocalSocket c = srv.accept()) {
                    DataInputStream in = new DataInputStream(c.getInputStream());
                    byte[] req = new byte[in.readUnsignedShort()];
                    in.readFully(req);
                    byte[] res;
                    try {
                        res = handle(req);
                    } catch (Exception e) {
                        Log.e("mellon", "request failed", e);
                        res = sw(0x6F00);
                    }
                    DataOutputStream out = new DataOutputStream(c.getOutputStream());
                    out.writeShort(res.length);
                    out.write(res);
                    out.flush();
                } catch (IOException e) {
                    // client went away; keep serving
                }
            }
        } catch (Exception e) {
            // socket name taken (second instance) or keystore unavailable
            Log.e("mellon", "server stopped", e);
        }
    }

    private byte[] handle(byte[] a) throws Exception {
        int off = 4, lc = 0;
        if (a.length >= 7 && a[4] == 0) {
            lc = ((a[5] & 255) << 8) | (a[6] & 255);
            off = 7;
        } else if (a.length > 4) {
            lc = a[4] & 255;
            off = 5;
        }
        if (a.length < 4 || off + lc > a.length) return sw(0x6700);
        byte[] d = Arrays.copyOfRange(a, off, off + lc);
        switch (a[1]) {
            case 3: return cat("U2F_V2".getBytes(), OK);
            case 1: return d.length == 64 ? register(a, d) : sw(0x6700);
            case 2: return d.length > 65 ? auth(a, d) : sw(0x6700);
            default: return sw(0x6D00);
        }
    }

    private byte[] register(byte[] a, byte[] d) throws Exception {
        int r = gate(a, null);
        if (r != 1) return sw(r == 0 ? 0x6985 : 0x6A80);
        byte[] app = Arrays.copyOfRange(d, 32, 64), kh = new byte[16];
        new SecureRandom().nextBytes(kh);
        String alias = hex(kh);
        KeyPairGenerator g = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore");
        g.initialize(new KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setUserAuthenticationRequired(true)
                .setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG)
                .build());
        byte[] enc = g.generateKeyPair().getPublic().getEncoded();
        byte[] pub = Arrays.copyOfRange(enc, enc.length - 65, enc.length); // 04||X||Y
        prefs.edit().putString(alias, hex(app)).apply();
        Certificate cert = attestation();
        ByteBuffer m = ByteBuffer.allocate(1 + 32 + 32 + 16 + 65);
        m.put((byte) 0).put(app).put(d, 0, 32).put(kh).put(pub);
        Signature s = Signature.getInstance("SHA256withECDSA");
        s.initSign((PrivateKey) ks.getKey("att", null));
        s.update(m.array());
        return cat(new byte[] {5}, pub, new byte[] {16}, kh, cert.getEncoded(), s.sign(), OK);
    }

    private byte[] auth(byte[] a, byte[] d) throws Exception {
        int kl = d[64] & 255;
        if (d.length != 65 + kl) return sw(0x6700);
        byte[] app = Arrays.copyOfRange(d, 32, 64), kh = Arrays.copyOfRange(d, 65, 65 + kl);
        String alias = hex(kh);
        if (!hex(app).equals(prefs.getString(alias, null)) || !ks.containsAlias(alias)) {
            return sw(0x6A80);
        }
        if (a[2] == 7) return sw(0x6985); // check-only: key is ours
        Signature s = Signature.getInstance("SHA256withECDSA");
        s.initSign((PrivateKey) ks.getKey(alias, null));
        int r = gate(a, s);
        if (r != 1) return sw(r == 0 ? 0x6985 : 0x6A80);
        long ctr = prefs.getLong("ctr", 0) + 1;
        prefs.edit().putLong("ctr", ctr).apply();
        byte[] c = ByteBuffer.allocate(4).putInt((int) ctr).array();
        Signature u = approved;
        u.update(app);
        u.update((byte) 1);
        u.update(c);
        u.update(d, 0, 32);
        return cat(new byte[] {1}, c, u.sign(), OK);
    }

    private Certificate attestation() throws Exception {
        if (!ks.containsAlias("att")) {
            KeyPairGenerator g = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore");
            g.initialize(new KeyGenParameterSpec.Builder("att", KeyProperties.PURPOSE_SIGN)
                    .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                    .setDigests(KeyProperties.DIGEST_SHA256)
                    .setCertificateSubject(new X500Principal("CN=Mellon"))
                    .build());
            g.generateKeyPair();
        }
        return ks.getCertificate("att");
    }

    /** 1 = fingerprint accepted, 0 = waiting (prompt open), -1 = refused. */
    private synchronized int gate(byte[] req, Signature s) {
        if (pending != null && Arrays.equals(req, pending)) {
            if (state == 0) return 0;
            int r = state;
            pending = null;
            return r;
        }
        if (cancel != null) cancel.cancel();
        final byte[] mine = req.clone();
        final CancellationSignal cs = new CancellationSignal();
        pending = mine;
        cancel = cs;
        state = 0;
        approved = null;
        runOnUiThread(() -> prompt(s, mine, cs));
        return 0;
    }

    private void prompt(Signature s, byte[] mine, CancellationSignal cs) {
        BiometricPrompt.AuthenticationCallback cb = new BiometricPrompt.AuthenticationCallback() {
            @Override
            public void onAuthenticationSucceeded(BiometricPrompt.AuthenticationResult r) {
                done(mine, 1, s == null ? null : r.getCryptoObject().getSignature());
            }

            @Override
            public void onAuthenticationError(int code, CharSequence msg) {
                done(mine, -1, null);
            }
        };
        BiometricPrompt p = new BiometricPrompt.Builder(this)
                .setTitle("Mellon")
                .setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG)
                .setNegativeButton("Cancel", getMainExecutor(), (dlg, w) -> done(mine, -1, null))
                .build();
        if (s == null) {
            p.authenticate(cs, getMainExecutor(), cb);
        } else {
            p.authenticate(new BiometricPrompt.CryptoObject(s), cs, getMainExecutor(), cb);
        }
    }

    private synchronized void done(byte[] mine, int st, Signature s) {
        if (pending == mine) { // ignore callbacks from superseded prompts
            state = st;
            approved = s;
        }
    }

    private static byte[] cat(byte[]... parts) {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        for (byte[] p : parts) o.write(p, 0, p.length);
        return o.toByteArray();
    }

    private static byte[] sw(int s) {
        return new byte[] {(byte) (s >> 8), (byte) s};
    }

    private static String hex(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format("%02x", x));
        return s.toString();
    }
}
