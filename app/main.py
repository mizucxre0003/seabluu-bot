import os
import json
import re
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
        if name == "orders":
            ws = sh.add_worksheet(title="orders", rows=2000, cols=20)
            ws.append_row(["order_id","client_name","phone","origin","status","note","country","updated_at"])
            return ws
        if name == "addresses":
            ws = sh.add_worksheet(title="addresses", rows=2000, cols=20)
            ws.append_row(["user_id","username","full_name","phone","city","address","postcode","created_at","updated_at"])
            return ws
        if name == "subscriptions":
            ws = sh.add_worksheet(title="subscriptions", rows=2000, cols=20)
            ws.append_row(["user_id","order_id","last_sent_status","created_at","updated_at"])
            return ws
        if name == "participants":
            ws = sh.add_worksheet(title="participants", rows=2000, cols=20)
            ws.append_row(["order_id","username","paid","qty","created_at","updated_at"])
            return ws
        raise

def df_from_ws(ws) -> pd.DataFrame:
    vals = ws.get_all_records()
    return pd.DataFrame(vals)

# ======== helpers ========
def _ensure_addresses_columns(df: pd.DataFrame) -> pd.DataFrame:
    need = ["user_id","username","full_name","phone","city","address","postcode","created_at","updated_at"]
    if df.empty:
        return pd.DataFrame(columns=need)
    for c in need:
        if c not in df.columns:
            df[c] = ""
    return df[need]

def _now_iso() -> str:
    return pd.Timestamp.utcnow().isoformat()

def _now_h() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# ======== addresses ========
def upsert_address(user_id:int, full_name:str, phone:str, city:str, address:str, postcode:str, username:str|None=""):
    ws = get_worksheet("addresses")
    df_raw = df_from_ws(ws)
    df = _ensure_addresses_columns(df_raw)
    now = _now_iso()
    uname = (username or "").lstrip("@")

    if not df.empty:
        mask = df["user_id"] == user_id
        if mask.any():
            idx = df.index[mask][0]
            df.loc[idx, ["username","full_name","phone","city","address","postcode","updated_at"]] = [
                uname, full_name, phone, city, address, postcode, now
            ]
        else:
            df.loc[len(df)] = [user_id, uname, full_name, phone, city, address, postcode, now, now]
    else:
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

    ws.clear(); ws.append_row(list(df.columns)); ws.append_rows(df.values.tolist())

def list_addresses(user_id:int):
    ws = get_worksheet("addresses")
    df = _ensure_addresses_columns(df_from_ws(ws))
    if df.empty: 
        return []
    return df[df["user_id"]==user_id].to_dict("records")

def delete_address(user_id:int):
    ws = get_worksheet("addresses")
    df = _ensure_addresses_columns(df_from_ws(ws))
    if df.empty:
        return False
    df = df[df["user_id"]!=user_id]
    ws.clear()
    if df.empty:
        ws.append_row(["user_id","username","full_name","phone","city","address","postcode","created_at","updated_at"])
    else:
        ws.append_row(list(df.columns)); ws.append_rows(df.values.tolist())
    return True

def get_addresses_by_usernames(usernames: list[str]) -> list[dict]:
    usernames = [u.lstrip("@").strip().lower() for u in usernames if u.strip()]
    if not usernames:
        return []
    ws = get_worksheet("addresses")
    df = _ensure_addresses_columns(df_from_ws(ws))
    if df.empty:
        return []
    df["__u"] = df["username"].astype(str).str.lower()
    res = df[df["__u"].isin(usernames)].drop(columns=["__u"])
    return res.to_dict("records")

def get_user_ids_by_usernames(usernames: list[str]) -> list[int]:
    recs = get_addresses_by_usernames(usernames)
    ids = []
    for r in recs:
        try:
            ids.append(int(r.get("user_id")))
        except Exception:
            pass
    return list({i for i in ids})

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

def add_order(record: dict) -> None:
    ws = get_worksheet("orders")
    df = df_from_ws(ws)
    now = _now_h()

    if not df.empty and (df["order_id"].astype(str) == str(record["order_id"])).any():
        raise ValueError("Такой order_id уже существует")

    row = {
        "order_id": str(record["order_id"]),
        "client_name": record.get("client_name",""),
        "phone": record.get("phone",""),
        "origin": record.get("origin",""),
        "status": record.get("status","выкуплен"),
        "note": record.get("note",""),
        "country": (record.get("country","") or "").upper(),
        "updated_at": now,
    }

    if df.empty:
        df = pd.DataFrame([row])
    else:
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    ws.clear()
    ws.update([df.columns.tolist()] + df.fillna("").values.tolist())

def update_order_status(order_id: str, new_status: str) -> bool:
    ws = get_worksheet("orders")
    df = df_from_ws(ws)
    if df.empty:
        return False
    hit = (df["order_id"].astype(str) == str(order_id))
    if not hit.any():
        return False
    df.loc[hit, "status"] = new_status
    if "updated_at" in df.columns:
        df.loc[hit, "updated_at"] = _now_h()
    ws.clear()
    ws.update([df.columns.tolist()] + df.fillna("").values.tolist())
    return True

def list_recent_orders(limit: int = 10) -> list[dict]:
    ws = get_worksheet("orders")
    df = df_from_ws(ws)
    if df.empty:
        return []
    sort_col = "updated_at" if "updated_at" in df.columns else None
    if sort_col:
        df = df.sort_values(sort_col, ascending=False)
    return df.head(limit).fillna("").to_dict(orient="records")

def list_orders_by_status(status: str) -> list[dict]:
    ws = get_worksheet("orders")
    df = df_from_ws(ws)
    if df.empty:
        return []
    mask = df["status"].astype(str).str.strip().str.lower() == str(status or "").strip().lower()
    return df[mask].fillna("").to_dict(orient="records")

# ======== subscriptions ========

def subscribe(user_id:int, order_id:str):
    ws = get_worksheet("subscriptions")
    df = df_from_ws(ws)
    now = _now_iso()
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

def is_subscribed(user_id:int, order_id:str) -> bool:
    ws = get_worksheet("subscriptions")
    df = df_from_ws(ws)
    if df.empty: 
        return False
    mask = (df["user_id"]==user_id) & (df["order_id"].astype(str)==str(order_id))
    return bool(mask.any())

def list_subscriptions(user_id:int):
    ws = get_worksheet("subscriptions")
    df = df_from_ws(ws)
    if df.empty: return []
    return df[df["user_id"]==user_id].to_dict("records")

def get_all_subscriptions() -> list[dict]:
    ws = get_worksheet("subscriptions")
    df = df_from_ws(ws)
    if df.empty: 
        return []
    return df.to_dict("records")

def set_last_sent_status(user_id:int, order_id:str, new_status:str):
    ws = get_worksheet("subscriptions")
    df = df_from_ws(ws)
    if df.empty: 
        return
    mask = (df["user_id"]==user_id) & (df["order_id"].astype(str)==str(order_id))
    if not mask.any():
        return
    now = _now_iso()
    df.loc[df.index[mask], ["last_sent_status","updated_at"]] = [new_status, now]
    ws.clear(); ws.append_row(list(df.columns)); ws.append_rows(df.values.tolist())

def scan_updates():
    ws_sub = get_worksheet("subscriptions")
    ws_ord = get_worksheet("orders")
    df_sub = df_from_ws(ws_sub)
    df_ord = df_from_ws(ws_ord)
    if df_sub.empty or df_ord.empty: 
        return []
    now = _now_iso()
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

# ======== participants (разборы) ========

def _participants_df() -> pd.DataFrame:
    ws = get_worksheet("participants")
    df = df_from_ws(ws)
    if df.empty:
        return pd.DataFrame(columns=["order_id","username","paid","qty","created_at","updated_at"])
    for c in ["order_id","username","paid","qty","created_at","updated_at"]:
        if c not in df.columns:
            df[c] = ""
    return df

def ensure_participants(order_id: str, usernames: list[str]) -> None:
    """Добавляет в participants отсутствующих @user из списка (paid = FALSE). username сохраняем без @, в нижнем регистре."""
    usernames = [u.lstrip("@").strip().lower() for u in usernames if u.strip()]
    if not usernames:
        return
    ws = get_worksheet("participants")
    df = _participants_df()
    now = _now_h()
    exist_mask = (df["order_id"].astype(str) == str(order_id))
    existing = set(df[exist_mask]["username"].astype(str).str.lower().tolist()) if not df.empty else set()

    for u in usernames:
        if u not in existing:
            df.loc[len(df)] = [str(order_id), u, "FALSE", "", now, now]

    ws.clear(); ws.append_row(list(df.columns)); ws.append_rows(df.fillna("").values.tolist())

def get_participants(order_id: str) -> list[dict]:
    df = _participants_df()
    if df.empty:
        return []
    res = df[df["order_id"].astype(str) == str(order_id)].copy()
    return res.fillna("").to_dict("records")

def set_participant_paid(order_id: str, username: str, paid: bool) -> bool:
    ws = get_worksheet("participants")
    df = _participants_df()
    if df.empty:
        return False
    uname = username.lstrip("@").strip().lower()
    mask = (df["order_id"].astype(str) == str(order_id)) & (df["username"].astype(str).str.lower() == uname)
    if not mask.any():
        return False
    df.loc[mask, ["paid","updated_at"]] = ["TRUE" if paid else "FALSE", _now_h()]
    ws.clear(); ws.append_row(list(df.columns)); ws.append_rows(df.fillna("").values.tolist())
    return True

def toggle_participant_paid(order_id: str, username: str) -> bool:
    df = _participants_df()
    if df.empty:
        return False
    uname = username.lstrip("@").strip().lower()
    mask = (df["order_id"].astype(str) == str(order_id)) & (df["username"].astype(str).str.lower() == uname)
    if not mask.any():
        return False
    cur = str(df.loc[mask, "paid"].iloc[0]).strip().lower() in {"true","1","yes","y"}
    return set_participant_paid(order_id, username, not cur)

def get_unpaid_usernames(order_id: str) -> list[str]:
    df = _participants_df()
    if df.empty:
        return []
    mask = (df["order_id"].astype(str) == str(order_id)) & ~(df["paid"].astype(str).str.strip().str.lower().isin(["true","1","yes","y"]))
    res = df[mask]["username"].astype(str).str.strip().tolist()
    return [u for u in res if u]

def find_orders_for_username(username: str) -> list[str]:
    """Заказы, где пользователь есть в participants."""
    if not username:
        return []
    uname = username.strip().lstrip("@").lower()
    if not uname:
        return []
    df = _participants_df()
    if df.empty:
        return []
    mask = df["username"].astype(str).str.lower() == uname
    return list({str(x) for x in df[mask]["order_id"].astype(str).tolist() if str(x).strip()})



# ===== Admin UI helpers (reply keyboard) =====
from telegram import ReplyKeyboardMarkup, KeyboardButton

ADMIN_MENU_BTNS = [
    [KeyboardButton("Отследить разбор"), KeyboardButton("Админ: Рассылка")],
    [KeyboardButton("Админ: Заказы"), KeyboardButton("Админ: Адреса")],
]

ADMIN_BACK_BTN = KeyboardButton("Назад, в админ-панель")

BROADCAST_MENU_BTNS = [
    [KeyboardButton("Уведомления всем должникам")],
    [KeyboardButton("Уведомления по ID разбора")],
    [ADMIN_BACK_BTN],
]


def is_admin_user(user_id:int) -> bool:
    try:
        from app.config import ADMIN_IDS  # if exists
    except Exception:
        try:
            from config import ADMIN_IDS
        except Exception:
            ADMIN_IDS = []
    return str(user_id) in {str(x) for x in ADMIN_IDS}

async def show_admin_menu(update, context):
    kb = ReplyKeyboardMarkup(ADMIN_MENU_BTNS, resize_keyboard=True, one_time_keyboard=False)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Админ-панель: выберите раздел",
        reply_markup=kb
    )

async def show_broadcast_menu(update, context):
    kb = ReplyKeyboardMarkup(BROADCAST_MENU_BTNS, resize_keyboard=True, one_time_keyboard=False)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Раздел «Рассылка»",
        reply_markup=kb
    )

# /admin toggles admin menu for admins only
async def cmd_admin(update, context):
    if not is_admin_user(update.effective_user.id):
        return
    await show_admin_menu(update, context)

# Text router for admin menus
async def on_admin_text(update, context):
    if not is_admin_user(update.effective_user.id):
        return
    text = (update.message.text or "").strip().lower()
    if text == "админ: рассылка":
        return await show_broadcast_menu(update, context)
    if text in ("назад, в админ-панель", "назад в админ-меню", "назад в админ-панель"):
        return await show_admin_menu(update, context)
    if text == "уведомления всем должникам":
        return await broadcast_all_unpaid(update, context)
    # "Уведомления по ID разбора" — запускает существующий сценарий вручную
    if text == "уведомления по id разбора":
        # Reuse your existing entry point (send a prompt to enter order_id)
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Введите номер разбора (order_id), например CN-00000")
        return

# Mass broadcast: all unpaid across all orders
async def broadcast_all_unpaid(update, context):
    from app import sheets  # or relative import
    try:
        grouped = sheets.get_all_unpaid_grouped()
    except Exception:
        try:
            import sheets
            grouped = sheets.get_all_unpaid_grouped()
        except Exception as e:
            await context.bot.send_message(update.effective_chat.id, f"Не удалось получить список должников: {e}")
            return
    total_orders = len(grouped)
    total_ok = 0
    total_fail = 0
    report_lines = []
    for order_id, users in grouped.items():
        unpaid_usernames = [u.lower() for u in users]
        user_ids = sheets.get_user_ids_by_usernames(unpaid_usernames)
    logging.info("[remind] mapped user_ids=%s", user_ids)
        ok = 0
        fail = 0
        for uid in user_ids:
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"Напоминание по доставке: у вас есть неоплаченный разбор {order_id}. Пожалуйста, оплатите."
                )
                ok += 1
            except Exception as e:
                fail += 1
        total_ok += ok
        total_fail += fail
        report_lines.append(f"{order_id}: ✅ {ok} ❌ {fail}")
    summary = "\\n".join(["📣 Уведомления всем должникам — итог",
                          f"Заказов: {total_orders}",
                          f"Успешно: {total_ok}",
                          f"Ошибок: {total_fail}",
                          "",
                          *report_lines])
    await context.bot.send_message(chat_id=update.effective_chat.id, text=summary)



def register_admin_ui(application):
    from telegram.ext import CommandHandler, MessageHandler, filters
    application.add_handler(CommandHandler("admin", cmd_admin))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_admin_text))


