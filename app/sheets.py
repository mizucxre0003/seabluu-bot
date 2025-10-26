import os
import json
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from .config import GOOGLE_SHEETS_ID, GOOGLE_CREDENTIALS_JSON, GOOGLE_CREDENTIALS_FILE

SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ======== auth ========
def _client():
    if GOOGLE_CREDENTIALS_FILE and os.path.exists(GOOGLE_CREDENTIALS_FILE):
        with open(GOOGLE_CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            creds_info = json.load(f)
    else:
        creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPE)
    return gspread.authorize(creds)

def get_worksheet(name: str):
    gc = _client()
    sh = gc.open_by_key(GOOGLE_SHEETS_ID)
    try:
        return sh.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        # создаём лист с базовыми заголовками
        if name == "orders":
            ws = sh.add_worksheet(title="orders", rows=1000, cols=20)
            ws.append_row(["order_id","client_name","phone","origin","status","note","country","updated_at"])
            return ws
        if name == "addresses":
            ws = sh.add_worksheet(title="addresses", rows=1000, cols=20)
            ws.append_row(["user_id","full_name","phone","city","address","postcode","created_at","updated_at"])
            return ws
        if name == "subscriptions":
            ws = sh.add_worksheet(title="subscriptions", rows=1000, cols=20)
            ws.append_row(["user_id","order_id","last_sent_status","created_at","updated_at"])
            return ws
        raise

def df_from_ws(ws) -> pd.DataFrame:
    vals = ws.get_all_records()
    return pd.DataFrame(vals)

# ======== addresses ========
def upsert_address(user_id:int, full_name:str, phone:str, city:str, address:str, postcode:str):
    ws = get_worksheet("addresses")
    df = df_from_ws(ws)
    now = pd.Timestamp.utcnow().isoformat()
    if not df.empty:
        mask = df["user_id"] == user_id
        if mask.any():
            idx = df.index[mask][0]
            df.loc[idx, ["full_name","phone","city","address","postcode","updated_at"]] = [full_name,phone,city,address,postcode,now]
        else:
            df.loc[len(df)] = [user_id,full_name,phone,city,address,postcode,now,now]
    else:
        df = pd.DataFrame([{
            "user_id": user_id,
            "full_name": full_name,
            "phone": phone,
            "city": city,
            "address": address,
            "postcode": postcode,
            "created_at": now,
            "updated_at": now,
        }])
    ws.clear(); ws.append_row(list(df.columns)); ws.append_rows(df.values.tolist())

def list_addresses(user_id:int):
    ws = get_worksheet("addresses")
    df = df_from_ws(ws)
    if df.empty: 
        return []
    return df[df["user_id"]==user_id].to_dict("records")

def delete_address(user_id:int):
    ws = get_worksheet("addresses")
    df = df_from_ws(ws)
    if df.empty:
        return False
    df = df[df["user_id"]!=user_id]
    ws.clear()
    if df.empty:
        ws.append_row(["user_id","full_name","phone","city","address","postcode","created_at","updated_at"])
    else:
        ws.append_row(list(df.columns)); ws.append_rows(df.values.tolist())
    return True

# ======== orders & subscriptions ========

def get_order(order_id:str):
    ws = get_worksheet("orders")
    df = df_from_ws(ws)
    if df.empty:
        return None
    row = df[df["order_id"].astype(str)==str(order_id)]
    if row.empty:
        return None
    return row.to_dict("records")[0]

def subscribe(user_id:int, order_id:str):
    ws = get_worksheet("subscriptions")
    df = df_from_ws(ws)
    now = pd.Timestamp.utcnow().isoformat()
    # узнаём текущий статус, чтобы не слать старые
    order = get_order(order_id)
    last = order.get("status") if order else ""
    if df.empty:
        df = pd.DataFrame([{"user_id":user_id,"order_id":str(order_id),"last_sent_status":last,"created_at":now,"updated_at":now}])
    else:
        mask = (df["user_id"]==user_id) & (df["order_id"].astype(str)==str(order_id))
        if mask.any():
            idx = df.index[mask][0]
            df.loc[idx, ["last_sent_status","updated_at"]] = [last,now]
        else:
            df.loc[len(df)] = [user_id,str(order_id),last,now,now]
    ws.clear(); ws.append_row(list(df.columns)); ws.append_rows(df.values.tolist())

def unsubscribe(user_id:int, order_id:str):
    ws = get_worksheet("subscriptions")
    df = df_from_ws(ws)
    if df.empty: return False
    mask = ~((df["user_id"]==user_id) & (df["order_id"].astype(str)==str(order_id)))
    new = df[mask]
    ws.clear()
    if new.empty:
        ws.append_row(["user_id","order_id","last_sent_status","created_at","updated_at"])
    else:
        ws.append_row(list(new.columns)); ws.append_rows(new.values.tolist())
    return True

def list_subscriptions(user_id:int):
    ws = get_worksheet("subscriptions")
    df = df_from_ws(ws)
    if df.empty: return []
    return df[df["user_id"]==user_id].to_dict("records")

def scan_updates():
    """Сравнивает статусы и возвращает список уведомлений к отправке:
       [{user_id, order_id, new_status}] и одновременно обновляет last_sent_status.
    """
    ws_sub = get_worksheet("subscriptions")
    ws_ord = get_worksheet("orders")
    df_sub = df_from_ws(ws_sub)
    df_ord = df_from_ws(ws_ord)
    if df_sub.empty or df_ord.empty: 
        return []

    now = pd.Timestamp.utcnow().isoformat()
    merged = df_sub.merge(df_ord[["order_id","status"]], how="left", on="order_id")
    to_send = []
    for i, row in merged.iterrows():
        cur = str(row.get("status") or "")
        last = str(row.get("last_sent_status") or "")
        if cur and cur != last:
            to_send.append({"user_id": int(row["user_id"]), "order_id": str(row["order_id"]), "new_status": cur})
            df_sub.loc[df_sub.index[i], ["last_sent_status","updated_at"]] = [cur, now]

    ws_sub.clear(); ws_sub.append_row(list(df_sub.columns)); ws_sub.append_rows(df_sub.values.tolist())
    return to_send

# ---------- ADMIN HELPERS ----------

ADMIN_ORDERS_WS = "orders"

def now_ts() -> str:
    from datetime import datetime
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def _ensure_order_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Гарантируем базовые столбцы и не ломаем существующие.
       address_id не обязателен: если он есть — просто будет пустым."""
    base_cols = ["order_id","client_name","phone","origin","status","note","country","updated_at"]
    if df.empty:
        return pd.DataFrame(columns=base_cols)
    # добавим недостающие базовые
    for c in base_cols:
        if c not in df.columns:
            df[c] = ""
    return df

def add_order(record: dict) -> None:
    """
    record = {
      "order_id": "SB-12345",
      "client_name": "...",
      "country": "CN|KR",
      "status": "выкуплен",
      "note": "прим."
    }
    """
    ws = get_worksheet(ADMIN_ORDERS_WS)
    df = _ensure_order_columns(df_from_ws(ws))

    if (df["order_id"].astype(str) == str(record["order_id"])).any():
        raise ValueError("Такой order_id уже существует")

    row = {
        "order_id": str(record["order_id"]),
        "client_name": record.get("client_name",""),
        "phone": record.get("phone",""),
        "origin": record.get("origin",""),
        "status": record.get("status","выкуплен"),
        "note": record.get("note",""),
        "country": record.get("country","").upper(),
        "updated_at": now_ts(),
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    ws.clear()
    ws.update([df.columns.tolist()] + df.fillna("").values.tolist())

def update_order_status(order_id: str, new_status: str) -> bool:
    ws = get_worksheet(ADMIN_ORDERS_WS)
    df = df_from_ws(ws)
    if df.empty:
        return False
    hit = (df["order_id"].astype(str) == str(order_id))
    if not hit.any():
        return False
    df.loc[hit, "status"] = new_status
    # обновим last_update/updated_at — в зависимости от того, что есть
    if "updated_at" in df.columns:
        df.loc[hit, "updated_at"] = now_ts()
    if "last_update" in df.columns:
        df.loc[hit, "last_update"] = pd.Timestamp.utcnow().isoformat()
    ws.clear()
    ws.update([df.columns.tolist()] + df.fillna("").values.tolist())
    return True

def list_recent_orders(limit: int = 10) -> list[dict]:
    ws = get_worksheet(ADMIN_ORDERS_WS)
    df = df_from_ws(ws)
    if df.empty:
        return []
    # сортируем по времени, если есть подходящая колонка
    sort_col = "updated_at" if "updated_at" in df.columns else ("last_update" if "last_update" in df.columns else None)
    if sort_col:
        df = df.sort_values(sort_col, ascending=False)
    return df.head(limit).fillna("").to_dict(orient="records")
