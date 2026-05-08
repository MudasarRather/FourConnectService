
import urllib.request
import json
import ssl

# Cache for exchange rates to minimize API calls
# Key: "FROM_TO", Value: float rate
rate_cache = {}

FALLBACK_RATES = {
    'USD': 1.0, 
    'EUR': 1.05, 
    'GBP': 1.25, 
    'INR': 0.012, 
    'JPY': 0.007, 
    'CAD': 0.74, 
    'AUD': 0.65
}

def get_rate(from_curr: str, to_curr: str) -> float:
    """
    Fetch exchange rate safely with SSL handling and fallback support.
    Returns 1.0 on total failure.
    """
    if not from_curr: from_curr = 'USD'
    if not to_curr: to_curr = 'USD'
    
    # Normalize
    from_curr = from_curr.upper()
    to_curr = to_curr.upper()

    if from_curr == to_curr: 
        return 1.0
    
    key = f"{from_curr}_{to_curr}"
    if key in rate_cache: 
        return rate_cache[key]
    
    # 1. Try Live API
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        url = f"https://open.er-api.com/v6/latest/{from_curr}"
        with urllib.request.urlopen(url, context=ctx, timeout=2) as response:
            data = json.loads(response.read().decode())
            # Use .get() chain to allow missing keys without crash
            rate = data.get('rates', {}).get(to_curr)
            if rate:
                rate_cache[key] = float(rate)
                return float(rate)
    except Exception as e:
        print(f"Currency API warning for {from_curr}->{to_curr}: {e}")
    
    # 2. Fallback Logic
    try:
        from_val = FALLBACK_RATES.get(from_curr, 1.0)
        to_val = FALLBACK_RATES.get(to_curr, 1.0)
        # Convert From -> USD -> To
        # Note: FALLBACK_RATES are likely "Value in USD" (e.g. 1 EUR = 1.05 USD)
        # So: 1 FromUnit * FromVal = X USD
        # X USD / ToVal = Y ToUnit
        return from_val / to_val
    except:
        return 1.0
