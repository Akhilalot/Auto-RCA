import requests
url="http://testignore-fredhca8d8erfhdc.centralindia-01.azurewebsites.net/"
#url="http://localhost:8000/"
response = requests.post(
    f"{url}run-agent",
    json={
        "time_stamp": "2026-03-25T10:30:00Z",
        "issue": "High API latency detected on the payment service. Response times have increased from 200ms to over 2s in the last 30 minutes."
    },
)

print("Status:", response.status_code)
print("Response:", response.json())
