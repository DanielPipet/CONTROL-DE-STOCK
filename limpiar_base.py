import streamlit as st
from supabase import create_client

# Inicializar conexión usando tus secretos actuales
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try:
    supabase = init_supabase()
    
    st.write("🗑️ Iniciando limpieza de la base de datos...")

    # 1. Limpiar Historial de Operaciones
    res_ops = supabase.table("operaciones").delete().neq("id", -1).execute()
    st.success("✅ Historial de operaciones vaciado.")

    # 2. Limpiar Base de Productos
    res_prod = supabase.table("productos").delete().neq("cantidad", -999999).execute()
    st.success("✅ Base de productos vaciada.")

    # 3. Limpiar Categorías
    res_cats = supabase.table("categorias").delete().neq("id", -1).execute()
    st.success("✅ Categorías vaciadas.")

    st.balloons()
    st.markdown("### 🎉 ¡Base de datos impecable y lista para usar desde cero!")

except Exception as e:
    st.error(f"❌ Ocurrió un error al limpiar: {e}")
