# app/sheets.py
import os
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# -------------------------------------------------
#  Google Sheets client
# -------------------------------------------------

SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")

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
    """Open a worksheet by title, create (with header) if doesn't exist."""
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

# -------------------------------------------------
#  ORDERS
# -------------------------------------------------

def _ensure_orders_cols(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["order_id", "client_name", "phone", "origin", "status", "note", "country", "updated_at"]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]

def get_order(order_id: str) -> Optional[Dict[str, Any]]:
    ws = get_worksheet("orders")
    rows = ws.get_all_records()
    for r in rows:
        if str(r.get("order_id", "")).strip().lower() == str(order_id).strip().lower():
            return r
    return None

def add_order(order: Dict[str, Any] = None, **kwargs) -> None:
    data = dict(order or {})
    data.update(kwargs)
    if not data.get("order_id"):
        raise ValueError("order_id is required")

    ws = get_worksheet("orders")
    values = ws.get_all_records()
    df = pd.DataFrame(values)
    if not df.empty:
        df = _ensure_orders_cols(df)

    now = _now()
    if df.empty:
        df = pd.DataFrame([{
            "order_id": data.get("order_id"),
            "client_name": data.get("client_name", ""),
            "phone": data.get("phone", ""),
            "origin": data.get("origin", ""),
            "status": data.get("status", ""),
            "note": data.get("note", ""),
            "country": data.get("country", ""),
            "updated_at": now,
        }])
    else:
        mask = df["order_id"].astype(str).str.lower() == str(data.get("order_id")).lower()
        if mask.any():
            idx = df.index[mask][0]
            for k in ["client_name", "phone", "origin", "status", "note", "country"]:
                if k in data:
                    df.loc[idx, k] = data.get(k, "")
            df.loc[idx, "updated_at"] = now
        else:
            df.loc[len(df)] = [
                data.get("order_id"),
                data.get("client_name", ""),
                data.get("phone", ""),
                data.get("origin", ""),
                data.get("status", ""),
                data.get("note", ""),
                data.get("country", ""),
                now,
            ]

    ws.clear()
    ws.append_row(list(df.columns))
    if len(df):
        ws.append_rows(df.values.tolist())

def update_order_status(order_id: str, new_status: str) -> bool:
    """Обновить статус заказа и updated_at. Возвращает True/False (найдена ли запись)."""
    ws = get_worksheet("orders")
    values = ws.get_all_records()
    if not values:
        return False
    df = pd.DataFrame(values)
    df = _ensure_orders_cols(df)
    mask = df["order_id"].astype(str).str.lower() == str(order_id).lower()
    if not mask.any():
        return False
    df.loc[mask, "status"] = new_status
    df.loc[mask, "updated_at"] = _now()
    ws.clear()
    ws.append_row(list(df.columns))
    if len(df):
        ws.append_rows(df.values.tolist())
    return True

def get_orders_by_note(marker: str) -> List[Dict[str, Any]]:
    """Вернуть все заказы, у которых note содержит подстроку marker (case-insensitive)."""
    ws = get_worksheet("orders")
    values = ws.get_all_records()
    if not values:
        return []
    df = pd.DataFrame(values)
    df = _ensure_orders_cols(df)
    m = str(marker).strip().lower()
    if not m:
        return []
    subset = df[df["note"].astype(str).str.lower().str.contains(m, na=False)]
    return subset.to_dict(orient="records")

def _parse_dt(s: str):
    from datetime import datetime
    try:
        # ожидаем ISO-строку вида 2025-10-27T12:34:56
        return datetime.fromisoformat(str(s))
    except Exception:
        return None

def list_recent_orders(limit: int = 20) -> list[dict]:
    """Последние обновлённые заказы по updated_at (desc)."""
    ws = get_worksheet("orders")
    values = ws.get_all_records()
    if not values:
        return []
    df = pd.DataFrame(values)
    df = _ensure_orders_cols(df)
    # приведём даты и отсортируем
    df["__dt"] = df["updated_at"].apply(_parse_dt)
    df = df.sort_values(by="__dt", ascending=False, na_position="last")
    res = df.drop(columns=["__dt"]).head(limit).to_dict(orient="records")
    return res

def list_orders_by_status(statuses) -> list[dict]:
    """
    Вернёт заказы по статусу/статусам (без учёта регистра).
    statuses: str | list[str]
    """
    if isinstance(statuses, str):
        statuses = [statuses]
    wanted = {str(s).strip().lower() for s in (statuses or []) if str(s).strip()}
    if not wanted:
        return []

    ws = get_worksheet("orders")
    values = ws.get_all_records()
    if not values:
        return []
    df = pd.DataFrame(values)
    df = _ensure_orders_cols(df)
    mask = df["status"].astype(str).str.lower().isin(wanted)
    return df[mask].to_dict(orient="records")

# -------------------------------------------------
#  ADDRESSES
# -------------------------------------------------

def _ensure_addr_cols(df: pd.DataFrame) -> pd.DataFrame:
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
    ws = get_worksheet("addresses")
    values = ws.get_all_records()
    df = pd.DataFrame(values)
    if not df.empty:
        df = _ensure_addr_cols(df)

    now = _now()
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

    ws.clear()
    ws.append_row(list(df.columns))
    if len(df):
        ws.append_rows(df.values.tolist())

def list_addresses(user_id: int) -> List[Dict[str, Any]]:
    ws = get_worksheet("addresses")
    values = ws.get_all_records()
    result: List[Dict[str, Any]] = []
    for r in values:
        if str(r.get("user_id", "")) == str(user_id):
            result.append(r)
    return result

def delete_address(user_id: int) -> bool:
    ws = get_worksheet("addresses")
    values = ws.get_all_records()
    if not values:
        return False
    df = pd.DataFrame(values)
    if df.empty:
        return False
    mask_keep = df["user_id"].astype(str) != str(user_id)
    if mask_keep.all():
        return False
    df = df[mask_keep]
    ws.clear()
    ws.append_row(list(df.columns))
    if len(df):
        ws.append_rows(df.values.tolist())
    return True

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

# -------------------------------------------------
#  SUBSCRIPTIONS
# -------------------------------------------------

def _ensure_subs_cols(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["user_id", "order_id", "last_sent_status", "created_at", "updated_at"]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]

def is_subscribed(user_id: int, order_id: str) -> bool:
    ws = get_worksheet("subscriptions")
    for r in ws.get_all_records():
        if str(r.get("user_id", "")) == str(user_id) and str(r.get("order_id", "")).lower() == order_id.lower():
            return True
    return False

def subscribe(user_id: int, order_id: str) -> None:
    ws = get_worksheet("subscriptions")
    values = ws.get_all_records()
    df = pd.DataFrame(values)
    if not df.empty:
        df = _ensure_subs_cols(df)

    now = _now()
    if df.empty:
        df = pd.DataFrame([{
            "user_id": user_id,
            "order_id": order_id,
            "last_sent_status": "",
            "created_at": now,
            "updated_at": now,
        }])
    else:
        mask = (df["user_id"].astype(str) == str(user_id)) & (df["order_id"].astype(str).str.lower() == order_id.lower())
        if mask.any():
            idx = df.index[mask][0]
            df.loc[idx, "updated_at"] = now
        else:
            df.loc[len(df)] = [user_id, order_id, "", now, now]

    ws.clear()
    ws.append_row(list(df.columns))
    if len(df):
        ws.append_rows(df.values.tolist())

def unsubscribe(user_id: int, order_id: str) -> bool:
    ws = get_worksheet("subscriptions")
    values = ws.get_all_records()
    if not values:
        return False
    df = pd.DataFrame(values)
    mask_keep = ~((df["user_id"].astype(str) == str(user_id)) & (df["order_id"].astype(str).str.lower() == order_id.lower()))
    if mask_keep.all():
        return False
    df = df[mask_keep]
    ws.clear()
    ws.append_row(list(df.columns))
    if len(df):
        ws.append_rows(df.values.tolist())
    return True

def list_subscriptions(user_id: int) -> List[Dict[str, Any]]:
    ws = get_worksheet("subscriptions")
    values = ws.get_all_records()
    result = []
    for r in values:
        if str(r.get("user_id", "")) == str(user_id):
            result.append(r)
    return result

def get_all_subscriptions() -> List[Dict[str, Any]]:
    """Вернуть все подписки (для рассылки подписчикам по статусу)."""
    ws = get_worksheet("subscriptions")
    return ws.get_all_records()

def set_last_sent_status(user_id: int, order_id: str, status: str) -> None:
    """Обновить last_sent_status у подписки; если нет — создать."""
    ws = get_worksheet("subscriptions")
    values = ws.get_all_records()
    df = pd.DataFrame(values)
    if not df.empty:
        df = _ensure_subs_cols(df)

    now = _now()
    if df.empty:
        df = pd.DataFrame([{
            "user_id": user_id, "order_id": order_id,
            "last_sent_status": status, "created_at": now, "updated_at": now
        }])
    else:
        mask = (df["user_id"].astype(str) == str(user_id)) & (df["order_id"].astype(str).str.lower() == order_id.lower())
        if mask.any():
            df.loc[mask, "last_sent_status"] = status
            df.loc[mask, "updated_at"] = now
        else:
            df.loc[len(df)] = [user_id, order_id, status, now, now]

    ws.clear()
    ws.append_row(list(df.columns))
    if len(df):
        ws.append_rows(df.values.tolist())

# -------------------------------------------------
#  PARTICIPANTS (разборы и оплаты)
# -------------------------------------------------

def _ensure_part_cols(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["order_id", "username", "paid", "qty", "created_at", "updated_at"]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]

def ensure_participants(order_id: str, usernames: List[str]) -> None:
    """Добавить участников в participants (если их ещё нет), paid=FALSE."""
    ws = get_worksheet("participants")
    values = ws.get_all_records()
    df = pd.DataFrame(values)
    if not df.empty:
        df = _ensure_part_cols(df)

    now = _now()
    to_add: List[List[Any]] = []
    existing = set()
    if not df.empty:
        for _, r in df.iterrows():
            if str(r["order_id"]).lower() == order_id.lower():
                existing.add(str(r["username"]).strip().lower())

    for u in usernames:
        uname = (u or "").lstrip("@").strip().lower()
        if not uname:
            continue
        if uname in existing:
            continue
        to_add.append([order_id, uname, "FALSE", "", now, now])

    if to_add:
        if not values:
            ws.append_row(["order_id", "username", "paid", "qty", "created_at", "updated_at"])
        ws.append_rows(to_add)

def get_participants(order_id: str) -> List[Dict[str, Any]]:
    """Список участников по разбору с полями username/paid/qty."""
    ws = get_worksheet("participants")
    data = ws.get_all_records()
    res: List[Dict[str, Any]] = []
    for r in data:
        if str(r.get("order_id", "")).strip().lower() == order_id.strip().lower():
            res.append({
                "order_id": r.get("order_id", ""),
                "username": str(r.get("username", "")).strip().lower(),
                "paid": str(r.get("paid", "")).strip().lower() in ("true", "1", "yes", "y"),
                "qty": r.get("qty", ""),
            })
    res.sort(key=lambda x: x["username"])
    return res

def set_participant_paid(order_id: str, username: str, paid: bool) -> bool:
    """Установить paid для username в разборе."""
    ws = get_worksheet("participants")
    values = ws.get_all_records()
    if not values:
        return False
    df = pd.DataFrame(values)
    df = _ensure_part_cols(df)
    uname = (username or "").lstrip("@").lower()
    mask = (df["order_id"].astype(str).str.lower() == order_id.lower()) & (df["username"].astype(str).str.lower() == uname)
    if not mask.any():
        return False
    df.loc[mask, "paid"] = "TRUE" if paid else "FALSE"
    df.loc[mask, "updated_at"] = _now()
    ws.clear()
    ws.append_row(list(df.columns))
    if len(df):
        ws.append_rows(df.values.tolist())
    return True

def toggle_participant_paid(order_id: str, username: str) -> bool:
    """Инвертировать paid для username; вернуть True, если нашли и обновили."""
    ws = get_worksheet("participants")
    values = ws.get_all_records()
    if not values:
        return False
    df = pd.DataFrame(values)
    df = _ensure_part_cols(df)
    uname = (username or "").lstrip("@").lower()
    mask = (df["order_id"].astype(str).str.lower() == order_id.lower()) & (df["username"].astype(str).str.lower() == uname)
    if not mask.any():
        return False
    current = str(df.loc[mask, "paid"].iloc[0]).strip().lower() in ("true", "1", "yes", "y")
    df.loc[mask, "paid"] = "FALSE" if current else "TRUE"
    df.loc[mask, "updated_at"] = _now()
    ws.clear()
    ws.append_row(list(df.columns))
    if len(df):
        ws.append_rows(df.values.tolist())
    return True

def get_unpaid_usernames(order_id: str) -> List[str]:
    ws = get_worksheet("participants")
    data = ws.get_all_records()
    result: List[str] = []
    for row in data:
        if str(row.get("order_id", "")).strip().lower() == order_id.strip().lower():
            paid = str(row.get("paid", "")).strip().lower()
            if paid not in ("true", "1", "yes", "y"):
                result.append(str(row.get("username", "")).strip().lower())
    return result

def get_all_unpaid_grouped() -> Dict[str, List[str]]:
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

def find_orders_for_username(username: str) -> List[str]:
    uname = (username or "").lstrip("@").lower()
    if not uname:
        return []
    ws = get_worksheet("participants")
    data = ws.get_all_records()
    result: List[str] = []
    for row in data:
        if str(row.get("username", "")).strip().lower() == uname:
            oid = str(row.get("order_id", "")).strip()
            if oid:
                result.append(oid)
    seen = set(); uniq = []
    for x in result:
        if x not in seen:
            uniq.append(x); seen.add(x)
    return uniq
