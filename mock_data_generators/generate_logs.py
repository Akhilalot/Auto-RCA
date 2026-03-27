import json
import logging
import random
import uuid
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Configurations
NUM_APP_LOGS = 1000
NUM_TELEMETRY_LOGS = 800
NUM_CICD_LOGS = 50
TOTAL_SECONDS_IN_DAY = 86400

SERVICE_NAME = "checkout-service"

METHODS = ["validateCart", "calculateTaxes", "applyDiscount", "processPayment", "updateInventory", "confirmOrder"]
METRICS = ["cpu_utilization_percent", "memory_usage_mb", "payment_gateway_latency_ms", "db_query_latency_ms", "active_checkout_sessions", "checkout_error_rate_percent"]
STAGES = ["build", "test", "security_scan", "deploy_staging", "deploy_production"]

# Define timeline bounds
start_time = datetime.utcnow() - timedelta(days=1)

# Inject an "Incident Window" halfway through the day for the Agent to discover
incident_start = start_time + timedelta(hours=12)
incident_end = incident_start + timedelta(minutes=45)

def is_incident(t: datetime) -> bool:
    return incident_start <= t <= incident_end

def get_app_message(method: str, level: str) -> str:
    if level in ["INFO", "DEBUG"]:
        # Expanded variety of INFO messages for a realistic application
        info_msgs = {
            "validateCart":[
                "Cart validated successfully.",
                "User cart retrieved from Redis cache.",
                "Cart items cross-referenced with active catalog.",
                "Session cart merged with active user profile.",
                "Detected 3 items in cart, total weight calculated."
            ],
            "calculateTaxes":[
                "Taxes calculated based on user shipping region.",
                "Tax exemptions checked for current user profile.",
                "Fetched real-time tax rates from external provider.",
                "Applied standard VAT to eligible cart items."
            ],
            "applyDiscount":[
                "Promo code applied to cart total.",
                "Checked user loyalty points balance.",
                "Seasonal discount rule evaluated and applied.",
                "No valid promo codes found in request, proceeding with base price.",
                "Free shipping threshold met, shipping cost zeroed."
            ],
            "processPayment":[
                "Payment transaction initiated.",
                "Tokenizing payment method details securely.",
                "Awaiting authorization from upstream payment gateway.",
                "3D Secure verification step triggered.",
                "Fraud detection pre-check passed successfully."
            ],
            "updateInventory":[
                "Items temporarily reserved in inventory.",
                "Stock levels decremented for purchased items.",
                "Warehouse allocation confirmed for shipping.",
                "Inventory lock acquired for active transaction."
            ],
            "confirmOrder":[
                "Order confirmation email queued for dispatch.",
                "Order successfully written to primary database.",
                "Push notification dispatched to user's mobile client.",
                "Generated digital PDF receipt for order.",
                "Order status updated to 'PROCESSING'."
            ]
        }
        # Randomly select one of the INFO messages for the given method
        return random.choice(info_msgs.get(method, ["Operation completed successfully."]))
        
    elif level == "WARN":
        return f"Retrying {method} due to slow response or rate limit."
    else: # ERROR
        errs = {
            "validateCart": "InvalidItemException: Item out of stock during checkout.",
            "calculateTaxes": "TaxProviderTimeout: Upstream API failed to respond.",
            "applyDiscount": "ExpiredPromoCodeException: Attempted use of invalid code.",
            "processPayment": "PaymentGatewayTimeoutException: Upstream provider timeout.",
            "updateInventory": "InventorySyncError: Deadlock found when locking DB row.",
            "confirmOrder": "NotificationDeliveryFailed: SMTP server rejected connection."
        }
        return errs.get(method, "Internal Server Error.")

def generate_correlated_logs() -> tuple[list, list, list]:
    app_logs = []
    telemetry_logs = []
    cicd_logs = []

    # 1. Generate Application Logs
    app_step = TOTAL_SECONDS_IN_DAY / NUM_APP_LOGS
    current_time = start_time
    
    for _ in range(NUM_APP_LOGS):
        current_time += timedelta(seconds=(app_step + random.uniform(-10, 10)))
        
        # If during the incident, increase error rates significantly
        if is_incident(current_time):
            lvl = random.choices(["INFO", "WARN", "ERROR"], weights=[0.4, 0.2, 0.4])[0]
            method = "processPayment" if lvl == "ERROR" else random.choice(METHODS)
        else:
            lvl = random.choices(["INFO", "DEBUG", "WARN", "ERROR"], weights=[0.85, 0.10, 0.04, 0.01])[0]
            method = random.choice(METHODS)

        app_logs.append({
            "timestamp": current_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "log_type": "application",
            "trace_id": f"chk-{uuid.uuid4().hex[:8]}",
            "service": SERVICE_NAME,
            "method": method,
            "level": lvl,
            "message": get_app_message(method, lvl)
        })

    # 2. Generate Telemetry Logs
    tel_step = TOTAL_SECONDS_IN_DAY / NUM_TELEMETRY_LOGS
    current_time = start_time
    
    for _ in range(NUM_TELEMETRY_LOGS):
        current_time += timedelta(seconds=(tel_step + random.uniform(-5, 5)))
        metric = random.choice(METRICS)
        
        # Spike telemetry during the incident
        if is_incident(current_time):
            if metric == "cpu_utilization_percent": val = round(random.uniform(85.0, 99.9), 2)
            elif metric == "payment_gateway_latency_ms": val = round(random.uniform(4000.0, 9500.0), 2)
            elif metric == "checkout_error_rate_percent": val = round(random.uniform(15.0, 45.0), 2)
            elif metric == "active_checkout_sessions": val = random.randint(1500, 3000)
            else: val = round(random.uniform(512.0, 4096.0), 2) # memory/db
        else:
            if metric == "cpu_utilization_percent": val = round(random.uniform(10.0, 45.0), 2)
            elif metric == "memory_usage_mb": val = round(random.uniform(512.0, 2048.0), 2)
            elif metric == "payment_gateway_latency_ms": val = round(random.uniform(150.0, 400.0), 2)
            elif metric == "db_query_latency_ms": val = round(random.uniform(5.0, 45.0), 2)
            elif metric == "active_checkout_sessions": val = random.randint(50, 400)
            else: val = round(random.uniform(0.0, 1.5), 2) # error rate

        telemetry_logs.append({
            "timestamp": current_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "log_type": "telemetry",
            "service": SERVICE_NAME,
            "metric": metric,
            "value": val
        })

    # 3. Generate CI/CD Logs
    cicd_step = TOTAL_SECONDS_IN_DAY / NUM_CICD_LOGS
    current_time = start_time
    
    for _ in range(NUM_CICD_LOGS):
        current_time += timedelta(seconds=(cicd_step + random.uniform(-100, 100)))
        
        # Inject deploy before incident, rollback at the end
        if incident_start - timedelta(minutes=10) <= current_time <= incident_start:
            stage, status = "deploy_production", "SUCCESS"
        elif incident_end <= current_time <= incident_end + timedelta(minutes=10):
            stage, status = "rollback_production", "SUCCESS"
        else:
            stage = random.choice(STAGES)
            status = random.choices(["SUCCESS", "IN_PROGRESS", "FAILED"], weights=[0.8, 0.1, 0.1])[0]

        cicd_logs.append({
            "timestamp": current_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "log_type": "cicd",
            "pipeline_id": f"pipe_chk_{random.randint(100, 999)}",
            "service": SERVICE_NAME,
            "commit_hash": uuid.uuid4().hex[:7],
            "stage": stage,
            "status": status,
            "duration_sec": random.randint(30, 400)
        })

    return app_logs, telemetry_logs, cicd_logs

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app_logs, telemetry_logs, cicd_logs = generate_correlated_logs()

    with open("application_logs.json", "w", encoding="utf-8") as f:
        json.dump(app_logs, f, indent=2)

    with open("telemetry_logs.json", "w", encoding="utf-8") as f:
        json.dump(telemetry_logs, f, indent=2)

    with open("cicd_logs.json", "w", encoding="utf-8") as f:
        json.dump(cicd_logs, f, indent=2)

    logger.info("Successfully generated 3 synchronized log files.")