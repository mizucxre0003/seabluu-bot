# app/sheets.py
import os
import json
from datetime import datetime
from typing import List, Dict, Any

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# =========================
#  Google Sheets client
# =========================

SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _client():
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE")
    if creds_json:
        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPE)
    elif creds_file:
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPE)
    else:
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON or GOOGLE_CREDENTIALS_FILE is not set")
    return gspread.authorize(creds)


def _sheet():
    sid = os.getenv("GOOGLE_SHEETS_ID")
    if not sid:
        raise RuntimeError("GOOGLE_SHEETS_ID is not set")
    return _client().open_by_key(sid)


def get_worksheet(title: str):
    """Open a worksheet by title, create with header if doesn't exist."""
    sh = _sheet()
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=20)
        if title == "orders":
            ws.append_row(["order_id", "client_name", "phone", "origin", "status", "note", "country", "updated_at"])
        elif title == "addresses":
            ws.append_row(["user_id", "username", "full_name", "phone", "city", "address", "postcode", "created_at", "updated_at"])
        elif title == "subscriptions":
            ws.append_row(["user_id", "order_id", "last_sent_status", "created_at", "updated_at"])
        elif title == "participants":
            ws.append_row(["order_id", "username", "paid", "qty", "created_at", "updated_at"])
        return ws


# =========================
#  Addresses
# =========================

def _ensure_addresses_columns(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["user_id", "username", "full_name", "phone", "city", "address", "postcode", "created_at", "updated_at"]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]


def upsert_address(
    user_id: int,
    full_name: str,
    phone: str,
    city: str,
    address: str,
    postcode: str,
    username: str | None = ""
):
    """Upsert address; store username canonically in lowercase (no leading @)."""
    ws = get_worksheet("addresses")
    values = ws.get_all_records()
    df = pd.DataFrame(values)
    if not df.empty:
        df = _ensure_addresses_columns(df)

    now = datetime.utcnow().isoformat(timespec="seconds")
    uname = (username or "").lstrip("@").lower()

    if df.empty:
        df = pd.DataFrame([{
            "user_id": user_id,
            "username": uname,
            "full_name": full_name,
            "phone": phone,
            "city": city,
            "address": address,
            "postcode": postcode,
            "created_at": now,
            "updated_at": now,
        }])
    else:
        mask = df["user_id"].astype(str) == str(user_id)
        if mask.any():
            idx = df.index[mask][0]
            df.loc[idx, ["username", "full_name", "phone", "city", "address", "postcode", "updated_at"]] = [
                uname, full_name, phone, city, address, postcode, now
            ]
        else:
            df.loc[len(df)] = [user_id, uname, full_name, phone, city, address, postcode, now, now]

    # Write back
    ws.clear()
    ws.append_row(list(df.columns))
    ws.append_rows(df.values.tolist())


def get_addresses_by_usernames(usernames: List[str]) -> List[Dict[str, Any]]:
    ws = get_worksheet("addresses")
    data = ws.get_all_records()
    by_user = {str((row.get("username") or "").strip().lower()): row for row in data}
    result = []
    for u in usernames:
        row = by_user.get((u or "").strip().lower())
        if row:
            result.append(row)
    return result


def get_user_ids_by_usernames(usernames: List[str]) -> List[int]:
    rows = get_addresses_by_usernames(usernames)
    ids: List[int] = []
    for r in rows:
        try:
            ids.append(int(r.get("user_id")))
        except Exception:
            pass
    return ids


# =========================
#  Participants (payments)
# =========================

def get_unpaid_usernames(order_id: str) -> List[str]:
    ws = get_worksheet("participants")
    data = ws.get_all_records()
    result: List[str] = []
    for row in data:
        if str(row.get("order_id", "")).strip() == str(order_id).strip():
            paid = str(row.get("paid", "")).strip().lower()
            if paid not in ("true", "1", "yes", "y"):
                result.append(str(row.get("username", "")).strip().lower())
    return result


def get_all_unpaid_grouped() -> Dict[str, List[str]]:
    """Return dict {order_id: [username_lower,...]} for all unpaid participants."""
    ws = get_worksheet("participants")
    data = ws.get_all_records()
    grouped: Dict[str, List[str]] = {}
    for row in data:
        order_id = str(row.get("order_id", "")).strip()
        username = str(row.get("username", "")).strip().lower()
        paid = str(row.get("paid", "")).strip().lower()
        if order_id and username and paid not in ("true", "1", "yes", "y"):
            grouped.setdefault(order_id, []).append(username)
    return grouped


# =========================
#  Subscriptions (stubs)
# =========================
# Оставил заглушки, чтобы код не падал, если где-то вызывается.
# Подставьте ваши реализации при необходимости.

def find_orders_for_username(username: str) -> List[str]:
    """Stub: return [] by default. Replace with your logic if используется."""
    return []
