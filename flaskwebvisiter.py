# app.py
from flask import Flask, request, jsonify, render_template_string
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time
import threading
import os
import signal
import sys
import requests
import logging

app = Flask(__name__)

# ============ CONFIGURATION ============
HEADLESS = True  # Set to False to see the browser
STAY_TRUE = True  # Keep browser open indefinitely (persistent session)
SELF_PING_INTERVAL = 300  # Ping every 5 minutes (in seconds)
SELF_PING_URL = "http://127.0.0.1:5600/api/health"  # Internal health check
# =======================================

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global driver instance - stays alive
driver = None
visited_urls = []
should_exit = False
ping_thread_running = False

def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully"""
    global should_exit, driver, ping_thread_running
    print("\n\nCtrl+C detected. Cleaning up...")
    should_exit = True
    ping_thread_running = False
    if driver:
        try:
            driver.quit()
            print("Browser closed.")
        except:
            pass
    sys.exit(0)

# Register signal handler
signal.signal(signal.SIGINT, signal_handler)

def self_ping():
    """Background thread to ping the server to keep it alive"""
    global ping_thread_running, should_exit
    ping_thread_running = True
    logger.info("🔄 Self-ping thread started (interval: %s seconds)", SELF_PING_INTERVAL)
    
    while not should_exit and ping_thread_running:
        try:
            time.sleep(SELF_PING_INTERVAL)
            if should_exit or not ping_thread_running:
                break
            
            # Ping the health endpoint
            response = requests.get(SELF_PING_URL, timeout=10)
            if response.status_code == 200:
                data = response.json()
                logger.info("💓 Self-ping successful - Status: %s, Browser: %s, Visited: %s", 
                           data.get('status'), data.get('browser_running'), data.get('visited_urls'))
            else:
                logger.warning("⚠️ Self-ping returned status: %s", response.status_code)
                
        except requests.exceptions.RequestException as e:
            logger.warning("⚠️ Self-ping failed: %s", str(e))
        except Exception as e:
            logger.error("❌ Self-ping error: %s", str(e))
    
    logger.info("🛑 Self-ping thread stopped")

# HTML template for the web interface
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Selenium Web Scraper - Persistent Session</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 900px; margin: 50px auto; padding: 20px; }
        input, button { padding: 10px; margin: 5px; }
        input { width: 60%; }
        button { background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }
        button:hover { background: #0056b3; }
        .result { margin-top: 20px; padding: 20px; background: #f5f5f5; border-radius: 4px; white-space: pre-wrap; }
        .error { color: red; }
        .success { color: green; }
        .info { color: blue; }
        .url-list { margin-top: 20px; padding: 15px; background: #e8f4fd; border-radius: 4px; }
        .url-item { padding: 5px 0; border-bottom: 1px solid #ddd; }
        .badge { display: inline-block; padding: 2px 8px; background: #28a745; color: white; border-radius: 12px; font-size: 12px; }
        .actions { margin: 20px 0; }
        .danger-btn { background: #dc3545; }
        .danger-btn:hover { background: #c82333; }
        .status-bar { background: #e9ecef; padding: 10px; border-radius: 4px; margin-bottom: 15px; display: flex; justify-content: space-between; align-items: center; }
        .status-dot { display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 8px; }
        .status-dot.green { background: #28a745; }
        .status-dot.yellow { background: #ffc107; }
        .status-dot.red { background: #dc3545; }
        .ping-info { font-size: 12px; color: #6c757d; }
    </style>
</head>
<body>
    <h1>🌐 Persistent Selenium Browser</h1>
    
    <div class="status-bar">
        <div>
            <span class="status-dot green" id="statusDot"></span>
            <span id="statusText">Browser ready</span>
        </div>
        <div class="ping-info">
            🔄 Self-ping: <span id="pingStatus">Active</span> (every {{ ping_interval }}s)
        </div>
    </div>
    
    <div class="actions">
        <form id="scrapeForm" style="display: inline;">
            <input type="text" id="url" placeholder="Enter URL (e.g., https://example.com)" value="https://example.com">
            <button type="submit">Navigate</button>
        </form>
        <button onclick="getCurrentUrl()" style="background: #17a2b8;">Get Current URL</button>
        <button onclick="refreshPage()" style="background: #ffc107; color: #000;">Refresh</button>
        <button onclick="goBack()" style="background: #6c757d;">Go Back</button>
    </div>
    
    <div id="result" class="result">🟢 Browser is ready. Enter a URL and click Navigate...</div>
    
    <div id="urlHistory" class="url-list">
        <strong>📋 Navigation History:</strong>
        <div id="historyContent">No URLs visited yet.</div>
    </div>

    <script>
        // Update status periodically
        async function updateStatus() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                const dot = document.getElementById('statusDot');
                const text = document.getElementById('statusText');
                
                if (data.status === 'running') {
                    dot.className = 'status-dot green';
                    text.textContent = 'Browser running - ' + data.current_url;
                } else if (data.status === 'not_initialized') {
                    dot.className = 'status-dot yellow';
                    text.textContent = 'Browser not initialized';
                } else {
                    dot.className = 'status-dot red';
                    text.textContent = 'Error';
                }
            } catch (error) {
                console.error('Status update failed:', error);
            }
        }
        
        async function fetchScrape(url) {
            const resultDiv = document.getElementById('result');
            resultDiv.innerHTML = '⏳ Navigating to ' + url + '...';
            
            try {
                const response = await fetch('/navigate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: url })
                });
                const data = await response.json();
                
                if (data.error) {
                    resultDiv.innerHTML = `<div class="error">❌ Error: ${data.error}</div>`;
                } else {
                    resultDiv.innerHTML = `
                        <div class="success">✅ Navigation successful!</div>
                        <strong>Current URL:</strong> ${data.current_url}<br>
                        <strong>Title:</strong> ${data.title}<br>
                        <strong>Visited URLs:</strong> ${data.visited_count}<br>
                        <hr>
                        <strong>HTML Preview:</strong><br>
                        ${data.html_preview}
                    `;
                    updateHistory(data.history);
                    updateStatus();
                }
            } catch (error) {
                resultDiv.innerHTML = `<div class="error">❌ Request failed: ${error.message}</div>`;
            }
        }
        
        function updateHistory(history) {
            const container = document.getElementById('historyContent');
            if (!history || history.length === 0) {
                container.innerHTML = 'No URLs visited yet.';
                return;
            }
            container.innerHTML = history.map((url, index) => 
                `<div class="url-item">${index + 1}. ${url}</div>`
            ).join('');
        }
        
        document.getElementById('scrapeForm').onsubmit = async (e) => {
            e.preventDefault();
            const url = document.getElementById('url').value;
            await fetchScrape(url);
        };
        
        async function getCurrentUrl() {
            try {
                const response = await fetch('/current_url');
                const data = await response.json();
                document.getElementById('result').innerHTML = `
                    <div class="info">📍 Current URL: ${data.current_url}</div>
                    <strong>Title:</strong> ${data.title}
                `;
            } catch (error) {
                document.getElementById('result').innerHTML = `<div class="error">❌ Error: ${error.message}</div>`;
            }
        }
        
        async function refreshPage() {
            try {
                const response = await fetch('/refresh', { method: 'POST' });
                const data = await response.json();
                document.getElementById('result').innerHTML = `
                    <div class="success">✅ Page refreshed!</div>
                    <strong>Current URL:</strong> ${data.current_url}<br>
                    <strong>Title:</strong> ${data.title}
                `;
            } catch (error) {
                document.getElementById('result').innerHTML = `<div class="error">❌ Error: ${error.message}</div>`;
            }
        }
        
        async function goBack() {
            try {
                const response = await fetch('/back', { method: 'POST' });
                const data = await response.json();
                document.getElementById('result').innerHTML = `
                    <div class="info">⬅️ Navigated back</div>
                    <strong>Current URL:</strong> ${data.current_url}<br>
                    <strong>Title:</strong> ${data.title}
                `;
                updateHistory(data.history);
            } catch (error) {
                document.getElementById('result').innerHTML = `<div class="error">❌ Error: ${error.message}</div>`;
            }
        }
        
        // Load initial history
        async function loadHistory() {
            try {
                const response = await fetch('/history');
                const data = await response.json();
                updateHistory(data.history);
            } catch (error) {
                console.error('Failed to load history:', error);
            }
        }
        
        // Initial load and periodic updates
        loadHistory();
        updateStatus();
        setInterval(updateStatus, 30000); // Update status every 30 seconds
    </script>
</body>
</html>
'''

def init_driver():
    """Initialize or return existing driver instance"""
    global driver
    if driver is None:
        logger.info("🔄 Initializing browser (persistent mode)...")
        chrome_options = Options()
        
        # Headless configuration
        if HEADLESS:
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
        
        # Common options
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # Initialize driver
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        logger.info("✅ Browser initialized and ready!")
    return driver

def get_page_info():
    """Get current page information"""
    global driver
    if driver is None:
        return None
    
    try:
        title = driver.title
        current_url = driver.current_url
        page_source = driver.page_source
        content_length = len(page_source)
        html_preview = page_source[:500] + "..." if len(page_source) > 500 else page_source
        
        return {
            'title': title,
            'current_url': current_url,
            'content_length': content_length,
            'html_preview': html_preview
        }
    except Exception as e:
        return {'error': str(e)}

@app.route('/')
def index():
    """Home page with web interface"""
    return render_template_string(HTML_TEMPLATE, ping_interval=SELF_PING_INTERVAL)

@app.route('/navigate', methods=['POST'])
def navigate():
    """Navigate to a URL (persistent session)"""
    global driver, visited_urls
    
    try:
        data = request.get_json()
        url = data.get('url', '').strip()
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
        # Add https:// if no protocol specified
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        # Initialize driver if not already done
        driver = init_driver()
        
        # Navigate to URL
        logger.info("🔄 Navigating to: %s", url)
        driver.get(url)
        
        # Track visited URLs
        if url not in visited_urls:
            visited_urls.append(url)
        
        # Get page info
        info = get_page_info()
        if info and 'error' not in info:
            return jsonify({
                'success': True,
                'current_url': info['current_url'],
                'title': info['title'],
                'content_length': info['content_length'],
                'html_preview': info['html_preview'],
                'visited_count': len(visited_urls),
                'history': visited_urls
            })
        else:
            return jsonify({'error': info.get('error', 'Failed to get page info')}), 500
            
    except Exception as e:
        logger.error("Navigation error: %s", str(e))
        return jsonify({'error': str(e)}), 500

@app.route('/current_url', methods=['GET'])
def current_url():
    """Get current URL and title"""
    global driver
    
    if driver is None:
        return jsonify({'error': 'Browser not initialized'}), 400
    
    info = get_page_info()
    if info and 'error' not in info:
        return jsonify({
            'current_url': info['current_url'],
            'title': info['title']
        })
    else:
        return jsonify({'error': info.get('error', 'Failed to get page info')}), 500

@app.route('/refresh', methods=['POST'])
def refresh():
    """Refresh current page"""
    global driver
    
    if driver is None:
        return jsonify({'error': 'Browser not initialized'}), 400
    
    try:
        driver.refresh()
        time.sleep(1)
        info = get_page_info()
        if info and 'error' not in info:
            return jsonify({
                'current_url': info['current_url'],
                'title': info['title']
            })
        else:
            return jsonify({'error': info.get('error', 'Failed to get page info')}), 500
    except Exception as e:
        logger.error("Refresh error: %s", str(e))
        return jsonify({'error': str(e)}), 500

@app.route('/back', methods=['POST'])
def go_back():
    """Go back in history"""
    global driver
    
    if driver is None:
        return jsonify({'error': 'Browser not initialized'}), 400
    
    try:
        driver.back()
        time.sleep(1)
        info = get_page_info()
        if info and 'error' not in info:
            return jsonify({
                'current_url': info['current_url'],
                'title': info['title'],
                'history': visited_urls
            })
        else:
            return jsonify({'error': info.get('error', 'Failed to get page info')}), 500
    except Exception as e:
        logger.error("Back navigation error: %s", str(e))
        return jsonify({'error': str(e)}), 500

@app.route('/history', methods=['GET'])
def get_history():
    """Get navigation history"""
    return jsonify({'history': visited_urls})

@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint"""
    global driver
    return jsonify({
        'status': 'healthy',
        'headless': HEADLESS,
        'persistent': STAY_TRUE,
        'browser_running': driver is not None,
        'visited_urls': len(visited_urls),
        'ping_interval': SELF_PING_INTERVAL,
        'ping_active': ping_thread_running
    })

@app.route('/api/status', methods=['GET'])
def status():
    """Get browser status"""
    global driver
    if driver is None:
        return jsonify({'status': 'not_initialized'})
    
    try:
        return jsonify({
            'status': 'running',
            'current_url': driver.current_url,
            'title': driver.title,
            'history': visited_urls
        })
    except:
        return jsonify({'status': 'error'})

@app.route('/api/ping', methods=['POST'])
def manual_ping():
    """Manual ping endpoint to trigger immediate keepalive"""
    logger.info("📡 Manual ping received")
    return jsonify({
        'status': 'pong',
        'timestamp': time.time(),
        'browser_running': driver is not None
    })

if __name__ == '__main__':
    print("="*60)
    print("🌐 Persistent Selenium Flask Server")
    print(f"Headless mode: {HEADLESS}")
    print(f"Persistent session: {STAY_TRUE} (browser stays open)")
    print(f"Self-ping interval: {SELF_PING_INTERVAL} seconds")
    print(f"Self-ping URL: {SELF_PING_URL}")
    print("Server running at: http://127.0.0.1:5600")
    print("Press Ctrl+C to stop and close browser")
    print("="*60)
    
    try:
        # Initialize browser on startup
        logger.info("🔄 Starting browser...")
        driver = init_driver()
        # Load a default page
        driver.get("https://example.com")
        logger.info("✅ Browser ready!")
        visited_urls.append("https://example.com")
        
        # Start self-ping thread
        ping_thread = threading.Thread(target=self_ping, daemon=True)
        ping_thread.start()
        logger.info("✅ Self-ping thread started")
        
        # Run Flask app
        app.run(debug=False, host='0.0.0.0', port=5600, use_reloader=False)
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        ping_thread_running = False
        if driver:
            try:
                driver.quit()
                print("Browser closed.")
            except:
                pass
    except Exception as e:
        logger.error("Error: %s", str(e))
    finally:
        ping_thread_running = False
        if driver:
            try:
                driver.quit()
                print("Browser closed.")
            except:
                pass