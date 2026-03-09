from flask import Flask, request, jsonify, render_template
from pathlib import Path
from datetime import datetime
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)

# ---------------------------
# File locations
# ---------------------------
METRICS_FILE = Path("metrics.jsonl")
ALERTS_FILE = Path("alerts.jsonl")

# ---------------------------
# Alert settings
# ---------------------------
ALERT_THRESHOLD_DBFS = -10.0
ALERT_SECONDS_REQUIRED = 3

# Prevent too many emails
ALERT_COOLDOWN_SECONDS = 300  # 5 minutes
last_email_time = None

# ---------------------------
# Email settings
# Replace these with your NEW values
# ---------------------------
EMAIL_ENABLED = True
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "your_email@gmail.com"
SENDER_PASSWORD = "your_new_16_char_app_password"
RECIPIENT_EMAIL = "your_recipient@gmail.com"

def append_jsonl(path: Path, obj: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj) + "\n")

def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []

    records = []
    for line in text.splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return records

def analyze_recording_for_alert(dbfs_series, threshold, min_seconds):
    """
    Returns:
      triggered (bool)
      exceed_count (int)
      exceed_times (list[int]) -> second indices above threshold
    """
    if not isinstance(dbfs_series, list):
        return False, 0, []

    exceed_times = []
    for i, value in enumerate(dbfs_series):
        try:
            if float(value) >= threshold:
                exceed_times.append(i)
        except (TypeError, ValueError):
            continue

    triggered = len(exceed_times) >= min_seconds
    return triggered, len(exceed_times), exceed_times

def send_alert_email(alert_event: dict) -> None:
    subject = f"Noise Alert from {alert_event.get('device_id', 'unknown')}"

    confidence_text = "N/A"
    confidence = alert_event.get("yamnet_confidence")
    if confidence is not None:
        try:
            confidence_text = f"{float(confidence) * 100:.1f}%"
        except (TypeError, ValueError):
            confidence_text = "N/A"

    body = f"""
A high-noise event was detected from the latest recording.

Timestamp: {alert_event.get('timestamp', 'N/A')}
Device: {alert_event.get('device_id', 'N/A')}
Detected source: {alert_event.get('yamnet_label', 'unknown')}
Confidence: {confidence_text}
Average dBFS: {alert_event.get('avg_dbfs', 'N/A')}
Max dBFS: {alert_event.get('max_dbfs', 'N/A')}
Threshold: {alert_event.get('threshold_dbfs', 'N/A')} dBFS
Seconds above threshold: {alert_event.get('seconds_exceeded', 'N/A')}
Second indices above threshold: {alert_event.get('exceed_times_seconds', [])}
Reason: {alert_event.get('reason', 'Threshold exceeded')}
"""

    msg = MIMEMultipart()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())

@app.get("/")
def dashboard():
    return render_template("dashboard.html")

@app.post("/metrics")
def metrics():
    global last_email_time

    data = request.get_json(force=True)

    timestamp = data.get("timestamp")
    if not timestamp:
        timestamp = datetime.utcnow().isoformat() + "Z"
        data["timestamp"] = timestamp

    device_id = data.get("device_id", "unknown")
    avg_dbfs = data.get("avg_dbfs")
    max_dbfs = data.get("max_dbfs")
    yamnet_label = data.get("yamnet_label", "unknown")
    yamnet_confidence = data.get("yamnet_confidence")
    dbfs_series = data.get("dbfs_series_1s", [])

    # Save every incoming metric event
    append_jsonl(METRICS_FILE, data)

    alert_triggered = False
    alert_reason = None
    email_sent = False

    triggered, exceed_count, exceed_times = analyze_recording_for_alert(
        dbfs_series,
        ALERT_THRESHOLD_DBFS,
        ALERT_SECONDS_REQUIRED
    )

    print("dbfs_series length:", len(dbfs_series) if isinstance(dbfs_series, list) else "not a list")
    print("triggered:", triggered)
    print("exceed_count:", exceed_count)
    print("exceed_times:", exceed_times)

    if triggered:
        print("ENTERED if triggered block")
        print("Writing alert to:", ALERTS_FILE.resolve())

        alert_triggered = True
        alert_reason = f"Recording exceeded threshold for {exceed_count} second(s)"

        alert_event = {
            "timestamp": timestamp,
            "device_id": device_id,
            "avg_dbfs": avg_dbfs,
            "max_dbfs": max_dbfs,
            "yamnet_label": yamnet_label,
            "yamnet_confidence": yamnet_confidence,
            "threshold_dbfs": ALERT_THRESHOLD_DBFS,
            "seconds_required": ALERT_SECONDS_REQUIRED,
            "seconds_exceeded": exceed_count,
            "exceed_times_seconds": exceed_times,
            "reason": alert_reason
        }

        append_jsonl(ALERTS_FILE, alert_event)

        if EMAIL_ENABLED:
            now = datetime.utcnow()
            print("EMAIL_ENABLED is True")
            print("Current time:", now)
            print("Last email time:", last_email_time)

            if (
                last_email_time is None
                or (now - last_email_time).total_seconds() >= ALERT_COOLDOWN_SECONDS
            ):
                print("Cooldown passed, attempting to send email...")
                try:
                    send_alert_email(alert_event)
                    last_email_time = now
                    email_sent = True
                    print("Email sent successfully.")
                except Exception as e:
                    print(f"Failed to send alert email: {e}")
            else:
                print("Cooldown not passed, skipping email.")

    return jsonify({
        "ok": True,
        "alert_triggered": alert_triggered,
        "threshold_dbfs": ALERT_THRESHOLD_DBFS,
        "seconds_required": ALERT_SECONDS_REQUIRED,
        "reason": alert_reason,
        "email_sent": email_sent
    })

@app.get("/latest")
def latest():
    records = read_jsonl(METRICS_FILE)
    if not records:
        return jsonify({"ok": False, "error": "no data yet"}), 404
    return jsonify(records[-1])

@app.get("/history")
def history():
    records = read_jsonl(METRICS_FILE)
    return jsonify(records)

@app.get("/alerts")
def alerts():
    records = read_jsonl(ALERTS_FILE)
    return jsonify(records)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)