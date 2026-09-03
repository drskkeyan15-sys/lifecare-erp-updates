"""
Life Care Pharmacy ERP - Combined Launcher
Starts both Desktop ERP and Web Analytics Dashboard automatically
"""

import subprocess
import time
import os
import sys
from pathlib import Path
import webbrowser
from threading import Thread

def start_desktop_erp(erp_path):
    """Start Desktop ERP application"""
    try:
        print(f"🏥 Starting Desktop ERP from: {erp_path}")
        if os.path.exists(erp_path):
            subprocess.Popen([sys.executable, erp_path])
            print("✓ Desktop ERP started")
            return True
        else:
            print(f"✗ Error: ERP not found at {erp_path}")
            return False
    except Exception as e:
        print(f"✗ Error starting ERP: {e}")
        return False

def start_web_dashboard(web_app_path):
    """Start Streamlit web dashboard in background"""
    try:
        print(f"📊 Starting Web Dashboard from: {web_app_path}")
        if os.path.exists(web_app_path):
            # --server.port pinned to 8502 explicitly - Streamlit defaults
            # to 8501, but open_web_dashboard_browser() below always opens
            # localhost:8502, so without this flag the two disagreed and
            # the auto-opened browser tab hit nothing.
            subprocess.Popen(
                [sys.executable, '-m', 'streamlit', 'run', web_app_path,
                 '--server.port', '8502', '--logger.level=error'],
                cwd=os.path.dirname(web_app_path),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            print("✓ Web Dashboard starting (10-15 seconds)")
            return True
        else:
            print(f"✗ Error: Web app not found at {web_app_path}")
            return False
    except Exception as e:
        print(f"✗ Error starting Web Dashboard: {e}")
        return False

def open_web_dashboard_browser():
    """Open web dashboard in browser after delay"""
    try:
        print("⏳ Waiting for web dashboard to be ready...")
        time.sleep(15)
        print("🌐 Opening web dashboard in browser...")
        webbrowser.open('http://localhost:8502')
        print("✓ Dashboard opened in browser")
    except Exception as e:
        print(f"✗ Could not open browser: {e}")

def main():
    """Main launcher function"""
    print("\n" + "="*60)
    print("  Life Care Pharmacy ERP - Auto-Launcher")
    print("="*60 + "\n")

    possible_erp_paths = [
        r'D:\05-08-2026\main.py',
        r'D:\05-08-2026\main.pyw',
    ]

    possible_web_paths = [
        r'D:\Pharmacy_Advanced\app.py',
    ]

    erp_path = None
    web_path = None

    for path in possible_erp_paths:
        if os.path.exists(path):
            erp_path = path
            break

    for path in possible_web_paths:
        if os.path.exists(path):
            web_path = path
            break

    if not erp_path:
        print("✗ ERROR: Desktop ERP not found!")
        input("Press Enter to exit...")
        return False

    if not web_path:
        print("⚠ WARNING: Web Dashboard not found!")

    print(f"✓ Found Desktop ERP: {erp_path}")
    if web_path:
        print(f"✓ Found Web Dashboard: {web_path}\n")

    erp_started = start_desktop_erp(erp_path)
    time.sleep(3)

    web_started = False
    if web_path:
        web_started = start_web_dashboard(web_path)
        browser_thread = Thread(target=open_web_dashboard_browser, daemon=True)
        browser_thread.start()

    print("\n" + "="*60)
    if erp_started:
        print("✅ Desktop ERP is running")
    if web_started:
        print("✅ Web Dashboard is starting")
    print("="*60 + "\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\nClosing applications...")
        sys.exit(0)

if __name__ == "__main__":
    main()
