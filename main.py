import os
import time
import json
import logging
import psutil
import smtplib
from email.mime.text import MIMEText
from prometheus_client import start_http_server, Gauge
from threading import Thread
from typing import List, Dict, Any
import subprocess
import platform

# Functions to dynamically get configuration and log file paths
def get_config_file_path() -> str:
    return os.getenv('CONFIG_FILE_PATH', 'config.json')

def get_log_file_path() -> str:
    return os.getenv('LOG_FILE_PATH', 'server_monitor.csv')

def load_config() -> Dict[str, Any]:
    """Load configuration from the JSON file."""
    try:
        with open(get_config_file_path()) as config_file:
            return json.load(config_file)
    except FileNotFoundError:
        logging.error(f"Configuration file not found: {get_config_file_path()}")
        raise
    except PermissionError:
        logging.error(f"Permission denied: {get_config_file_path()}")
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
    
    console_handler = logging.StreamHandler()
    file_handler = logging.FileHandler(get_log_file_path())
    
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
    """Detect anomalies based on historical data."""
    if len(historical_data) < 5:
        return False
    
    avg = sum(historical_data) / len(historical_data)
    deviation = abs(current_value - avg)
    is_anomaly = deviation > threshold
    logging.debug(f"Anomaly detection - Current Value: {current_value}, Avg: {avg}, Deviation: {deviation}, Is Anomaly: {is_anomaly}")
    return is_anomaly

def send_alert(message: str) -> None:
    """Send an alert email."""
    sender_email = "sender@example.com"
    receiver_email = "receiver@example.com"
    
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

def adjust_thresholds(new_thresholds: Dict[str, int]) -> None:
    """Adjust health check thresholds in the configuration."""
    try:
        with open(get_config_file_path(), 'r+') as config_file:
            config = json.load(config_file)
            config.update(new_thresholds)
            config_file.seek(0)
            json.dump(config, config_file, indent=4)
            config_file.truncate()
    except PermissionError:
        logging.error(f"Permission denied: {get_config_file_path()}")
    except json.JSONDecodeError:
        logging.error("Error decoding JSON from the configuration file.")
    except Exception as e:
        logging.error(f"Unexpected error while adjusting thresholds: {e}")

def clear_terminal() -> None:
    """Clear the terminal screen."""
    os.system('cls' if platform.system() == 'Windows' else 'clear')

# Example function call to start the server and monitoring
if __name__ == '__main__':
    setup_logging('DEBUG')  # Set the logging level as required
    start_prometheus_server(8000)  # Start Prometheus server on port 8000
    
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
            historical_data[key].append(health_results.get(f'{key}', 0))

        # Print health check results
        print(f"Timestamp: {health_results['timestamp']}")
        print(f"CPU Usage: {health_results['cpu_usage']}%")
        print(f"Memory Usage: {health_results['memory_usage']}%")
        print(f"Disk Usage: {health_results['disk_usage']}%")
        print(f"Needs Restart: {health_results['needs_restart']}")
        
        time.sleep(10)  # Wait for 10 seconds before the next health check
