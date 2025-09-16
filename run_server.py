#!/usr/bin/env python3

import os
import sys
import subprocess
import platform

def check_python_version():
    if sys.version_info < (3, 8):
        print("Error: Python 3.8 or higher is required")
        print(f"Current version: {sys.version}")
        sys.exit(1)

def install_requirements():
    print("Installing required packages...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("✓ Requirements installed successfully")
    except subprocess.CalledProcessError as e:
        print(f"Error installing requirements: {e}")
        sys.exit(1)

def check_chromedriver():
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        
        options = Options()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        
        driver = webdriver.Chrome(options=options)
        driver.quit()
        print("✓ ChromeDriver is working")
        return True
    except Exception as e:
        print(f"⚠ ChromeDriver not found or not working: {e}")
        print("Please install ChromeDriver:")
        print("  macOS: brew install chromedriver")
        print("  Ubuntu: sudo apt-get install chromium-chromedriver")
        print("  Or download from: https://chromedriver.chromium.org/")
        return False

def create_env_file():
    env_file = ".env"
    if not os.path.exists(env_file):
        with open(env_file, "w") as f:
            f.write("# GW Auto-Registration Environment Variables\n")
            f.write("SECRET_KEY=your-secret-key-here\n")
            f.write("FLASK_ENV=development\n")
        print(f"✓ Created {env_file} file")
        print("⚠ Please update the SECRET_KEY in .env file for production use")

def main():
    print("🚀 Starting GW Auto-Registration Server Setup...")
    print("=" * 50)
    
    check_python_version()
    print("✓ Python version check passed")
    
    install_requirements()
    
    check_chromedriver()
    
    create_env_file()
    
    print("\n" + "=" * 50)
    print("🎉 Setup complete! Starting server...")
    print("=" * 50)
    
    try:
        from server_app import app
        app.run(debug=True, host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        print("\n👋 Server stopped by user")
    except Exception as e:
        print(f"❌ Error starting server: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
