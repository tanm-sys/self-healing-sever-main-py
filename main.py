import os
import time
import json
import logging
import psutil
import smtplib
from email.mime.text import MIMEText
from prometheus_client import start_http_server, Gauge
from threading import Thread
from typing import List, Dict, Any, Union
import subprocess
import platform
import statistics

# Global constants for file paths
CONFIG_FILE_PATH = os.getenv('CONFIG_FILE_PATH', 'config.json')
LOG_FILE_PATH = os.getenv('LOG_FILE_PATH', 'server_monitor.log')

# Define the expected configuration keys and their types
REQUIRED_CONFIG_KEYS = {
    'cpu_threshold': (int, float),
    'memory_threshold': (int, float),
    'disk_threshold': (int, float),
    'log_level': str,
    'alert_email': str
}

def load_config() -> Dict[str, Any]:
    """Load and validate configuration from the JSON file."""
    if os.stat(CONFIG_FILE_PATH).st_size == 0:
        logging.error(f"Configuration file is empty: {CONFIG_FILE_PATH}")
        raise ValueError("Configuration file is empty.")
    
    try:
        with open(CONFIG_FILE_PATH) as config_file:
            config = json.load(config_file)
            validate_config(config)
            return config
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

def validate_config(config: Dict[str, Any]) -> None:
    """Validate the configuration dictionary."""
    for key, expected_type in REQUIRED_CONFIG_KEYS.items():
        if key not in config:
            logging.error(f"Missing required configuration key: {key}")
            raise ValueError(f"Missing required configuration key: {key}")
        if not isinstance(config[key], expected_type):
            logging.error(f"Invalid type for key {key}: Expected {expected_type}, got {type(config[key])}")
            raise ValueError(f"Invalid type for key {key}: Expected {expected_type}, got {type(config[key])}")

def setup_logging(level: str) -> None:
    """Setup logging configuration to CSV format."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    logger = logging.getLogger()
    logger.setLevel(log_level)
    
    console_handler = logging.StreamHandler()
    file_handler = logging.FileHandler(LOG_FILE_PATH)
    
    formatter = logging.Formatter('%(asctime)s,%(levelname)s,%(message)s')
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)
    
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

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
    """Detect anomalies using a statistical approach."""
    if len(historical_data) < 5:
        logging.warning("Insufficient data for reliable anomaly detection.")
        return False
    
    mean = statistics.mean(historical_data)
    stdev = statistics.stdev(historical_data) if len(historical_data) > 1 else 0
    is_anomaly = abs(current_value - mean) > (threshold * stdev)
    
    logging.debug(f"Anomaly detection - Current Value: {current_value}, Mean: {mean}, "
                  f"Standard Deviation: {stdev}, Is Anomaly: {is_anomaly}")
    return is_anomaly

def send_alert(message: str) -> None:
    """Send an alert email."""
    config = load_config()
    sender_email = "sender@example.com"
    receiver_email = config['alert_email']
    
    msg = MIMEText(message)
    msg['Subject'] = 'Server Alert'
    msg['From'] = sender_email
    msg['To'] = receiver_email

    try:
        with smtplib.SMTP('localhost') as server:
            server.send_message(msg)
            logging.info('Alert sent successfully.')
    except Exception as e:
        logging.error(f"Failed to send alert: {e}")

def restart_service(service_name: str) -> None:
    """Restart the server service with enhanced error handling."""
    try:
        subprocess.run(['systemctl', 'restart', service_name], check=True)
        logging.info(f'Service {service_name} restarted successfully.')
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to restart service {service_name}: {e}")
        send_alert(f"Failed to restart service {service_name}: {e}")
    except FileNotFoundError:
        logging.error("Systemctl not found on the system.")
        send_alert("Systemctl not found on the system.")
    except Exception as e:
        logging.error(f"Unexpected error during service restart: {e}")
        send_alert(f"Unexpected error during service restart: {e}")

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
    """Check health results and adapt thresholds using exponential backoff."""
    thresholds = load_config()
    
    cpu_anomaly = detect_anomalies(results['cpu_usage'], historical_data['cpu_usage'], thresholds['cpu_threshold'])
    memory_anomaly = detect_anomalies(results['memory_usage'], historical_data['memory_usage'], thresholds['memory_threshold'])
    disk_anomaly = detect_anomalies(results['disk_usage'], historical_data['disk_usage'], thresholds['disk_threshold'])
    
    if cpu_anomaly or memory_anomaly or disk_anomaly:
        # Use exponential backoff for threshold adjustment
        new_thresholds = {
            'cpu_threshold': thresholds['cpu_threshold'] * 1.5,
            'memory_threshold': thresholds['memory_threshold'] * 1.5,
            'disk_threshold': thresholds['disk_threshold'] * 1.5
        }
        adjust_thresholds(new_thresholds)
        logging.info('Thresholds adjusted due to detected anomalies using exponential backoff.')

def adjust_thresholds(new_thresholds: Dict[str, Union[int, float]]) -> None:
    """Adjust health check thresholds in the configuration."""
    if os.stat(CONFIG_FILE_PATH).st_size == 0:
        logging.error(f"Configuration file is empty: {CONFIG_FILE_PATH}")
        raise ValueError("Configuration file is empty.")
    
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
