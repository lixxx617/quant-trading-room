import streamlit as st
from supabase import create_client, Client


@st.cache_resource
def init_supabase() -> Client:
	url = st.secrets["SUPABASE_URL"]
	key = st.secrets["SUPABASE_KEY"]
	return create_client(url, key)


supabase = init_supabase()


def get_user_data(line_user_id: str) -> dict:
	"""取得特定 LINE 帳號的投資組合資料"""
	res = supabase.table("users").select("*").eq("line_user_id", line_user_id).execute()
	if res.data:
		return res.data[0]
	return None


def save_or_update_user(line_user_id: str, line_user_name: str, cash: float = 100000.0, holdings: dict = None):
	"""新增或更新使用者的個人資料與投資組合"""
	data = {"line_user_id": line_user_id, "line_user_name": line_user_name, "updated_at": "now()"}
	if holdings is not None:
		data["holdings"] = holdings
	if cash is not None:
		data["cash"] = cash

	supabase.table("users").upsert(data).execute()
