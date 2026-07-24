# telebirr_verify.py
# ============================================
# HABESHA BET - TELEBIRR ONLINE RECEIPT VERIFICATION
# ============================================

import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("habesha_bet")

TELEBIRR_RECEIPT_URL = "https://transactioninfo.ethiotelecom.et/receipt/{receipt_no}"


def verify_receipt_online(receipt_no: str, timeout: int = 10) -> dict:
    """
    Fetch and verify a Telebirr receipt online.

    Returns dict with:
        ok: bool
        amount: float (if ok)
        recipient_name: str (if ok)
        recipient_phone_last4: str (if ok)
        reference: str (if ok)
        timestamp: str (if ok)
        error: str (if not ok)
    """
    if not receipt_no or not re.match(r'^[A-Za-z0-9]{8,20}$', receipt_no):
        return {"ok": False, "error": "invalid_receipt_number"}

    url = TELEBIRR_RECEIPT_URL.format(receipt_no=receipt_no.upper())
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code != 200:
            return {"ok": False, "error": f"receipt_not_found_http_{resp.status_code}"}
    except requests.Timeout:
        logger.warning("[telebirr_verify] timeout fetching receipt %s", receipt_no)
        return {"ok": False, "error": "receipt_site_timeout"}
    except requests.ConnectionError:
        logger.warning("[telebirr_verify] connection error fetching receipt %s", receipt_no)
        return {"ok": False, "error": "receipt_site_unreachable"}
    except Exception as e:
        logger.warning("[telebirr_verify] unexpected error fetching receipt %s: %s", receipt_no, e)
        return {"ok": False, "error": "receipt_fetch_error"}

    html = resp.text
    result = parse_receipt_html(html)

    if not result.get("ok"):
        return result

    result["reference"] = receipt_no.upper()
    return result


def parse_receipt_html(html: str) -> dict:
    """
    Parse Telebirr receipt HTML page.
    Extract: amount, recipient name, recipient phone last 4, reference, timestamp.
    Use BeautifulSoup with resilient selectors.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        return {"ok": False, "error": f"html_parse_error: {e}"}

    amount = _extract_amount(soup)
    recipient_name = _extract_recipient_name(soup)
    recipient_phone_last4 = _extract_recipient_phone_last4(soup)
    timestamp = _extract_timestamp(soup)

    if amount is None:
        return {"ok": False, "error": "amount_not_found_on_receipt"}

    return {
        "ok": True,
        "amount": amount,
        "recipient_name": recipient_name or "",
        "recipient_phone_last4": recipient_phone_last4 or "",
        "timestamp": timestamp or "",
    }


def _extract_amount(soup) -> Optional[float]:
    text = soup.get_text(separator=" ", strip=True)
    m = re.search(r'(?:amount|transferred|debit|paid)[\s:]*ETB\s*([\d,]+(?:\.\d{1,2})?)', text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    m = re.search(r'ETB\s*([\d,]+(?:\.\d{1,2})?)', text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def _extract_recipient_name(soup) -> Optional[str]:
    text = soup.get_text(separator=" ", strip=True)
    patterns = [
        r'(?:to|recipient|beneficiary)[\s:]*([A-Za-z][A-Za-z\s]{2,40}?)(?:\s*\(|\s*$|\s*–|\s*-)',
        r'Payee[\s:]*([A-Za-z][A-Za-z\s]{2,40}?)(?:\s*\(|\s*$|\s*–|\s*-)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            if len(name) >= 2:
                return name
    return None


def _extract_recipient_phone_last4(soup) -> Optional[str]:
    text = soup.get_text(separator=" ", strip=True)
    m = re.search(r'(?:\(|phone[\s:]*|to[\s:]*)(?:2519|09)?\d*\*+(\d{4})(?:\))?', text)
    if m:
        return m.group(1)
    return None


def _extract_timestamp(soup) -> Optional[str]:
    text = soup.get_text(separator=" ", strip=True)
    m = re.search(r'(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})', text)
    if m:
        return m.group(1)
    m = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', text)
    if m:
        return m.group(1)
    return None
