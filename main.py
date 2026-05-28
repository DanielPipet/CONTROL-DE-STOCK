import streamlit as st
import pandas as pd
from datetime import date, datetime
import uuid
import io
import extra_streamlit_components as stx
from supabase import create_client

# ─────────────────────────────────────────────
# CONFIGURACIÓN GENERAL
# ─────────────────────────────────────────────
USUARIO    = "admin"
CONTRASENA = "control2026"

st.set_page_config(page_title="Control de Stock", page_icon="📦", layout="wide")

def get_cookie_manager():
    return stx.CookieManager()

cookie_manager = get_cookie_manager()

# ─────────────────────────────────────────────
# CONEXIÓN CON SUPABASE
# ─────────────────────────────────────────────
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_supabase()

# ─────────────────────────────────────────────
# HELPERS — CATEGORÍAS
# ─────────────────────────────────────────────
def get_categorias():
    try:
        response = supabase.table("categorias").select("*").order("nombre").execute()
        if response.data:
            return pd.DataFrame(response.data)
    except:
        pass
    return pd.DataFrame(columns=["id", "nombre"])

def agregar_categoria(nombre):
    nombre_limpio = str(nombre).strip()
    if not nombre_limpio or nombre_limpio.lower() in ("none", "nan", ""):
        return
    try:
        check = supabase.table("categorias").select("id").eq("nombre", nombre_limpio).execute()
        if not check.data:
            supabase.table("categorias").insert({"nombre": nombre_limpio}).execute()
    except:
        pass

def eliminar_categoria(cat_id):
    try:
        supabase.table("categorias").delete().eq("id", cat_id).execute()
    except:
        pass

# ─────────────────────────────────────────────
# HELPERS — PRODUCTOS (BLINDADOS ANTI-NONE)
# ─────────────────────────────────────────────
def get_productos():
    try:
        response = supabase.table("productos").select("*").order("descripcion").execute()
        if response.data:
            return pd.DataFrame(response.data)
    except:
        pass
    return pd.DataFrame(columns=["cod_prod", "descripcion", "categoria", "valor_unitario", "cantidad"])

def get_producto(cod):
    cod_limpio = str(cod).strip()
    # Si viene un código basura, nulo o vacío, frenamos antes de consultar a Supabase
    if not cod_limpio or cod_limpio.lower() in ("nan", "none", ""):
        return None
    try:
        response = supabase.table("productos").select("*").eq("cod_prod", cod_limpio).execute()
        return response.data[0] if response.data else None
    except:
        return None

def get_producto_by_nombre(nombre):
    nombre_limpio = str(nombre).strip()
    # Evitamos que busque "None" en la base de datos
    if not nombre_limpio or nombre_limpio.lower() in ("nan", "none", ""):
        return None
    try:
        response = supabase.table("productos").select("*").ilike("descripcion", nombre_limpio).execute()
        return response.data[0] if response.data else None
    except:
        return None

def agregar_producto(cod, desc, cat, precio, cantidad):
    try:
        supabase.table("productos").insert({
            "cod_prod": str(cod).strip(),
            "descripcion": str(desc).strip(),
            "categoria": str(cat).strip(),
            "valor_unitario": float(precio),
            "cantidad": int(cantidad)
        }).execute()
    except:
        pass

def editar_producto(cod, desc, cat, precio, cantidad):
    try:
        supabase.table("productos").update({
            "descripcion": str(desc).strip(),
            "categoria": str(cat).strip(),
            "valor_unitario": float(precio),
            "cantidad": int(cantidad)
        }).eq("cod_prod", str(cod).strip()).execute()
    except:
        pass

def actualizar_stock(cod, delta):
    prod = get_producto(cod)
    if prod:
        nueva_cantidad = int(prod["cantidad"]) + int(delta)
        try:
            supabase.table("productos").update({"cantidad": nueva_cantidad}).eq("cod_prod", cod).execute()
        except:
            pass

def actualizar_precio(cod, nuevo_precio):
    try:
        supabase.table("productos").update({"valor_unitario": float(nuevo_precio)}).eq("cod_prod", cod).execute()
    except:
        pass

def upsert_producto(cod, desc, cat, precio, stock_delta):
    prod = get_producto(cod)
    if not prod:
        agregar_producto(cod, desc, cat, precio, stock_delta)
    else:
        nueva_cantidad = int(prod["cantidad"]) + int(stock_delta)
        try:
            supabase.table("productos").update({
                "descripcion": str(desc).strip(),
                "categoria": str(cat).strip(),
                "valor_unitario": float(precio),
                "cantidad": nueva_cantidad
            }).eq("cod_prod", cod).execute()
        except:
            pass

# ─────────────────────────────────────────────
# HELPERS — OPERACIONES
# ─────────────────────────────────────────────
def registrar_operacion(id_op, tipo, fecha, cod, desc, cat, cantidad, precio, subtotal, factura, obs):
    try:
        supabase.table("operaciones").insert({
            "id_operacion": id_op,
            "tipo": tipo,
            "fecha": str(fecha),
            "cod_prod": cod,
            "descripcion": desc,
            "categoria": cat,
            "cantidad": int(cantidad),
            "precio_unitario": float(precio),
            "subtotal": float(subtotal),
            "con_factura": 1 if factura else 0,
            "observaciones": obs
        }).execute()
    except:
        pass

def get_operaciones():
    try:
        response = supabase.table("operaciones").select("*").order("fecha", desc=True).execute()
        if response.data:
            return pd.DataFrame(response.data)
    except:
        pass
    return pd.DataFrame(columns=[
        "id", "id_operacion", "tipo", "fecha", "cod_prod", "descripcion", 
        "categoria", "cantidad", "precio_unitario", "subtotal", "con_factura", "observaciones"
    ])

# ─────────────────────────────────────────────
# CONTROLADOR DE PLANILLA (MÁXIMA DESINFECCIÓN)
# ─────────────────────────────────────────────
EMPTY_OP_ROW = {
    "COD_PROD": "", "PRODUCTO": "", "CANTIDAD": 1,
    "PRECIO_UNITARIO": 0.0, "TOTAL": 0.0, "OBSERVACIONES": ""
}

def new_op_df():
    return pd.DataFrame([EMPTY_OP_ROW.copy()])

def limpiar_valores_none(val):
    if pd.isna(val) or val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ("none", "nan", ""):
        return ""
    return s

def callback_planilla(suffix: str):
    state_key = f"op_editor_{suffix}"
    df_key = "op_df_ventas" if suffix == "ventas" else "op_df_compras"
    
    if state_key not in st.session_state:
        return

    edits = st.session_state[state_key]
    df = st.session_state[df_key].copy()

    # 1. Filas agregadas dinámicamente por el usuario
    for row in edits.get("added_rows", []):
        new_row = EMPTY_OP_ROW.copy()
        # Desinfectamos cualquier valor que el diccionario nuevo traiga por defecto
        cleaned_row = {k: (limpiar_valores_none(v) if k in ("COD_PROD", "PRODUCTO") else v) for k, v in row.items()}
        new_row.update(cleaned_row)
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    # 2. Ediciones sobre celdas existentes
    for idx_str, column_changes in edits.get("edited_rows", {}).items():
        idx = int(idx_str)
        
        # Guardamos los cambios desinfectando textos al vuelo
        for col, val in column_changes.items():
            if col in ("COD_PROD", "PRODUCTO"):
                df.at[idx, col] = limpiar_valores_none(val)
            else:
                df.at[idx, col] = val
        
        cod = limpiar_valores_none(df.at[idx, "COD_PROD"])
        nombre = limpiar_valores_none(df.at[idx, "PRODUCTO"])

        prod = None
        # Solo consultamos si las variables no quedaron vacías tras la desinfección
        if "COD_PROD" in column_changes and cod:
            prod = get_producto(cod)
        elif "PRODUCTO" in column_changes and nombre:
            prod = get_producto_by_nombre(nombre)

        if prod is not None:
            df.at[idx, "COD_PROD"] = str(prod["cod_prod"])
            df.at[idx, "PRODUCTO"] = str(prod["descripcion"])
            df.at[idx, "PRECIO_UNITARIO"] = float(prod["valor_unitario"])
        else:
            # Si borró el código o el producto, reseteamos los datos de esa fila
            if not cod and not nombre:
                df.at[idx, "COD_PROD"] = ""
                df.at[idx, "PRODUCTO"] = ""
                df.at[idx, "PRECIO_UNITARIO"] = 0.0

        try:
            cant = max(int(df.at[idx, "CANTIDAD"]), 1)
        except:
            cant = 1
        df.at[idx, "CANTIDAD"] = cant
        
        precio = float(df.at[idx, "PRECIO_UNITARIO"])
        df.at[idx, "TOTAL"] = round(cant * precio, 2)

    # 3. Procesamos eliminaciones
    deleted_indices = edits.get("deleted_rows", [])
    if deleted_indices:
        df = df.drop(deleted_indices).reset_index(drop=True)

    if df.empty:
        df = new_op_df()

    # Desinfección forzada absoluta final en toda la estructura del DataFrame
    df["COD_PROD"] = df["COD_PROD"].apply(limpiar_valores_none)
    df["PRODUCTO"] = df["PRODUCTO"].apply(limpiar_valores_none)

    st.session_state[df_key] = df

def build_op_col_config():
    prods = get_productos()
    opciones_prod = prods["descripcion"].dropna().unique().tolist() if not prods.empty else []
    return {
        "COD_PROD":        st.column_config.TextColumn("Código",            width="small"),
        "PRODUCTO":        st.column_config.SelectboxColumn("Producto",      options=opciones_prod, width="large"),
        "CANTIDAD":        st.column_config.NumberColumn("Cantidad",         min_value=1, step=1, width="small"),
        "PRECIO_UNITARIO": st.column_config.NumberColumn("Precio Unit. ($)", disabled=True, format="$%.2f", width="medium"),
        "TOTAL":           st.column_config.NumberColumn("Total ($)",        disabled=True, format="$%.2f", width="medium"),
        "OBSERVACIONES":   st.column_config.TextColumn("Observaciones",      width="medium"),
    }

# ─────────────────────────────────────────────
# INITIALIZE SESSION STATE
# ─────────────────────────────────────────────
defaults = {
    "logged_in":      False,
    "op_df_ventas":   new_op_df(),
    "op_df_compras":  new_op_df(),
    "inv_add_key":    0,
    "inv_edit_key":   0,
    "inv_cat_key":    0,
    "bulk_key":       0,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─────────────────────────────────────────────
# CONTROL DE SESIÓN
# ─────────────────────────────────────────────
user_cookie = cookie_manager.get(cookie="logged_in_user")

if user_cookie == "admin":
    st.session_state.logged_in = True

def pantalla_login():
    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown("## 📦 Sistema de Control de Stock")
        st.markdown("---")
        st.markdown("### Iniciar Sesión")
        usr  = st.text_input("Usuario",     placeholder="Ingrese su usuario")
        pwd  = st.text_input("Contraseña", type="password", placeholder="Ingrese su contraseña")
        if st.button("Ingresar", use_container_width=True):
            if usr == USUARIO and pwd == CONTRASENA:
                st.session_state.logged_in = True
                cookie_manager.set(cookie="logged_in_user", val="admin", expires_at=datetime.now() + pd.Timedelta(days=30))
                st.rerun()
            else:
                st.error("❌ Usuario o contraseña incorrectos.")

if not st.session_state.logged_in:
    pantalla_login()
    st.stop()

# ─────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📦 Control de Stock")
    st.markdown("---")
    seccion = st.radio("Menú", ["🛒 OPERACIONES", "📊 HISTORIAL", "📋 INVENTARIO"], label_visibility="collapsed")
    st.markdown("---")
    if st.button("🚪 Cerrar Sesión"):
        st.session_state.logged_in = False
        cookie_manager.delete(cookie="logged_in_user")
        st.rerun()

# ══════════════════════════════════════════════
# OPERACIONES
# ══════════════════════════════════════════════
if seccion == "🛒 OPERACIONES":
    st.title("🛒 Operaciones")
    tab_ventas, tab_compras = st.tabs(["🧾 Ventas", "📥 Compras"])
    
    def render_op_tab(tab_tipo: str):
        suffix   = "ventas" if tab_tipo == "Venta" else "compras"
        df_key   = f"op_df_{suffix}"

        col_fecha, col_fac = st.columns([2, 2])
        fecha = col_fecha.date_input("Fecha", value=date.today(), format="DD/MM/YYYY", key=f"fecha_{suffix}")
        col_fac.write(" ")
        con_factura = col_fac.checkbox("Con Factura ✔️", key=f"factura_{suffix}")

        st.markdown("---")
        st.caption("✏️ Seleccioná el **Producto** desde el menú desplegable o escribí el **Código** — los totales se calculan al instante.")

        # Desinfección preventiva agresiva antes de pintar la UI
        st.session_state[df_key]["COD_PROD"] = st.session_state[df_key]["COD_PROD"].apply(limpiar_valores_none)
        st.session_state[df_key]["PRODUCTO"] = st.session_state[df_key]["PRODUCTO"].apply(limpiar_valores_none)

        st.data_editor(
            st.session_state[df_key],
            column_config=build_op_col_config(),
            num_rows="dynamic",
            use_container_width=True,
            key=f"op_editor_{suffix}",
            on_change=callback_planilla,
            args=(suffix,)
        )

        current_df = st.session_state[df_key]
        valid_rows = current_df[(current_df["COD_PROD"] != "") & (current_df["PRODUCTO"] != "") & (current_df["TOTAL"] > 0)]
        total_general = valid_rows["TOTAL"].sum()

        col_tot, col_btn = st.columns([3, 1])
        total_formateado = f"${total_general:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        col_tot.markdown(f"## 💰 Total General: **{total_formateado}**")
        
        if col_btn.button("🗑️ Limpiar planilla", key=f"limpiar_{suffix}"):
            st.session_state[df_key] = new_op_df()
            st.rerun()

        st.markdown("---")

        if st.button(f"✅ Confirmar {tab_tipo}", use_container_width=True, key=f"confirmar_{suffix}"):
            filas = valid_rows.copy()
            if filas.empty:
                st.error("❌ No hay productos válidos cargados en la planilla.")
                return

            errores = []
            if tab_tipo == "Venta":
                for _, row in filas.iterrows():
                    prod = get_producto(str(row["COD_PROD"]))
                    if prod is None:
                        errores.append(f"Código '{row['COD_PROD']}' no existe en el inventario.")
                    elif int(row["CANTIDAD"]) > int(prod["cantidad"]):
                        errores.append(f"'{row['PRODUCTO']}': Stock insuficiente (Disponible: {int(prod['cantidad'])}, Requerido: {int(row['CANTIDAD'])}).")
            
            if errores:
                for e in errores:
                    st.error(f"❌ {e}")
                return

            id_op = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:4].upper()
            
            for _, row in filas.iterrows():
                cod   = str(row["COD_PROD"])
                desc  = str(row["PRODUCTO"])
                cant  = int(row["CANTIDAD"])
                prec  = float(row["PRECIO_UNITARIO"])
                tot   = float(row["TOTAL"])
                obs   = str(row["OBSERVACIONES"]) if str(row["OBSERVACIONES"]).strip().lower() not in ("nan","none","") else ""
                
                prod  = get_producto(cod)
                cat   = str(prod["categoria"]) if prod is not None else ""

                delta = -cant if tab_tipo == "Venta" else cant
                actualizar_stock(cod, delta)
                if tab_tipo == "Compra":
                    actualizar_precio(cod, prec)

                registrar_operacion(id_op, tab_tipo, fecha, cod, desc, cat, cant, prec, tot, con_factura, obs)

            total_msg = f"${total_general:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            st.success(f"✅ {tab_tipo} confirmada con éxito. Nro Ticket: **{id_op}** | Total: **{total_msg}**")
            st.session_state[df_key] = new_op_df()
            st.rerun()

    with tab_ventas:
        render_op_tab("Venta")
    with tab_compras:
        render_op_tab("Compra")

# ─────────────────────────────────────────────
# HISTORIAL
# ─────────────────────────────────────────────
elif seccion == "📊 HISTORIAL":
    st.title("📊 Historial de Operaciones")
    operaciones_df = get_operaciones()

    if operaciones_df.empty:
        st.info("No hay operaciones registradas aún.")
        st.stop()

    st.markdown("#### 🔍 Filtros")
    f1, f2, f3 = st.columns(3)
    filto_tipo    = f1.selectbox("Tipo",    ["Todos", "Venta", "Compra"])
    filtro_factura = f2.selectbox("Factura", ["Todos", "Con Factura", "Sin Factura"])
    cats_ops       = ["Todos"] + sorted(operaciones_df["categoria"].dropna().unique().tolist())
    filtro_cat     = f3.selectbox("Categoría", cats_ops)

    fd1, fd2, fd3 = st.columns(3)
    df_f = operaciones_df.copy()
    df_f["_fecha_dt"] = pd.to_datetime(df_f["fecha"], errors="coerce").dt.date
    
    fecha_min = df_f["_fecha_dt"].min() if pd.notna(df_f["_fecha_dt"].min()) else date.today()
    fecha_max = df_f["_fecha_dt"].max() if pd.notna(df_f["_fecha_dt"].max()) else date.today()
    
    filtro_desde  = fd1.date_input("Desde", value=fecha_min, format="DD/MM/YYYY", key="hist_desde")
    filtro_hasta  = fd2.date_input("Hasta", value=fecha_max, format="DD/MM/YYYY", key="hist_hasta")
    filtro_texto  = fd3.text_input("Buscar código o descripción")
    st.markdown("---")

    if filto_tipo    != "Todos":         df_f = df_f[df_f["tipo"] == filto_tipo]
    if filtro_factura == "Con Factura":   df_f = df_f[df_f["con_factura"] == 1]
    elif filtro_factura == "Sin Factura": df_f = df_f[df_f["con_factura"] == 0]
    if filtro_cat     != "Todos":         df_f = df_f[df_f["categoria"]   == filtro_cat]
    
    df_f = df_f[(df_f["_fecha_dt"] >= filtro_desde) & (df_f["_fecha_dt"] <= filtro_hasta)]
    if filtro_texto:
        mask = (df_f["cod_prod"].str.contains(filtro_texto, case=False, na=False) |
                df_f["descripcion"].str.contains(filtro_texto, case=False, na=False))
        df_f = df_f[mask]
        
    df_f = df_f.drop(columns=["_fecha_dt"], errors="ignore")

    total_v = df_f[df_f['tipo']=='Venta']['subtotal'].sum()
    total_c = df_f[df_f['tipo']=='Compra']['subtotal'].sum()
    v_formateado = f"${total_v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    c_formateado = f"${total_c:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Operaciones",       len(df_f))
    m2.metric("Total Ventas ($)",  v_formateado)
    m3.metric("Total Compras ($)", c_formateado)
    m4.metric("Con Factura",       len(df_f[df_f["con_factura"] == 1]))
    st.markdown("---")

    if df_f.empty:
        st.warning("No se encontraron operaciones con los filtros aplicados.")
    else:
        base_cols = ["tipo", "fecha", "cod_prod", "descripcion", "categoria",
                     "cantidad", "precio_unitario", "subtotal", "con_factura", "observaciones"]
        if "id_operacion" in df_f.columns:
            base_cols = ["id_operacion"] + base_cols
        disp = df_f[[c for c in base_cols if c in df_f.columns]].copy()
        disp["con_factura"] = disp["con_factura"].map({1: "Sí", 0: "No"})
        disp = disp.rename(columns={
            "id_operacion": "Nro. Operación", "tipo": "Tipo", "fecha": "Fecha",
            "cod_prod": "Código", "descripcion": "Descripción", "categoria": "Categoría",
            "cantidad": "Cantidad", "precio_unitario": "Precio Unitario",
            "subtotal": "Subtotal", "con_factura": "Factura", "observaciones": "Observaciones"
        })
        
        st.dataframe(
            disp, 
            use_container_width=True, 
            hide_index=True,
            column_config={
                "Precio Unitario": st.column_config.NumberColumn(format="$%.2f"),
                "Subtotal": st.column_config.NumberColumn(format="$%.2f")
            }
        )
        
        buf_hist = io.BytesIO()
        with pd.ExcelWriter(buf_hist, engine="openpyxl") as writer:
            disp.to_excel(writer, index=False, sheet_name="Historial")
            workbook  = writer.book
            worksheet = writer.sheets["Historial"]
            formato_moneda = "$#,##0.00;($#,##0.00);\"-\";@" 
            for row in range(2, len(disp) + 2):
                worksheet[f"I{row}"].number_format = formato_moneda
                worksheet[f"J{row}"].number_format = formato_moneda
            
        st.download_button(
            "📥 Exportar historial a Excel",
            data=buf_hist.getvalue(),
            file_name="historial_operaciones.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

# ─────────────────────────────────────────────
# INVENTARIO
# ─────────────────────────────────────────────
elif seccion == "📋 INVENTARIO":
    st.title("📋 Base de Datos de Productos")

    productos_df  = get_productos()
    categorias_df = get_categorias()
    cat_nombres   = categorias_df["nombre"].tolist() if not categorias_df.empty else []

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Productos",   len(productos_df))
    c2.metric("Unidades en Stock", int(productos_df["cantidad"].sum()) if not productos_df.empty else 0)
    c3.metric("Categorías",        len(categorias_df))
    st.markdown("---")

    tab_ver, tab_add, tab_edit, tab_cats = st.tabs(
        ["📄 Ver Inventario", "➕ Agregar Producto", "✏️ Editar Producto", "🏷️ Categorías"]
    )

    with tab_ver:
        if productos_df.empty:
            st.info("No hay productos registrados todavía.")
        else:
            disp_inv = productos_df.rename(columns={
                "cod_prod": "Código", "descripcion": "Descripción",
                "categoria": "Categoría", "valor_unitario": "Precio Unitario", "cantidad": "Stock"
            })
            column_order = ["Código", "Descripción", "Categoría", "Precio Unitario", "Stock"]
            disp_inv = disp_inv[[c for c in column_order if c in disp_inv.columns]]
            
            st.dataframe(
                disp_inv, 
                use_container_width=True, 
                hide_index=True,
                column_config={
                    "Precio Unitario": st.column_config.NumberColumn(format="$%.2f")
                }
            )

            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                disp_inv.to_excel(writer, index=False, sheet_name="Inventario")
                workbook = writer.book
                worksheet = writer.sheets["Inventario"]
                formato_moneda = "$#,##0.00;($#,##0.00);\"-\";@"
                for row in range(2, len(disp_inv) + 2):
                    worksheet[f"D{row}"].number_format = formato_moneda
                    
            st.download_button(
                "📥 Exportar inventario a Excel",
                data=buf.getvalue(),
                file_name="inventario.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    with tab_add:
        ak = st.session_state.inv_add_key
        with st.form(f"form_agregar_{ak}"):
            st.subheader("Nuevo Producto")
            col_a, col_b = st.columns(2)
            new_cod  = col_a.text_input("Código de Producto *", key=f"new_cod_{ak}")
            new_desc = col_b.text_input("Descripción *",       key=f"new_desc_{ak}")
            col_c, col_d, col_e = st.columns(3)
            
            if cat_nombres:
                new_cat = col_c.selectbox("Categoría", cat_nombres, key=f"new_cat_{ak}")
            else:
                new_cat = col_c.text_input("Categoría (Crea una en la pestaña 🏷️)", disabled=True, key=f"new_cat_{ak}")
                
            new_precio = col_d.number_input("Precio Unitario ($)", min_value=0.0, step=0.01, key=f"new_precio_{ak}")
            new_stock  = col_e.number_input("Stock Inicial",       min_value=0,   step=1,    key=f"new_stock_{ak}")
            
            if st.form_submit_button("✅ Agregar Producto", use_container_width=True):
                if not new_cod or not new_desc:
                    st.error("El código y la descripción son obligatorios.")
                elif not new_cat:
                    st.error("Debes registrar y seleccionar una categoría primero.")
                elif get_producto(new_cod) is not None:
                    st.error(f"Ya existe un producto con el código '{new_cod}'.")
                else:
                    agregar_producto(new_cod, new_desc, new_cat, new_precio, new_stock)
                    st.success(f"✅ Producto '{new_desc}' agregado correctamente.")
                    st.session_state.inv_add_key += 1
                    st.rerun()

    with tab_edit:
        ek = st.session_state.inv_edit_key
        productos_df2 = get_productos()
        if productos_df2.empty:
            st.info("No hay productos para editar.")
        else:
            PLACEHOLDER = "— Seleccionar un producto —"
            opciones  = [PLACEHOLDER] + [f"{r['cod_prod']} — {r['descripcion']}" for _, r in productos_df2.iterrows()]
            seleccion = st.selectbox("Seleccionar producto a editar", opciones, key=f"edit_sel_{ek}")

            if seleccion == PLACEHOLDER:
                st.info("Seleccioná un producto de la lista para ver y editar sus datos.")
            else:
                cod_sel = seleccion.split(" — ")[0]
                prod    = get_producto(cod_sel)
                if prod is not None:
                    with st.form(f"form_editar_{ek}_{cod_sel}"):
                        st.subheader(f"Editando: {prod['descripcion']}")
                        col_a, col_b = st.columns(2)
                        e_desc = col_a.text_input("Descripción", value=prod["descripcion"], key=f"e_desc_{ek}_{cod_sel}")
                        
                        current_cat = str(prod["categoria"] or "")
                        if cat_nombres:
                            idx   = cat_nombres.index(current_cat) if current_cat in cat_nombres else 0
                            e_cat = col_b.selectbox("Categoría", cat_nombres, index=idx, key=f"e_cat_{ek}_{cod_sel}")
                        else:
                            e_cat = col_b.text_input("Categoría", value=current_cat, disabled=True, key=f"e_cat_{ek}_{cod_sel}")
                            
                        col_c, col_d = st.columns(2)
                        e_precio = col_c.number_input("Precio Unitario ($)", value=float(prod["valor_unitario"]), min_value=0.0, step=0.01, key=f"e_precio_{ek}_{cod_sel}")
                        e_stock  = col_d.number_input("Stock", value=int(prod["cantidad"]), min_value=0, step=1, key=f"e_stock_{ek}_{cod_sel}")
                        
                        if st.form_submit_button("💾 Guardar Cambios", use_container_width=True):
                            editar_producto(cod_sel, e_desc, e_cat, e_precio, e_stock)
                            st.success("✅ Producto actualizado correctamente.")
                            st.session_state.inv_edit_key += 1
                            st.rerun()

    with tab_cats:
        ck = st.session_state.inv_cat_key
        st.subheader("🏷️ Gestión de Categorías")
        col_form, col_tabla = st.columns([1, 2])

        with col_form:
            with st.form(f"form_cat_{ck}"):
                nueva_cat = st.text_input("Nueva Categoría *", key=f"nueva_cat_{ck}")
                if st.form_submit_button("➕ Agregar", use_container_width=True):
                    if not nueva_cat.strip():
                        st.error("El nombre no puede estar vacío.")
                    else:
                        agregar_categoria(nueva_cat)
                        st.success(f"✅ '{nueva_cat}' agregada.")
                        st.session_state.inv_cat_key += 1
                        st.rerun()

        with col_tabla:
            cats_df = get_categorias()
            if cats_df.empty:
                st.info("No hay categorías registradas.")
            else:
                st.dataframe(
                    cats_df[["nombre"]].rename(columns={"nombre": "Categoría"}),
                    use_container_width=True, hide_index=True
                )
                cat_del = st.selectbox("Eliminar", cats_df["nombre"].tolist(), key=f"del_cat_sel_{ck}")
                if st.button("🗑️ Eliminar categoría seleccionada", key=f"del_cat_btn_{ck}"):
                    row = cats_df[cats_df["nombre"] == cat_del]
                    if not row.empty:
                        eliminar_categoria(int(row.iloc[0]["id"]))
                        st.success(f"Categoría '{cat_del}' eliminada.")
                        st.session_state.inv_cat_key += 1
                        st.rerun()

    st.markdown("---")
    with st.expander("📦 Carga Masiva desde Excel o CSV"):
        st.markdown(
            "El archivo debe contener las columnas: **COD_PROD, PRODUCTO, CATEGORIA, PRECIO, STOCK**\n\n"
            "- Si el código ya existe → actualiza precio/categoría y **suma** el stock.\n"
            "- Si la categoría no existe → se crea automáticamente."
        )

        if st.session_state.get("bulk_result"):
            res = st.session_state.pop("bulk_result")
            if res.get("errors"):
                for e in res["errors"]:
                    st.warning(f"⚠️ {e}")
            st.success(f"✅ Carga finalizada — {res['inserted']} nuevo(s), {res['updated']} actualizado(s).")

        bk = st.session_state.bulk_key
        archivo = st.file_uploader("Seleccionar archivo", type=["xlsx", "csv"], key=f"bulk_upload_{bk}")
        if archivo is not None:
            try:
                df_bulk = (pd.read_csv(archivo, dtype=str) if archivo.name.endswith(".csv") else pd.read_excel(archivo, dtype=str))
                required = {"COD_PROD", "PRODUCTO", "CATEGORIA", "PRECIO", "STOCK"}
                missing  = required - set(df_bulk.columns)
                
                if missing:
                    st.error(f"❌ Faltan columnas: {', '.join(missing)}")
                else:
                    df_bulk = df_bulk.fillna("")
                    inserted, updated, errors = 0, 0, []

                    for _, row in df_bulk.iterrows():
                        try:
                            cod    = str(row["COD_PROD"]).strip()
                            desc   = str(row["PRODUCTO"]).strip()
                            cat    = str(row["CATEGORIA"]).strip()
                            precio = float(str(row["PRECIO"]).replace(",", "."))
                            stock  = int(float(str(row["STOCK"]).replace(",", ".")))

                            if not cod or not desc:
                                errors.append("Fila omitida: código o descripción vacíos.")
                                continue

                            if cat:
                                agregar_categoria(cat)

                            existia = get_producto(cod) is not None
                            upsert_producto(cod, desc, cat, precio, stock)
                            updated  += 1 if existia else 0
                            inserted += 0 if existia else 1
                        except Exception as ex:
                            errors.append(f"COD_PROD='{row.get('COD_PROD','?')}': {ex}")

                    st.session_state["bulk_result"] = {
                        "inserted": inserted,
                        "updated":  updated,
                        "errors":   errors,
                    }
                    st.session_state.bulk_key += 1
                    st.rerun()
            except Exception as ex:
                st.error(f"❌ Error al procesar el archivo: {ex}")

# link temporal para limpiar la base
if st.sidebar.button("🚨 LIMPIAR BASE DE DATOS (PRODUCCIÓN)"):
    from supabase import create_client
    s = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    s.table("operaciones").delete().neq("id", -1).execute()
    s.table("productos").delete().neq("cantidad", -999999).execute()
    s.table("categorias").delete().neq("id", -1).execute()
    st.sidebar.success("¡Base de datos limpia!")
    st.balloons()
