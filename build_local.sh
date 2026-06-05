#!/bin/bash

# Exit immediately if any command fails
set -e

# 1. Load credentials
if [ ! -f "credentials.sh" ]; then
    echo "ERROR: credentials.sh not found! Please create it first."
    exit 1
fi
source credentials.sh

echo "========================================="
echo "Starting local Intel macOS Build Process"
echo "========================================="

# 2. Install/Verify homebrew and ffmpeg if missing
if ! command -v brew &> /dev/null; then
    echo "Homebrew is not installed. Please install it from https://brew.sh/"
    exit 1
fi

if ! command -v ffmpeg &> /dev/null; then
    echo "Installing ffmpeg..."
    brew install ffmpeg
else
    echo "ffmpeg is already installed."
fi

# 3. Set up Python Virtual Environment and install dependencies
echo "Setting up Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

# 4. Build the executable
echo "Building executable with PyInstaller..."
pyinstaller Move-It.spec --clean --noconfirm

# Verify build output
if [ ! -d "dist/Move-It.app" ]; then
    echo "ERROR: PyInstaller build failed! dist/Move-It.app not found."
    exit 1
fi

# Make sure executable is runnable
chmod +x dist/Move-It.app/Contents/MacOS/Move-It

# 5. Code Sign the Application
echo "Locating Developer ID Application certificate in Keychain..."
CERT_IDENTITY=$(security find-identity -v -p codesigning | grep "Developer ID Application" | sed -n 's/.*"\(.*\)".*/\1/p' | head -n 1)

if [ -z "$CERT_IDENTITY" ]; then
    echo "ERROR: No 'Developer ID Application' certificate found in Keychain!"
    echo "Please make sure you double-clicked the .p12 file and imported it."
    exit 1
fi
echo "Using certificate identity: $CERT_IDENTITY"

# Create entitlements plist for Hardened Runtime
cat << 'EOF' > entitlements.plist
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.allow-jit</key>
    <true/>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
    <key>com.apple.security.device.camera</key>
    <true/>
    <key>com.apple.security.device.microphone</key>
    <true/>
</dict>
</plist>
EOF

echo "Signing internal binaries..."
find dist/Move-It.app -type f | while read -r file; do
  if file "$file" | grep -q "Mach-O"; then
    codesign --force --sign "$CERT_IDENTITY" --options runtime --entitlements entitlements.plist "$file" || true
  fi
done

echo "Signing the main app bundle..."
codesign --deep --force --sign "$CERT_IDENTITY" --options runtime --entitlements entitlements.plist dist/Move-It.app

echo "Verifying code signature..."
codesign -dv --verbose=4 dist/Move-It.app

# 6. Notarize the App
echo "Creating ZIP archive for notarization..."
ditto -c -k --keepParent dist/Move-It.app dist/Move-It-notarize.zip

echo "Storing credentials in temporary notarytool profile..."
xcrun notarytool store-credentials "local-notary-creds" \
    --apple-id "$APPLE_ID" \
    --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_PASSWORD"

echo "Submitting app for notarization..."
SUBMIT_OUTPUT=$(xcrun notarytool submit dist/Move-It-notarize.zip --keychain-profile "local-notary-creds" --wait 2>&1)
echo "$SUBMIT_OUTPUT"

# Verify notarization succeeded
if echo "$SUBMIT_OUTPUT" | grep -q "status: Accepted"; then
    echo "Notarization succeeded!"
else
    echo "ERROR: Notarization failed! Check the output logs above."
    exit 1
fi

# 7. Staple the Ticket to the App
echo "Stapling notarization ticket directly to .app..."
xcrun stapler staple dist/Move-It.app
xcrun stapler validate dist/Move-It.app

# 8. Create DMG
echo "Packaging App into a DMG..."
mkdir -p dist/dmg
rm -rf dist/dmg/*
ditto dist/Move-It.app dist/dmg/Move-It.app
ln -s /Applications dist/dmg/Applications
hdiutil create -volname "Move-It" -srcfolder dist/dmg -ov -format UDZO dist/Move-It-macOS.dmg

# Sign DMG
echo "Signing DMG file..."
codesign --force --sign "$CERT_IDENTITY" dist/Move-It-macOS.dmg

echo "========================================="
echo "SUCCESS! Your signed and notarized DMG is ready:"
echo "Location: dist/Move-It-macOS.dmg"
echo "========================================="

# Clean up sensitive files
rm -f entitlements.plist dist/Move-It-notarize.zip
