import subprocess
import time
import httpx
import sys

print("Starting server...")
proc = subprocess.Popen(
    ["/Users/lakshaychandra/Documents/Semantic JSONL Converison/.venv/bin/python", "-m", "uvicorn", "api:app", "--port", "8003"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT
)
time.sleep(3)

print("Running E2E request...")
try:
    with open("artifacts/decision_rules.xlsx", "rb") as f:
        files = {"file": ("decision_rules.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        resp = httpx.post("http://localhost:8003/convert?usecase_id=test_e2e_gemini", files=files, timeout=30.0)
    
    print("\n========= RESULT =========")
    print(f"HTTP {resp.status_code}")
    print(resp.text)
    print("==========================")
except Exception as e:
    print(f"Error during request: {e}")
finally:
    proc.terminate()
    stdout, _ = proc.communicate()
    print("\n--- SERVER LOGS ---")
    print(stdout.decode())
    
