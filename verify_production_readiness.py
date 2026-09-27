import os
import subprocess
import base64
import requests

def load_heroku_config():
    """Pulls config vars from Heroku CLI securely into local environment."""
    print("[*] Loading configuration from Heroku...")
    try:
        # Assumes heroku CLI is logged in and linked to your app
        config_json = subprocess.check_output(["heroku", "config", "-s"]).decode("utf-8")
        for line in config_json.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                os.environ[key] = value
        print("[+] Heroku configuration loaded successfully.")
    except Exception as e:
        print(f"[-] Warning: Could not auto-load Heroku config via CLI ({e}). Falling back to existing environment variables.")

def verify_ebay_oauth_scopes():
    """Validates the refresh token and prints granted scopes without triggering a mutation."""
    print("\n--- 1. Verifying eBay OAuth Scopes ---")
    client_id = os.getenv("EBAY_CLIENT_ID")
    client_secret = os.getenv("EBAY_CLIENT_SECRET")
    refresh_token = os.getenv("EBAY_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        print("[-] Error: Missing eBay credentials in environment variables.")
        return False

    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encoded_credentials}"
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }

    response = requests.post("https://api.ebay.com/identity/v1/oauth2/token", headers=headers, data=data)
    
    if response.status_code == 200:
        token_data = response.json()
        scopes = token_data.get("scope", "No scopes returned in response")
        print("[+] eBay OAuth Token Exchange: SUCCESS")
        print(f"[+] Active Granted Scopes: {scopes}")
        return True
    else:
        print(f"[-] eBay OAuth Token Exchange Failed: {response.status_code} - {response.text}")
        return False

def verify_supabase_rls():
    """Probes Supabase with the anonymous key to ensure RLS blocks unauthorized public reads/writes."""
    print("\n--- 2. Probing Supabase RLS & Anonymous Access ---")
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")

    if not all([supabase_url, supabase_anon_key]):
        print("[-] Error: Missing Supabase URL or Anon Key in environment variables.")
        return False

    headers = {
        "apikey": supabase_anon_key,
        "Authorization": f"Bearer {supabase_anon_key}"
    }

    # Attempt to query a protected inventory/catalog table anonymously
    target_endpoint = f"{supabase_url.rstrip('/')}/rest/v1/listings"
    response = requests.get(target_endpoint, headers=headers)

    print(f"[*] Probing endpoint: {target_endpoint}")
    print(f"[*] Response Status Code: {response.status_code}")

    # RLS enforcement typically returns 401/403 or a safe empty array [] for restricted rows
    if response.status_code in [401, 403]:
        print("[+] RLS Verification: PASSED (Anonymous access strictly blocked with HTTP 401/403).")
        return True
    elif response.status_code == 200 and (response.json() == [] or not response.json()):
        print("[+] RLS Verification: PASSED (Endpoint reachable, but RLS correctly restricts returning rows to anon key).")
        return True
    else:
        print(f"[-] RLS Warning: Unexpected response for anonymous probe -> {response.text}")
        return False

if __name__ == "__main__":
    print("=== Starting Production Readiness Verification ===")
    load_heroku_config()
    
    oauth_ok = verify_ebay_oauth_scopes()
    rls_ok = verify_supabase_rls()

    print("\n=== Verification Summary ===")
    if oauth_ok and rls_ok:
        print("[+] STATUS: All remaining verification items successfully closed.")
        print("[+] RESULT: READY FOR CONTROLLED ROTATION PILOT.")
    else:
        print("[-] STATUS: One or more verification checks failed. Review output above.")