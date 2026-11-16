// J.A.R.V.I.S. Remote — the Android app. © 2026 komar-hubs, KotikVanil. MIT License.
// Original: https://github.com/komar-hubs/jarvis
//
// A full-screen landscape WebView with the holo remote (assets/index.html) and a small bridge "Native":
// UDP discovery of PCs, HTTP to the PC (the page is file:// and cannot read cross-origin answers),
// settings, haptics and push-to-talk (AudioRecord 16 kHz mono PCM sent to the PC's /voice).
package com.jarvis.remote;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.view.HapticFeedbackConstants;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.util.HashSet;
import java.util.Set;

public class MainActivity extends Activity {
    private static final int SR = 16000;
    private static final int MAX_SECONDS = 15;
    private WebView web;
    private SharedPreferences prefs;
    private volatile boolean recording = false;
    private ByteArrayOutputStream pcm;
    private Thread recThread;
    private volatile boolean playing = false;
    private volatile HttpURLConnection soundConn;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN | WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        prefs = getSharedPreferences("remote", MODE_PRIVATE);
        web = new WebView(this);
        web.setBackgroundColor(0xFF01060A);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setTextZoom(100);
        web.setWebViewClient(new WebViewClient());
        web.setWebChromeClient(new WebChromeClient());
        web.setHapticFeedbackEnabled(true);
        web.addJavascriptInterface(new Bridge(), "Native");
        setContentView(web);
        immersive();
        web.loadUrl("file:///android_asset/index.html");
    }

    private void immersive() {
        web.setSystemUiVisibility(View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN);
    }

    @Override
    public void onWindowFocusChanged(boolean focus) {
        super.onWindowFocusChanged(focus);
        if (focus) immersive();
    }

    @Override
    public void onBackPressed() {
        web.evaluateJavascript("window.onBack && window.onBack()", v -> {
            if (!"true".equals(v)) MainActivity.super.onBackPressed();
        });
    }

    @Override
    protected void onPause() {
        super.onPause();
        recording = false;
    }

    private void js(final String code) {
        web.post(() -> web.evaluateJavascript(code, null));
    }

    private static String q(String s) {
        return JSONObject.quote(s == null ? "" : s);
    }

    // ------------------------------------------------------------------ the bridge for the page
    class Bridge {
        @JavascriptInterface
        public String get(String key) {
            return prefs.getString(key, null);
        }

        @JavascriptInterface
        public void put(String key, String value) {
            prefs.edit().putString(key, value).apply();
        }

        @JavascriptInterface
        public int appVersion() {
            try {
                return getPackageManager().getPackageInfo(getPackageName(), 0).versionCode;
            } catch (Exception e) {
                return 0;
            }
        }

        /** Opens a link outside the app: the new APK from the PC downloads in the phone's browser / downloader. */
        @JavascriptInterface
        public void openUrl(final String url) {
            runOnUiThread(() -> {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, android.net.Uri.parse(url)));
                } catch (Exception ignored) {
                }
            });
        }

        @JavascriptInterface
        public String deviceName() {
            String m = Build.MODEL == null ? "Android" : Build.MODEL;
            String b = Build.MANUFACTURER == null ? "" : Build.MANUFACTURER;
            if (!b.isEmpty() && !m.toLowerCase().startsWith(b.toLowerCase()))
                m = Character.toUpperCase(b.charAt(0)) + b.substring(1) + " " + m;
            return m;
        }

        /** A light tap of the vibro motor through the view's haptics: needs no VIBRATE permission. */
        @JavascriptInterface
        public void vibrate(final int ms) {
            web.post(() -> web.performHapticFeedback(ms >= 20 ? HapticFeedbackConstants.LONG_PRESS
                    : HapticFeedbackConstants.KEYBOARD_TAP, HapticFeedbackConstants.FLAG_IGNORE_VIEW_SETTING));
        }

        @JavascriptInterface
        public void http(final int id, final String method, final String url, final String body, final String token) {
            new Thread(() -> {
                int status = 0;
                String text = "";
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                    c.setConnectTimeout(2500);
                    c.setReadTimeout(5000);
                    c.setRequestMethod(method);
                    if (token != null && !token.isEmpty()) c.setRequestProperty("X-Token", token);
                    if (body != null && !body.isEmpty()) {
                        c.setDoOutput(true);
                        c.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                        OutputStream o = c.getOutputStream();
                        o.write(body.getBytes("UTF-8"));
                        o.close();
                    }
                    status = c.getResponseCode();
                    InputStream in = status >= 400 ? c.getErrorStream() : c.getInputStream();
                    text = in == null ? "" : read(in);
                    c.disconnect();
                } catch (Exception e) {
                    status = 0;
                }
                js("window.__nat(" + id + "," + status + "," + q(text) + ")");
            }, "http").start();
        }

        /** A broadcast "JARVIS-REMOTE?" to the Jarvis port + 1; every PC that answers goes to window.onFound. */
        @JavascriptInterface
        public void discover() {
            new Thread(() -> {
                try {
                    WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
                    DatagramSocket sock = new DatagramSocket();
                    sock.setBroadcast(true);
                    sock.setSoTimeout(400);
                    byte[] hello = "JARVIS-REMOTE?".getBytes("UTF-8");
                    Set<String> targets = new HashSet<>();
                    targets.add("255.255.255.255");
                    if (wm != null && wm.getDhcpInfo() != null) {
                        int ip = wm.getDhcpInfo().ipAddress, mask = wm.getDhcpInfo().netmask;
                        if (ip != 0) {
                            if (mask == 0) mask = 0x00FFFFFF;      // little-endian 255.255.255.0
                            int bc = (ip & mask) | ~mask;
                            targets.add((bc & 0xFF) + "." + ((bc >> 8) & 0xFF) + "." + ((bc >> 16) & 0xFF) + "." + ((bc >> 24) & 0xFF));
                        }
                    }
                    long end = System.currentTimeMillis() + 1800;
                    int round = 0;
                    byte[] buf = new byte[512];
                    while (System.currentTimeMillis() < end) {
                        if (round++ % 3 == 0)
                            for (String t : targets)
                                for (int port = 8791; port <= 8793; port += 2)
                                    try {
                                        sock.send(new DatagramPacket(hello, hello.length, InetAddress.getByName(t), port));
                                    } catch (Exception ignored) {
                                    }
                        try {
                            DatagramPacket p = new DatagramPacket(buf, buf.length);
                            sock.receive(p);
                            JSONObject j = new JSONObject(new String(p.getData(), 0, p.getLength(), "UTF-8"));
                            j.put("host", p.getAddress().getHostAddress());
                            js("window.onFound(" + q(j.toString()) + ")");
                        } catch (SocketTimeoutException ignored) {
                        } catch (Exception ignored) {
                        }
                    }
                    sock.close();
                } catch (Exception ignored) {
                } finally {
                    js("window.onFoundDone && window.onFoundDone()");
                }
            }, "discover").start();
        }

        /** A screenshot from the PC into the gallery (Pictures/JARVIS). Android 10+: MediaStore, no permission;
         *  older phones: the app's own Pictures folder. Calls window.onSaved(true|false). */
        @JavascriptInterface
        public void saveImage(final String url) {
            new Thread(() -> {
                boolean ok = false;
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                    c.setConnectTimeout(3000);
                    c.setReadTimeout(15000);
                    InputStream in = c.getInputStream();
                    ByteArrayOutputStream buf = new ByteArrayOutputStream();
                    byte[] b = new byte[16384];
                    int n;
                    while ((n = in.read(b)) > 0) buf.write(b, 0, n);
                    in.close();
                    c.disconnect();
                    String name = "jarvis_" + new java.text.SimpleDateFormat("yyyyMMdd_HHmmss", java.util.Locale.US).format(new java.util.Date()) + ".jpg";
                    OutputStream out;
                    if (Build.VERSION.SDK_INT >= 29) {
                        android.content.ContentValues v = new android.content.ContentValues();
                        v.put(android.provider.MediaStore.Images.Media.DISPLAY_NAME, name);
                        v.put(android.provider.MediaStore.Images.Media.MIME_TYPE, "image/jpeg");
                        v.put(android.provider.MediaStore.Images.Media.RELATIVE_PATH, "Pictures/JARVIS");
                        android.net.Uri uri = getContentResolver().insert(android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI, v);
                        out = uri == null ? null : getContentResolver().openOutputStream(uri);
                    } else {
                        java.io.File dir = getExternalFilesDir(android.os.Environment.DIRECTORY_PICTURES);
                        out = dir == null ? null : new java.io.FileOutputStream(new java.io.File(dir, name));
                    }
                    if (out != null) {
                        out.write(buf.toByteArray());
                        out.close();
                        ok = true;
                    }
                } catch (Exception ignored) {
                }
                js("window.onSaved && window.onSaved(" + ok + ")");
            }, "save").start();
        }

        /** Wake-on-LAN: 6x FF + the MAC 16 times, to the LAN broadcasts on ports 9 and 7, three rounds.
         *  json = {"macs": ["4C-82-..."], "bcast": ["192.168.1.255"]}. Returns how many packets went out. */
        @JavascriptInterface
        public int wake(final String json) {
            int sent = 0;
            try {
                JSONObject j = new JSONObject(json);
                org.json.JSONArray macs = j.optJSONArray("macs"), bc = j.optJSONArray("bcast");
                Set<String> targets = new HashSet<>();
                targets.add("255.255.255.255");
                if (bc != null) for (int i = 0; i < bc.length(); i++) targets.add(bc.getString(i));
                DatagramSocket sock = new DatagramSocket();
                sock.setBroadcast(true);
                for (int round = 0; round < 3; round++) {
                    for (int m = 0; macs != null && m < macs.length(); m++) {
                        String[] hx = macs.getString(m).split("[-:]");
                        if (hx.length != 6) continue;
                        byte[] mac = new byte[6];
                        for (int i = 0; i < 6; i++) mac[i] = (byte) Integer.parseInt(hx[i], 16);
                        byte[] pkt = new byte[102];
                        for (int i = 0; i < 6; i++) pkt[i] = (byte) 0xFF;
                        for (int i = 6; i < 102; i += 6) System.arraycopy(mac, 0, pkt, i, 6);
                        for (String t : targets)
                            for (int port : new int[]{9, 7})
                                try { sock.send(new DatagramPacket(pkt, pkt.length, InetAddress.getByName(t), port)); sent++; } catch (Exception ignored) { }
                    }
                    try { Thread.sleep(120); } catch (InterruptedException ignored) { }
                }
                sock.close();
            } catch (Exception ignored) {
            }
            return sent;
        }

        /** The PC's sound: a raw PCM16 stream (X-Rate, X-Channels) played through an AudioTrack as it comes. Calls
         *  window.onAudio(true) once it plays and window.onAudio(false, reason) when it stops. */
        @JavascriptInterface
        public void audioStart(final String url) {
            if (playing) return;
            playing = true;
            new Thread(() -> {
                AudioTrack track = null;
                String why = "stop";
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                    soundConn = c;
                    c.setConnectTimeout(3000);
                    c.setReadTimeout(0);                    // silence on the PC sends nothing for a while
                    int st = c.getResponseCode();
                    if (st != 200) { why = st == 401 ? "auth" : st == 429 ? "busy" : "pc"; throw new java.io.IOException("http " + st); }
                    int rate = Integer.parseInt(c.getHeaderField("X-Rate") == null ? "24000" : c.getHeaderField("X-Rate"));
                    int ch = "1".equals(c.getHeaderField("X-Channels")) ? AudioFormat.CHANNEL_OUT_MONO : AudioFormat.CHANNEL_OUT_STEREO;
                    int bytesPerSec = rate * 2 * (ch == AudioFormat.CHANNEL_OUT_MONO ? 1 : 2);
                    int min = AudioTrack.getMinBufferSize(rate, ch, AudioFormat.ENCODING_PCM_16BIT);
                    track = new AudioTrack.Builder()
                            .setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                                    .setContentType(AudioAttributes.CONTENT_TYPE_MOVIE).build())
                            .setAudioFormat(new AudioFormat.Builder().setSampleRate(rate).setChannelMask(ch)
                                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                            .setBufferSizeInBytes(Math.max(min * 2, bytesPerSec / 5))          // ~200 ms
                            .setTransferMode(AudioTrack.MODE_STREAM).build();
                    track.play();
                    js("window.onAudio && window.onAudio(true)");
                    InputStream in = c.getInputStream();
                    byte[] buf = new byte[4096];
                    int carry = 0;
                    while (playing) {
                        int n = in.read(buf, carry, buf.length - carry);
                        if (n < 0) { why = "pc"; break; }
                        n += carry;
                        int whole = n - n % 4;              // never split a stereo frame
                        // fell behind (a Wi-Fi hiccup): skip what piled up instead of playing it late
                        if (in.available() > bytesPerSec / 2) { in.skip(in.available() - in.available() % 4); carry = 0; continue; }
                        if (whole > 0) track.write(buf, 0, whole);
                        carry = n - whole;
                        if (carry > 0) System.arraycopy(buf, whole, buf, 0, carry);
                    }
                    c.disconnect();
                } catch (Exception e) {
                    if (playing && "stop".equals(why)) why = "net";
                } finally {
                    if (track != null) { try { track.stop(); } catch (Exception ignored) { } track.release(); }
                    boolean wasOn = playing;
                    playing = false;
                    soundConn = null;
                    js("window.onAudio && window.onAudio(false, " + q(wasOn ? why : "stop") + ")");
                }
            }, "pc-sound").start();
        }

        @JavascriptInterface
        public void audioStop() {
            playing = false;
            HttpURLConnection c = soundConn;
            if (c != null) new Thread(() -> { try { c.disconnect(); } catch (Exception ignored) { } }).start();
        }

        /** "ok", "perm" (asked for the microphone, hold again) or "busy". */
        @JavascriptInterface
        public String startVoice() {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                runOnUiThread(() -> requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 1));
                return "perm";
            }
            if (recording) return "busy";
            final int min = AudioRecord.getMinBufferSize(SR, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
            final AudioRecord rec;
            try {
                rec = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, SR, AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT, Math.max(min, SR));
                if (rec.getState() != AudioRecord.STATE_INITIALIZED) return "busy";
                rec.startRecording();
            } catch (Exception e) {
                return "busy";
            }
            pcm = new ByteArrayOutputStream();
            recording = true;
            recThread = new Thread(() -> {
                byte[] b = new byte[1600];        // 50 ms
                long t0 = System.currentTimeMillis();
                while (recording && System.currentTimeMillis() - t0 < MAX_SECONDS * 1000) {
                    int n = rec.read(b, 0, b.length);
                    if (n <= 0) continue;
                    pcm.write(b, 0, n);
                    long sum = 0;
                    for (int i = 0; i + 1 < n; i += 2) {
                        int v = (short) ((b[i] & 0xFF) | (b[i + 1] << 8));
                        sum += (long) v * v;
                    }
                    double rms = Math.sqrt(sum / Math.max(1.0, n / 2.0)) / 32768.0;
                    js("window.onLevel(" + String.format(java.util.Locale.US, "%.3f", Math.min(1.0, rms * 4)) + ")");
                }
                try { rec.stop(); } catch (Exception ignored) { }
                rec.release();
                recording = false;
            }, "voice");
            recThread.start();
            return "ok";
        }

        @JavascriptInterface
        public void stopVoice(final String url, final String token) {
            recording = false;
            new Thread(() -> {
                try {
                    if (recThread != null) recThread.join(1500);
                } catch (InterruptedException ignored) {
                }
                byte[] data = pcm == null ? new byte[0] : pcm.toByteArray();
                if (data.length < SR * 2 * 3 / 10) {          // shorter than 0.3 s: a slip of the thumb
                    js("window.onVoice(" + q("{\"error\":\"short\"}") + ")");
                    return;
                }
                String text;
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                    c.setConnectTimeout(3000);
                    c.setReadTimeout(20000);
                    c.setRequestMethod("POST");
                    c.setDoOutput(true);
                    c.setRequestProperty("X-Token", token);
                    c.setRequestProperty("Content-Type", "application/octet-stream");
                    c.setFixedLengthStreamingMode(data.length);
                    OutputStream o = c.getOutputStream();
                    o.write(data);
                    o.close();
                    int st = c.getResponseCode();
                    InputStream in = st >= 400 ? c.getErrorStream() : c.getInputStream();
                    text = in == null ? "{}" : read(in);
                    c.disconnect();
                } catch (Exception e) {
                    text = "{\"error\":\"net\"}";
                }
                js("window.onVoice(" + q(text) + ")");
            }, "voice-send").start();
        }
    }

    private static String read(InputStream in) throws java.io.IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] b = new byte[4096];
        int n;
        while ((n = in.read(b)) > 0) out.write(b, 0, n);
        in.close();
        return out.toString("UTF-8");
    }
}
