#!/bin/sh
# Build mellon.apk with only Android SDK build-tools + platform jar (no Gradle).
# Needs: ANDROID_HOME (build-tools + one platform), JDK 17+, python3.
# Debug-signing only: the keystore password below protects nothing.
set -e
: "${ANDROID_HOME:?set ANDROID_HOME to your Android SDK}"
BT=$(ls -d "$ANDROID_HOME"/build-tools/* | sort -V | tail -1)
JAR=$(ls -d "$ANDROID_HOME"/platforms/android-* | sort -V | tail -1)/android.jar
KS=${MELLON_KEYSTORE:-$HOME/.mellon.keystore}

rm -rf build && mkdir -p build/classes
javac --release 8 -cp "$JAR" -d build/classes app/*.java
"$BT/d8" --lib "$JAR" --min-api 28 --output build build/classes/mellon/*.class
"$BT/aapt2" link -o build/u.apk --manifest app/AndroidManifest.xml -I "$JAR" \
    --min-sdk-version 28 --target-sdk-version 35 --version-code 1 --version-name 0.1
python3 -c "import zipfile; zipfile.ZipFile('build/u.apk','a').write('build/classes.dex','classes.dex')"
"$BT/zipalign" -f 4 build/u.apk build/a.apk
[ -f "$KS" ] || keytool -genkeypair -keystore "$KS" -storepass android -keypass android \
    -alias mellon -dname CN=Mellon -keyalg RSA -keysize 2048 -validity 10000
"$BT/apksigner" sign --ks "$KS" --ks-pass pass:android --out mellon.apk build/a.apk
echo "built mellon.apk"
