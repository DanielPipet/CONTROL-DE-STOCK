import streamlit as st
import sqlite3
import pandas as pd
from datetime import date, datetime
import uuid
import io

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
DB_PATH    = "inventario.db"
USUARIO    = "admin"
CONTRASENA = "control2026"

st.set_page_config(page_title="Control de Stock", page_icon="📦", layout="wide")

# ─────────────────────────────────────────────
# BASE DE DATOS
# ─────────────────────────────────────────────
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS categorias (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT UNIQUE NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS productos (
                cod_prod       TEXT PRIMARY KEY,
                descripcion    TEXT NOT NULL,
                categoria      TEXT,
                valor_unitario REAL DEFAULT 0,
                cantidad       INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS operaciones (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                id_operacion    TEXT,
                tipo            TEXT NOT NULL,
                fecha           TEXT NOT NULL,
                cod_prod        TEXT NOT NULL,
                descripcion     TEXT NOT NULL,
                categoria       TEXT,
                cantidad        INTEGER NOT NULL,
                precio_unitario REAL NOT NULL,
                subtotal        REAL NOT NULL,
                con_factura     INTEGER NOT NULL DEFAULT 0,
                observaciones   TEXT
            )
        """)
        # Migración segura
        try:
            conn.execute("ALTER TABLE operaciones ADD COLUMN id_operacion TEXT")
        except Exception:
            pass
        try:
            rows = conn.execute(
                "SELECT DISTINCT categoria FROM productos WHERE categoria IS NOT NULL AND TRIM(categoria) != ''"
            ).fetchall()
            for (cat,) in rows:
                conn.execute("INSERT OR IGNORE INTO categorias (nombre) VALUES (?)", (cat.strip(),))
        except Exception:
            pass
        conn.commit()

init_db()

# ─────────────────────────────────────────────
# HELPERS — CATEGORÍAS
# ─────────────────────────────────────────────
def get_categorias():
    with get_conn() as conn:
        return pd.read_sql("SELECT * FROM categorias ORDER BY nombre", conn)

def agregar_categoria(nombre):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO categorias (nombre) VALUES (?)", (nombre.strip(),))
        conn.commit()

def eliminar_categoria(cat_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM categorias WHERE id = ?", (cat_id,))
        conn.commit()

# ─────────────────────────────────────────────
# HELPERS — PRODUCTOS
# ─────────────────────────────────────────────
def get_productos():
    with get_conn() as conn:
        return pd.read_sql("SELECT * FROM productos ORDER BY descripcion", conn)

def get_producto(cod):
    cod = str(cod).strip()
    if not cod or cod in ("nan", "None", ""):
        return None
    with get_conn() as conn:
        df = pd.read_sql("SELECT * FROM productos WHERE cod_prod = ?", conn, params=(cod,))
        return df.iloc[0] if not df.empty else None

def get_producto_by_nombre(nombre):
    nombre = str(nombre).strip()
    if not nombre or nombre in ("nan", "None", ""):
        return None
    with get_conn() as conn:
        df = pd.read_sql(
            "SELECT * FROM productos WHERE LOWER(descripcion) = LOWER(?)",
            conn, params=(nombre,)
        )
        return df.iloc[0] if not df.empty else None

def agregar_producto(cod, desc, cat, precio, cantidad):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO productos (cod_prod, descripcion, categoria, valor_unitario, cantidad) VALUES (?,?,?,?,?)",
            (cod, desc, cat, precio, cantidad)
        )
        conn.commit()

def editar_producto(cod, desc, cat, precio, cantidad):
    with get_conn() as conn:
        conn.execute(
            "UPDATE productos SET descripcion=?, categoria=?, valor_unitario=?, cantidad=? WHERE cod_prod=?",
            (desc, cat, precio, cantidad, cod)
        )
        conn.commit()

def actualizar_stock(cod, delta):
    with get_conn() as conn:
        conn.execute("UPDATE productos SET cantidad = cantidad + ? WHERE cod_prod = ?", (delta, cod))
        conn.commit()

def actualizar_precio(cod, nuevo_precio):
    with get_conn() as conn:
        conn.execute("UPDATE productos SET valor_unitario = ? WHERE cod_prod = ?", (nuevo_precio, cod))
        conn.commit()

def upsert_producto(cod, desc, cat, precio, stock_delta):
    with get_conn() as conn:
        existing = pd.read_sql("SELECT 1 FROM productos WHERE cod_prod = ?", conn, params=(cod,))
        if existing.empty:
            conn.execute(
                "INSERT INTO productos (cod_prod, descripcion, categoria, valor_unitario, cantidad) VALUES (?,?,?,?,?)",
                (cod, desc, cat, precio, stock_delta)
            )
        else:
            conn.execute(
                "UPDATE productos SET descripcion=?, categoria=?, valor_unitario=?, cantidad=cantidad+? WHERE cod_prod=?",
                (desc, cat, precio, stock_delta, cod)
            )
        conn.commit()

# ─────────────────────────────────────────────
# HELPERS — OPERACIONES
# ─────────────────────────────────────────────
def registrar_operacion(id_op, tipo, fecha, cod, desc, cat, cantidad, precio, subtotal, factura, obs):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO operaciones
               (id_operacion,tipo,fecha,cod_prod,descripcion,categoria,
                cantidad,precio_unitario,subtotal,con_factura,observaciones)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (id_op, tipo, str(fecha), cod, desc, cat,
             cantidad, precio, subtotal, 1 if factura else 0, obs)
        )
        conn.commit()

def get_operaciones():
    with get_conn() as conn:
        return pd.read_sql("SELECT * FROM operaciones ORDER BY fecha DESC, id DESC", conn)

# ─────────────────────────────────────────────
# CONTROLADOR DE PLANILLA (DATA EDITOR OPTIMIZADO)
# ─────────────────────────────────────────────
EMPTY_OP_ROW = {
    "COD_PROD": "", "PRODUCTO": None, "CANTIDAD": 1,
    "PRECIO_UNITARIO": 0.0, "TOTAL": 0.0, "OBSERVACIONES": ""
}

def new_op_df():
    return pd.DataFrame([EMPTY_OP_ROW.copy()])

def callback_planilla(suffix: str):
    """Maneja cambios en la planilla de forma nativa sin generar bucles infinitos."""
    state_key = f"op_editor_{suffix}"
    df_key = "op_df_ventas" if suffix == "ventas" else "op_df_compras"
    
    if state_key not in st.session_state:
        return

    edits = st.session_state[state_key]
    df = st.session_state[df_key].copy()

    # 1. Filas Agregadas
    for row in edits.get("added_rows", []):
        new_row = EMPTY_OP_ROW.copy()
        new_row.update(row)
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    # 2. Filas Editadas
    for idx_str, column_changes in edits.get("edited_rows", {}).items():
        idx = int(idx_str)
        for col, val in column_changes.items():
            df.at[idx, col] = val
        
        # Lógica de Autocompletado reactivo por Código o Nombre
        cod = str(df.at[idx, "COD_PROD"]).strip()
        nombre = str(df.at[idx, "PRODUCTO"]).strip() if df.at[idx, "PRODUCTO"] else ""

        prod = None
        if "COD_PROD" in column_changes and cod:
            prod = get_producto(cod)
        elif "PRODUCTO" in column_changes and nombre:
            prod = get_producto_by_nombre(nombre)

        if prod is not None:
            df.at[idx, "COD_PROD"] = str(prod["cod_prod"])
            df.at[idx, "PRODUCTO"] = str(prod["descripcion"])
            df.at[idx, "PRECIO_UNITARIO"] = float(prod["valor_unitario"])

        # Recálculo de Subtotales
        cant = df.at[idx, "CANTIDAD"]
        try:
            cant = max(int(cant), 1)
        except:
            cant = 1
        df.at[idx, "CANTIDAD"] = cant
        precio = float(df.at[idx, "PRECIO_UNITARIO"])
        df.at[idx, "TOTAL"] = round(cant * precio, 2)

    # 3. Filas Eliminadas
    deleted_indices = edits.get("deleted_rows", [])
    if deleted_indices:
        df = df.drop(deleted_indices).reset_index(drop=True)

    if df.empty:
        df = new_op_df()

    st.session_state[df_key] = df

def build_op_col_config():
    prods = get_productos()
    opciones_prod = prods["descripcion"].dropna().unique().tolist() if not prods.empty else []
    return {
        "COD_PROD":        st.column_config.TextColumn("Código",            width="small"),
        "PRODUCTO":        st.column_config.SelectboxColumn("Producto",      options=opciones_prod, width="large"),
        "CANTIDAD":        st.column_config.NumberColumn("Cantidad",         min_value=1, step=1, width="small"),
        "PRECIO_UNITARIO": st.column_config.NumberColumn("Precio Unit. ($)", disabled=True, format="%.2f", width="medium"),
        "TOTAL":           st.column_config.NumberColumn("Total ($)",        disabled=True, format="%.2f", width="medium"),
        "OBSERVACIONES":   st.column_config.TextColumn("Observaciones",      width="medium"),
    }

# ─────────────────────────────────────────────
# SESSION STATE INITIALIZATION
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
# LOGIN
# ─────────────────────────────────────────────
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
    seccion = st.radio(
        "Menú",
        ["🛒 OPERACIONES", "📊 HISTORIAL", "📋 INVENTARIO"],
        label_visibility="collapsed"
    )
    st.markdown("---")
    if st.button("🚪 Cerrar Sesión"):
        st.session_state.logged_in = False
        st.rerun()

# ══════════════════════════════════════════════
# OPERACIONES
# ══════════════════════════════════════════════
def render_op_tab(tab_tipo: str):
    suffix   = "ventas" if tab_tipo == "Venta" else "compras"
    df_key   = f"op_df_{suffix}"

    col_fecha, col_fac = st.columns([2, 2])
    fecha = col_fecha.date_input("Fecha", value=date.today(), format="DD/MM/YYYY", key=f"fecha_{suffix}")
    col_fac.write(" ")
    con_factura = col_fac.checkbox("Con Factura ✔️", key=f"factura_{suffix}")

    st.markdown("---")
    st.caption("✏️ Seleccioná el **Producto** desde el menú desplegable o escribí el **Código** — los totales se calculan al instante.")

    # El editor ahora actualiza de manera segura mediante on_change sin romper hilos
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
    valid_rows = current_df[(current_df["COD_PROD"] != "") & (current_df["TOTAL"] > 0)]
    total_general = valid_rows["TOTAL"].sum()

    col_tot, col_btn = st.columns([3, 1])
    col_tot.markdown(f"## 💰 Total General: **${total_general:,.2f}**")
    
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

        # ID de Operación unificado para toda la orden de compra/venta
        id_op = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:4].upper()
        
        for _, row in filas.iterrows():
            cod   = str(row["COD_PROD"])
            desc  = str(row["PRODUCTO"])
            cant  = int(row["CANTIDAD"])
            prec  = float(row["PRECIO_UNITARIO"])
            tot   = float(row["TOTAL"])
            obs   = str(row["OBSERVACIONES"]) if str(row["OBSERVACIONES"]) not in ("nan","None","") else ""
            
            prod  = get_producto(cod)
            cat   = str(prod["categoria"]) if prod is not None else ""

            delta = -cant if tab_tipo == "Venta" else cant
            actualizar_stock(cod, delta)
            if tab_tipo == "Compra":
                actualizar_precio(cod, prec)

            registrar_operacion(id_op, tab_tipo, fecha, cod, desc, cat, cant, prec, tot, con_factura, obs)

        st.success(f"✅ {tab_tipo} confirmada con éxito. Nro Ticket: **{id_op}** | Total: **${total_general:,.2f}**")
        st.session_state[df_key] = new_op_df()
        st.rerun()

if seccion == "🛒 OPERACIONES":
    st.title("🛒 Operaciones")
    tab_ventas, tab_compras = st.tabs(["🧾 Ventas", "📥 Compras"])
    with tab_ventas:
        render_op_tab("Venta")
    with tab_compras:
        render_op_tab("Compra")

# ══════════════════════════════════════════════
# HISTORIAL
# ══════════════════════════════════════════════
elif seccion == "📊 HISTORIAL":
    st.title("📊 Historial de Operaciones")
    operaciones_df = get_operaciones()

    if operaciones_df.empty:
        st.info("No hay operaciones registradas aún.")
        st.stop()

    st.markdown("#### 🔍 Filtros")
    f1, f2, f3 = st.columns(3)
    filtro_tipo    = f1.selectbox("Tipo",    ["Todos", "Venta", "Compra"])
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

    if filtro_tipo    != "Todos":         df_f = df_f[df_f["tipo"] == filtro_tipo]
    if filtro_factura == "Con Factura":   df_f = df_f[df_f["con_factura"] == 1]
    elif filtro_factura == "Sin Factura": df_f = df_f[df_f["con_factura"] == 0]
    if filtro_cat     != "Todos":         df_f = df_f[df_f["categoria"]   == filtro_cat]
    
    df_f = df_f[(df_f["_fecha_dt"] >= filtro_desde) & (df_f["_fecha_dt"] <= filtro_hasta)]
    if filtro_texto:
        mask = (df_f["cod_prod"].str.contains(filtro_texto, case=False, na=False) |
                df_f["descripcion"].str.contains(filtro_texto, case=False, na=False))
        df_f = df_f[mask]
        
    df_f = df_f.drop(columns=["_fecha_dt"], errors="ignore")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Operaciones",       len(df_f))
    m2.metric("Total Ventas ($)",  f"{df_f[df_f['tipo']=='Venta']['subtotal'].sum():,.2f}")
    m3.metric("Total Compras ($)", f"{df_f[df_f['tipo']=='Compra']['subtotal'].sum():,.2f}")
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
        st.dataframe(disp, use_container_width=True, hide_index=True)
        csv = disp.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Exportar a CSV", csv, "historial_operaciones.csv", "text/csv")

# ══════════════════════════════════════════════
# INVENTARIO
# ══════════════════════════════════════════════
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

    # ── Ver ──
    with tab_ver:
        if productos_df.empty:
            st.info("No hay productos registrados todavía.")
        else:
            disp_inv = productos_df.rename(columns={
                "cod_prod": "Código", "descripcion": "Descripción",
                "categoria": "Categoría", "valor_unitario": "Precio Unitario", "cantidad": "Stock"
            })
            st.dataframe(disp_inv, use_container_width=True, hide_index=True)

            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                disp_inv.to_excel(writer, index=False, sheet_name="Inventario")
            st.download_button(
                "📥 Exportar inventario a Excel",
                data=buf.getvalue(),
                file_name="inventario.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    # ── Agregar (Con selector maestro de categorías) ──
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

    # ── Editar (Con selector maestro de categorías) ──
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

    # ── Categorías ──
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

    # ── Carga Masiva ──
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