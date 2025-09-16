
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import threading
import time
import datetime
import json
import os
import secrets
from functools import wraps
import schedule
import atexit

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///gw_registration.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    gw_username = db.Column(db.String(80), nullable=True)
    gw_password = db.Column(db.String(120), nullable=True)
    two_fa_secret = db.Column(db.String(120), nullable=True)
    session_cookies = db.Column(db.Text, nullable=True)
    cookies_expiry = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class RegistrationJob(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    crns = db.Column(db.Text, nullable=False)
    scheduled_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    error_message = db.Column(db.Text, nullable=True)

class RegistrationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey('registration_job.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    level = db.Column(db.String(20), default='info')

class SavedSchedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    crns = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    last_used = db.Column(db.DateTime, nullable=True)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Invalid credentials')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        
        if User.query.filter_by(username=username).first():
            return render_template('register.html', error='Username already exists')
        
        if User.query.filter_by(email=email).first():
            return render_template('register.html', error='Email already registered')
        
        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password)
        )
        
        db.session.add(user)
        db.session.commit()
        
        session['user_id'] = user.id
        return redirect(url_for('setup_credentials'))
    
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    user = User.query.get(session['user_id'])
    jobs = RegistrationJob.query.filter_by(user_id=user.id).order_by(RegistrationJob.created_at.desc()).limit(10).all()
    return render_template('dashboard.html', user=user, jobs=jobs)

@app.route('/setup-credentials', methods=['GET', 'POST'])
@login_required
def setup_credentials():
    user = User.query.get(session['user_id'])
    
    if request.method == 'POST':
        gw_username = request.form['gw_username']
        gw_password = request.form['gw_password']
        two_fa_secret = request.form.get('two_fa_secret', '')
        
        user.gw_username = gw_username
        user.gw_password = generate_password_hash(gw_password)
        user.two_fa_secret = generate_password_hash(two_fa_secret) if two_fa_secret else None
        
        db.session.commit()
        return redirect(url_for('dashboard'))
    
    return render_template('setup_credentials.html', user=user)

@app.route('/create-job', methods=['POST'])
@login_required
def create_job():
    data = request.get_json()
    crns = data.get('crns', [])
    schedule_id = data.get('schedule_id')
    scheduled_time_str = data.get('scheduled_time')
    
    try:
        scheduled_time = datetime.datetime.fromisoformat(scheduled_time_str.replace('Z', '+00:00'))
    except ValueError:
        return jsonify({'error': 'Invalid date format'}), 400
    
    if schedule_id:
        saved_schedule = SavedSchedule.query.get(schedule_id)
        if not saved_schedule or saved_schedule.user_id != session['user_id']:
            return jsonify({'error': 'Invalid schedule selected'}), 400
        crns = json.loads(saved_schedule.crns)
        saved_schedule.last_used = datetime.datetime.utcnow()
        db.session.commit()
    elif not crns:
        return jsonify({'error': 'No CRNs provided'}), 400
    
    job = RegistrationJob(
        user_id=session['user_id'],
        crns=json.dumps(crns),
        scheduled_time=scheduled_time
    )
    
    db.session.add(job)
    db.session.commit()
    
    schedule_job(job.id)
    
    return jsonify({'success': True, 'job_id': job.id})

@app.route('/job-status/<int:job_id>')
@login_required
def job_status(job_id):
    job = RegistrationJob.query.get(job_id)
    if not job or job.user_id != session['user_id']:
        return jsonify({'error': 'Job not found'}), 404
    
    logs = RegistrationLog.query.filter_by(job_id=job_id).order_by(RegistrationLog.timestamp.desc()).all()
    
    return jsonify({
        'status': job.status,
        'error_message': job.error_message,
        'logs': [{'message': log.message, 'timestamp': log.timestamp.isoformat(), 'level': log.level} for log in logs]
    })

@app.route('/test-login', methods=['POST'])
@login_required
def test_login():
    user = User.query.get(session['user_id'])
    if not user or not user.gw_username or not user.gw_password:
        return jsonify({'error': 'GW credentials not configured'}), 400
    
    try:
        driver = create_driver()
        if not driver:
            return jsonify({'error': 'Failed to create browser driver'}), 500
        
        success, cookies = perform_login_and_save_cookies(driver, user)
        
        if success:
            user.session_cookies = json.dumps(cookies)
            user.cookies_expiry = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
            db.session.commit()
            
            driver.quit()
            return jsonify({'success': True, 'message': 'Login successful! Cookies saved for 24 hours.'})
        else:
            driver.quit()
            return jsonify({'error': 'Login failed. Please check your credentials.'}), 400
            
    except Exception as e:
        return jsonify({'error': f'Login test failed: {str(e)}'}), 500

@app.route('/schedules')
@login_required
def schedules():
    user = User.query.get(session['user_id'])
    saved_schedules = SavedSchedule.query.filter_by(user_id=user.id).order_by(SavedSchedule.created_at.desc()).all()
    return render_template('schedules.html', schedules=saved_schedules)

@app.route('/save-schedule', methods=['POST'])
@login_required
def save_schedule():
    data = request.get_json()
    name = data.get('name', '').strip()
    crns = data.get('crns', [])
    description = data.get('description', '').strip()
    
    if not name:
        return jsonify({'error': 'Schedule name is required'}), 400
    
    if not crns:
        return jsonify({'error': 'At least one CRN is required'}), 400
    
    existing = SavedSchedule.query.filter_by(user_id=session['user_id'], name=name).first()
    if existing:
        return jsonify({'error': 'A schedule with this name already exists'}), 400
    
    schedule = SavedSchedule(
        user_id=session['user_id'],
        name=name,
        crns=json.dumps(crns),
        description=description
    )
    
    db.session.add(schedule)
    db.session.commit()
    
    return jsonify({'success': True, 'schedule_id': schedule.id})

@app.route('/delete-schedule/<int:schedule_id>', methods=['DELETE'])
@login_required
def delete_schedule(schedule_id):
    schedule = SavedSchedule.query.get(schedule_id)
    if not schedule or schedule.user_id != session['user_id']:
        return jsonify({'error': 'Schedule not found'}), 404
    
    db.session.delete(schedule)
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/get-schedules')
@login_required
def get_schedules():
    schedules = SavedSchedule.query.filter_by(user_id=session['user_id']).order_by(SavedSchedule.created_at.desc()).all()
    return jsonify({
        'schedules': [{
            'id': s.id,
            'name': s.name,
            'crns': json.loads(s.crns),
            'description': s.description,
            'created_at': s.created_at.isoformat(),
            'last_used': s.last_used.isoformat() if s.last_used else None
        } for s in schedules]
    })

@app.route('/quick-register', methods=['GET', 'POST'])
def quick_register():
    if request.method == 'POST':
        data = request.get_json()
        gw_username = data.get('gw_username', '').strip()
        gw_password = data.get('gw_password', '').strip()
        crns = data.get('crns', [])
        scheduled_time_str = data.get('scheduled_time')
        
        if not gw_username or not gw_password:
            return jsonify({'error': 'GW username and password are required'}), 400
        
        if not crns:
            return jsonify({'error': 'At least one CRN is required'}), 400
        
        try:
            scheduled_time = datetime.datetime.fromisoformat(scheduled_time_str.replace('Z', '+00:00'))
        except ValueError:
            return jsonify({'error': 'Invalid date format'}), 400
        
        job = RegistrationJob(
            user_id=None,
            crns=json.dumps(crns),
            scheduled_time=scheduled_time
        )
        
        db.session.add(job)
        db.session.commit()
        
        schedule_job(job.id)
        
        return jsonify({'success': True, 'job_id': job.id, 'message': 'Registration scheduled successfully!'})
    
    return render_template('quick_register.html')

def create_driver(headless=True):
    chrome_options = Options()
    if headless:
        chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return driver
    except Exception as e:
        print(f"Error creating Chrome driver: {e}")
        return None

def log_job_message(job_id, message, level='info'):
    log = RegistrationLog(
        job_id=job_id,
        message=message,
        level=level
    )
    db.session.add(log)
    db.session.commit()

def perform_login_and_save_cookies(driver, user):
    try:
        driver.get("https://gweb-site.gwu.edu/")
        time.sleep(2)
        
        wait = WebDriverWait(driver, 30)
        
        username_field = wait.until(EC.presence_of_element_located((By.NAME, "username")))
        username_field.clear()
        username_field.send_keys(user.gw_username)
        
        password_field = driver.find_element(By.NAME, "password")
        password_field.clear()
        password_field.send_keys(user.gw_password)
        
        login_button = driver.find_element(By.XPATH, "//input[@type='submit']")
        login_button.click()
        
        time.sleep(3)
        
        if "2fa" in driver.current_url.lower() or "duo" in driver.current_url.lower():
            return False, None
        
        if "bssoweb" in driver.current_url.lower() or "gwu.edu" in driver.current_url.lower():
            cookies = driver.get_cookies()
            return True, cookies
        
        return False, None
        
    except Exception as e:
        print(f"Login error: {e}")
        return False, None

def load_cookies_to_driver(driver, cookies_json):
    try:
        cookies = json.loads(cookies_json)
        driver.get("https://gweb-site.gwu.edu/")
        time.sleep(1)
        
        for cookie in cookies:
            try:
                driver.add_cookie(cookie)
            except Exception as e:
                continue
        
        driver.refresh()
        time.sleep(2)
        return True
    except Exception as e:
        print(f"Cookie loading error: {e}")
        return False

def execute_registration_job(job_id):
    job = RegistrationJob.query.get(job_id)
    if not job:
        return
    
    job.status = 'running'
    db.session.commit()
    
    log_job_message(job_id, f"Starting FAST registration job for {len(json.loads(job.crns))} CRNs")
    
    max_attempts = 5
    attempt = 0
    
    while attempt < max_attempts:
        attempt += 1
        log_job_message(job_id, f"Registration attempt {attempt}/{max_attempts}")
        
        success = try_registration(job_id, job)
        
        if success:
            log_job_message(job_id, f"Registration successful on attempt {attempt}")
            job.status = 'completed'
            job.completed_at = datetime.datetime.utcnow()
            db.session.commit()
            return
        
        if attempt < max_attempts:
            log_job_message(job_id, f"Registration failed on attempt {attempt}, retrying in 1 minute...")
            time.sleep(60)
        else:
            log_job_message(job_id, f"Registration failed after {max_attempts} attempts")
            job.status = 'failed'
            job.error_message = f'Registration failed after {max_attempts} attempts - registration may not be open yet'
            db.session.commit()
            return

def try_registration(job_id, job):
    if job.user_id:
        user = User.query.get(job.user_id)
        if not user or not user.gw_username or not user.gw_password:
            return False
        gw_username = user.gw_username
        gw_password = user.gw_password
        session_cookies = user.session_cookies
        cookies_expiry = user.cookies_expiry
    else:
        return False
    
    driver = create_driver(headless=True)
    if not driver:
        return False
    
    try:
        cookies_loaded = False
        if session_cookies and cookies_expiry and cookies_expiry > datetime.datetime.utcnow():
            log_job_message(job_id, "Using saved cookies for instant login")
            cookies_loaded = load_cookies_to_driver(driver, session_cookies)
        
        if not cookies_loaded:
            log_job_message(job_id, "Performing fresh login")
            temp_user = type('User', (), {'gw_username': gw_username, 'gw_password': gw_password})()
            success, cookies = perform_login_and_save_cookies(driver, temp_user)
            if not success:
                return False
            
            if job.user_id:
                user.session_cookies = json.dumps(cookies)
                user.cookies_expiry = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
                db.session.commit()
        
        driver.get("https://bssoweb.gwu.edu:8002/StudentRegistrationSsb/ssb/registration/")
        log_job_message(job_id, "Navigated to registration page")
        
        wait = WebDriverWait(driver, 10)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        
        crns = json.loads(job.crns)
        log_job_message(job_id, f"Attempting to register for CRNs: {', '.join(crns)}")
        
        for i, crn in enumerate(crns, 1):
            try:
                crn_input = driver.find_element(By.ID, f"txt_crn{i}")
                crn_input.clear()
                crn_input.send_keys(crn)
                log_job_message(job_id, f"Entered CRN {crn} in field {i}")
                time.sleep(0.1)
            except:
                try:
                    crn_inputs = driver.find_elements(By.CSS_SELECTOR, "input[name*='crn'], input[id*='crn']")
                    for inp in crn_inputs:
                        if not inp.get_attribute("value"):
                            inp.clear()
                            inp.send_keys(crn)
                            log_job_message(job_id, f"Entered CRN {crn}")
                            break
                except Exception as e:
                    log_job_message(job_id, f"Error entering CRN {crn}: {str(e)}", "error")
        
        time.sleep(0.5)
        
        submit_button = driver.find_element(By.ID, "add_crn_button")
        submit_button.click()
        log_job_message(job_id, "Clicked Add Courses button")
        
        time.sleep(1)
        
        try:
            register_button = driver.find_element(By.ID, "register_button")
            register_button.click()
            log_job_message(job_id, "Clicked Register button")
        except:
            try:
                register_button = driver.find_element(By.XPATH, "//input[@value='Register']")
                register_button.click()
                log_job_message(job_id, "Clicked Register button (alternative)")
            except:
                log_job_message(job_id, "Could not find register button", "warning")
        
        time.sleep(2)
        
        page_text = driver.find_element(By.TAG_NAME, "body").text
        if "successfully" in page_text.lower() or "registered" in page_text.lower():
            log_job_message(job_id, "Registration appears successful!")
            return True
        else:
            log_job_message(job_id, f"Registration may have failed. Check results: {page_text[:300]}...")
            return False
        
    except Exception as e:
        log_job_message(job_id, f"Registration failed: {str(e)}", "error")
        return False
    
    finally:
        driver.quit()

def schedule_job(job_id):
    job = RegistrationJob.query.get(job_id)
    if not job:
        return
    
    def run_job():
        execute_registration_job(job_id)
    
    local_time = job.scheduled_time.replace(tzinfo=None)
    schedule.every().day.at(local_time.strftime("%H:%M")).do(run_job)
    
    log_job_message(job_id, f"Job scheduled for {local_time}")

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
scheduler_thread.start()

def cleanup():
    pass

atexit.register(cleanup)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    
    print("Starting GW Auto-Registration Server...")
    print("Access the application at: http://localhost:8080")
    app.run(debug=True, host='0.0.0.0', port=8080)
