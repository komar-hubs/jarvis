# J.A.R.V.I.S. Remote — APK build without Gradle. © 2026 komar-hubs, KotikVanil. MIT License.
# Original: https://github.com/komar-hubs/jarvis
"""Builds android/jarvis-remote.apk (the Android remote, only for Android) from android/: aapt2 -> javac -> d8 ->
zipalign -> apksigner. Needs JDK 17+ and the Android SDK (build-tools 35+, platform 35):
  python android/build.py
Paths: ANDROID_HOME / JAVA_HOME, else %USERPROFILE%\\Android\\sdk and the newest JDK in Program Files\\Microsoft.
The signing key is created once in data/android/ (never commit it: the same key is needed to update the app)."""
import os
import secrets
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "jarvis-remote.apk"
WORK = ROOT / "data" / "android"
BUILD = WORK / "build"


def find_sdk():
    for p in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"), Path.home() / "Android" / "sdk",
              Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk"):
        if p and (Path(p) / "platforms").exists():
            return Path(p)
    sys.exit("Android SDK не найден (ANDROID_HOME)")


def find_jdk():
    cands = [os.environ.get("JAVA_HOME")] + sorted(Path(r"C:\Program Files\Microsoft").glob("jdk-*"), reverse=True) \
        + sorted(Path(r"C:\Program Files\Java").glob("jdk-*"), reverse=True)
    for p in cands:
        if p and (Path(p) / "bin" / "javac.exe").exists():
            return Path(p)
    sys.exit("JDK не найден (JAVA_HOME)")


def newest(d, need):
    for p in sorted(d.iterdir(), key=lambda p: [int(x) if x.isdigit() else 0 for x in p.name.replace("-", ".").split(".")], reverse=True):
        if all((p / n).exists() for n in need):
            return p
    sys.exit(f"нет {need} в {d}")


def run(*cmd, env=None):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        sys.exit(f"Ошибка: {' '.join(map(str, cmd[:2]))}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def make_icons():
    """The launcher icon: a small arc reactor (drawn once with Pillow, then kept in res/)."""
    from PIL import Image, ImageDraw, ImageFilter
    sizes = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    big = 768
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = big / 2
    d.ellipse((12, 12, big - 12, big - 12), fill=(4, 19, 31, 255), outline=(79, 216, 255, 255), width=14)
    glow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    for i in range(10):                                   # ring segments
        a0 = i * 36 + 4
        g.arc((110, 110, big - 110, big - 110), a0, a0 + 28, fill=(79, 216, 255, 255), width=46)
    g.ellipse((c - 150, c - 150, c + 150, c + 150), outline=(191, 244, 255, 255), width=14)
    g.ellipse((c - 92, c - 92, c + 92, c + 92), fill=(140, 230, 255, 255))
    g.ellipse((c - 52, c - 52, c + 52, c + 52), fill=(255, 255, 255, 255))
    blur = glow.filter(ImageFilter.GaussianBlur(22))
    im = Image.alpha_composite(im, blur)
    im = Image.alpha_composite(im, glow)
    for name, px in sizes.items():
        p = HERE / "res" / f"mipmap-{name}" / "ic_launcher.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        im.resize((px, px), Image.LANCZOS).save(p)


def main():
    sdk, jdk = find_sdk(), find_jdk()
    bt = newest(sdk / "build-tools", ["aapt2.exe", "d8.bat", "zipalign.exe", "apksigner.bat"])
    plat = sdk / "platforms" / "android-35"           # the same API level as targetSdkVersion
    if not (plat / "android.jar").exists():
        plat = newest(sdk / "platforms", ["android.jar"])
    jar = plat / "android.jar"
    env = dict(os.environ, JAVA_HOME=str(jdk), PATH=str(jdk / "bin") + os.pathsep + os.environ.get("PATH", ""))
    if not (HERE / "res" / "mipmap-xxxhdpi" / "ic_launcher.png").exists() or "--icons" in sys.argv:
        make_icons()
    shutil.rmtree(BUILD, ignore_errors=True)
    (BUILD / "classes").mkdir(parents=True)
    (BUILD / "dex").mkdir()
    # 1. resources + manifest + assets
    run(bt / "aapt2.exe", "compile", "--dir", HERE / "res", "-o", BUILD / "res.zip")
    ver = int(time.time() // 60)          # grows with every build: a new APK installs over the old one
    run(bt / "aapt2.exe", "link", "-I", jar, "--manifest", HERE / "AndroidManifest.xml", "-A", HERE / "assets",
        "--version-code", ver, "--version-name", time.strftime("%Y.%m.%d"), "--replace-version", "-o", BUILD / "base.apk", BUILD / "res.zip")
    # 2. java -> dex
    srcs = [str(p) for p in (HERE / "src").rglob("*.java")]
    run(jdk / "bin" / "javac.exe", "-encoding", "UTF-8", "-source", "8", "-target", "8", "-nowarn", "-Xlint:-options",
        "-bootclasspath", jar, "-classpath", bt / "core-lambda-stubs.jar", "-d", BUILD / "classes", *srcs, env=env)
    classes = [str(p) for p in (BUILD / "classes").rglob("*.class")]
    run(bt / "d8.bat", "--release", "--min-api", "24", "--lib", jar, "--output", BUILD / "dex", *classes, env=env)
    with zipfile.ZipFile(BUILD / "base.apk", "a", zipfile.ZIP_DEFLATED) as z:
        z.write(BUILD / "dex" / "classes.dex", "classes.dex")
    # 3. align + sign
    run(bt / "zipalign.exe", "-p", "-f", "4", BUILD / "base.apk", BUILD / "aligned.apk")
    ks, pw_file = WORK / "remote.keystore", WORK / "keystore.txt"
    if not ks.exists():
        pw = secrets.token_urlsafe(18)
        run(jdk / "bin" / "keytool.exe", "-genkeypair", "-keystore", ks, "-alias", "jarvis", "-keyalg", "RSA",
            "-keysize", "2048", "-validity", "36500", "-storepass", pw, "-keypass", pw,
            "-dname", "CN=komar-hubs, OU=J.A.R.V.I.S. Remote, O=komar-hubs, L=github.com/komar-hubs/jarvis, C=RU", env=env)
        pw_file.write_text(pw, encoding="utf-8")
    pw = pw_file.read_text(encoding="utf-8").strip()
    run(bt / "apksigner.bat", "sign", "--ks", ks, "--ks-key-alias", "jarvis", "--ks-pass", "pass:" + pw,
        "--key-pass", "pass:" + pw, "--min-sdk-version", "24",
        # all three schemes: some phones' installers (Xiaomi, old Samsung) call a v2-only APK "damaged"
        "--v1-signing-enabled", "true", "--v2-signing-enabled", "true", "--v3-signing-enabled", "true",
        "--v4-signing-enabled", "false", "--out", OUT, BUILD / "aligned.apk", env=env)
    run(bt / "apksigner.bat", "verify", OUT, env=env)
    for f in OUT.parent.glob("jarvis-remote.apk.idsig"):
        f.unlink()
    import hashlib
    sha = hashlib.sha256(OUT.read_bytes()).hexdigest()
    with open(HERE / "jarvis-remote.sha256", "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{sha}  jarvis-remote.apk\n")
    print(run(bt / "apksigner.bat", "verify", "--verbose", OUT, env=env).splitlines()[:4])
    print(f"OK: {OUT} ({OUT.stat().st_size // 1024} КБ), версия {ver}, SHA-256 {sha}")


if __name__ == "__main__":
    main()
