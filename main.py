import os
import time
import json
import logging
import psutil
import pandas as pd
from email.mime.text import MIMEText
from prometheus_client import start_http_server, Gauge
from threading import Thread
from typing import List, Dict, Any
import subprocess
import platform
import base64
import google.auth
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# Configuration
CONFIG_FILE_PATH = os.getenv('CONFIG_FILE_PATH', 'config.json')
LOG_FILE_PATH = os.getenv('LOG_FILE_PATH', 'server_monitor.csv')

def load_config() -> Dict[str, Any]:
    """Load configuration from the JSON file."""
    try:
        with open(CONFIG_FILE_PATH) as config_file:
            return json.load(config_file)
    except FileNotFoundError:
        logging.error(f"Configuration file not found: {CONFIG_FILE_PATH}")
        raise
    except PermissionError:
        logging.error(f"Permission denied: {CONFIG_FILE_PATH}")
        raise
    except json.JSONDecodeError:
        logging.error("Error decoding JSON from the configuration file.")
        raise
    except Exception as e:
        logging.error(f"Unexpected error while loading configuration: {e}")
        raise

def setup_logging(level: str) -> None:
    """Setup logging configuration to CSV format."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    logger = logging.getLogger()
    logger.setLevel(log_level)

    # Create handlers
    console_handler = logging.StreamHandler()
    file_handler = logging.FileHandler(LOG_FILE_PATH)

    # Create formatters and add them to handlers
    formatter = logging.Formatter('%(asctime)s,%(levelname)s,%(message)s')
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    # Add handlers to the logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

def authenticate_gmail_api() -> Credentials:
    """Authenticate and return Gmail API credentials."""
    SCOPES = ['https://www.googleapis.com/auth/gmail.send']
    creds = None

    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise Exception("Token not available. Please ensure proper OAuth setup.")
    
    return creds

def send_email(message: str) -> None:
    """Send an alert email using Gmail API."""
    config = load_config()
    creds = authenticate_gmail_api()
    try:
        service = build('gmail', 'v1', credentials=creds)
        email_msg = MIMEText(message)
        email_msg['to'] = config['alert_email']
        email_msg['from'] = 'rohanbelsare113@gmail.com'
        email_msg['subject'] = 'Server Alert'

        raw_msg = base64.urlsafe_b64encode(email_msg.as_bytes()).decode()
        body = {'raw': raw_msg}

        service.users().messages().send(userId='me', body=body).execute()
        logging.info('Alert sent successfully.')
    except HttpError as error:
        logging.error(f"An error occurred: {error}")

def perform_health_checks() -> Dict[str, Any]:
    """Perform health checks on CPU, memory, and disk usage."""
    results = {
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'cpu_usage': psutil.cpu_percent(),
        'memory_usage': psutil.virtual_memory().percent,
        'disk_usage': psutil.disk_usage('/').percent,
        'needs_restart': False
    }
    
    config = load_config()
    if results['cpu_usage'] > config['cpu_threshold']:
        results['needs_restart'] = True
    
    logging.debug(f"Health check results: {results}")
    return results

def detect_anomalies(current_value: float, historical_data: List[float], threshold: float) -> bool:
    """Detect anomalies based on historical data."""
    if len(historical_data) < 5:
        return False
    
    avg = sum(historical_data) / len(historical_data)
    deviation = abs(current_value - avg)
    is_anomaly = deviation > threshold
    logging.debug(f"Anomaly detection - Current Value: {current_value}, Avg: {avg}, Deviation: {deviation}, Is Anomaly: {is_anomaly}")
    return is_anomaly

def restart_service(service_name: str) -> None:
    """Restart the server service."""
    try:
        subprocess.run(['systemctl', 'restart', service_name], check=True)
        logging.info(f'Service {service_name} restarted successfully.')
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to restart service {service_name}: {e}")
        send_email(f"Failed to restart service {service_name}: {e}")

def start_prometheus_server(port: int) -> None:
    """Start the Prometheus metrics server."""
    def prometheus_thread():
        cpu_usage_gauge = Gauge('cpu_usage', 'CPU Usage in percent')
        memory_usage_gauge = Gauge('memory_usage', 'Memory Usage in percent')
        disk_usage_gauge = Gauge('disk_usage', 'Disk Usage in percent')

        start_http_server(port)
        logging.info(f"Prometheus metrics server started on port {port}")
        while True:
            cpu_usage_gauge.set(psutil.cpu_percent())
            memory_usage_gauge.set(psutil.virtual_memory().percent)
            disk_usage_gauge.set(psutil.disk_usage('/').percent)
            time.sleep(10)

    thread = Thread(target=prometheus_thread)
    thread.daemon = True
    thread.start()

def check_and_adapt_thresholds(results: Dict[str, Any], historical_data: Dict[str, List[float]]) -> None:
    """Check health results and adapt thresholds if necessary."""
    thresholds = load_config()
    
    cpu_anomaly = detect_anomalies(results['cpu_usage'], historical_data['cpu_usage'], thresholds['cpu_threshold'])
    memory_anomaly = detect_anomalies(results['memory_usage'], historical_data['memory_usage'], thresholds['memory_threshold'])
    disk_anomaly = detect_anomalies(results['disk_usage'], historical_data['disk_usage'], thresholds['disk_threshold'])
    
    if cpu_anomaly or memory_anomaly or disk_anomaly:
        new_thresholds = {
            'cpu_threshold': thresholds['cpu_threshold'] + 5,
            'memory_threshold': thresholds['memory_threshold'] + 5,
            'disk_threshold': thresholds['disk_threshold'] + 5
        }
        adjust_thresholds(new_thresholds)
        logging.info('Thresholds adjusted due to detected anomalies.')

def adjust_thresholds(new_thresholds: Dict[str, int]) -> None:
    """Adjust health check thresholds in the configuration."""
    try:
        with open(CONFIG_FILE_PATH, 'r+') as config_file:
            config = json.load(config_file)
            config.update(new_thresholds)
            config_file.seek(0)
            json.dump(config, config_file, indent=4)
            config_file.truncate()
    except PermissionError:
        logging.error(f"Permission denied: {CONFIG_FILE_PATH}")
    except json.JSONDecodeError:
        logging.error("Error decoding JSON from the configuration file.")
    except Exception as e:
        logging.error(f"Unexpected error while adjusting thresholds: {e}")

def clear_terminal() -> None:
    """Clear the terminal screen."""
    os.system('cls' if platform.system() == 'Windows' else 'clear')


if __name__ == '__main__':
    setup_logging('DEBUG')  # Set the logging level as required
    start_prometheus_server(8000)  # Start Prometheus server on port 8000

    # Example configuration
    historical_data = {
        'cpu_usage': [],
        'memory_usage': [],
        'disk_usage': []
    }

    while True:
        clear_terminal()
        health_results = perform_health_checks()
        check_and_adapt_thresholds(health_results, historical_data)

        if health_results['needs_restart']:
            restart_service('your_service_name')

        # Append to historical data for anomaly detection
        for key in historical_data.keys():
            historical_data[key].append(health_results.get(key, 0))

        # Print health check results
        print(f"Timestamp: {health_results['timestamp']}")
        print(f"CPU Usage: {health_results['cpu_usage']}%")
        print(f"Memory Usage: {health_results['memory_usage']}%")
        print(f"Disk Usage: {health_results['disk_usage']}%")
        print(f"Needs Restart: {health_results['needs_restart']}")

        time.sleep(5)


