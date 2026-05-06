import streamlit as st
import pandas as pd
from datetime import datetime, timezone, timedelta
from supabase import create_client

# 1. Configuración de Conexión y Página
st.set_page_config(layout="wide", page_title="Cronoer - Consola de Control")
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase = create_client(url, key)

# 2. Funciones de Lógica de Tiempo (Estricto Backyard)
def calcular_seguimiento_carrera(hora_cero_db):
    inicio_carrera = datetime.fromisoformat(hora_cero_db)
    ahora = datetime.now(timezone.utc)
    tiempo_transcurrido = ahora - inicio_carrera
    segundos_totales = tiempo_transcurrido.total_seconds()
    
    if segundos_totales < 0:
        return 0, "00:00", 0, "ESPERANDO LARGADA"
    
    patio_actual = int(segundos_totales // 3600) + 1
    segundos_en_este_patio = segundos_totales % 3600
    segundos_restantes = 3600 - segundos_en_este_patio
    
    minutos = int(segundos_restantes // 60)
    segundos = int(segundos_restantes % 60)
    tiempo_fmt = f"{minutos:02d}:{segundos:02d}"
    
    # Lógica de llamados de corral (3', 2', 1')
    alerta = "EN CURSO"
    if 120 < segundos_restantes <= 180: alerta = "🚨 ¡3 MINUTOS! (1° LLAMADO)"
    elif 60 < segundos_restantes <= 120: alerta = "🚨 ¡2 MINUTOS! (2° LLAMADO)"
    elif 0 < segundos_restantes <= 60: alerta = "⚠️ ¡1 MINUTO! (ÚLTIMO LLAMADO)"
    
    return patio_actual, tiempo_fmt, segundos_restantes, alerta

# 3. Funciones de Base de Datos
def registrar_suceso(id_evento, dorsal, nro_vuelta, estado="ACT"):
    ahora = datetime.now(timezone.utc).isoformat()
    nuevo_registro = {
        "id_evento": id_evento, "dorsal": dorsal, 
        "nro_vuelta": nro_vuelta, "hora_llegada": ahora, "estado": estado
    }
    try:
        supabase.table("vueltas_vivo").insert(nuevo_registro).execute()
        return f"✅ Dorsal {dorsal} -> {estado}"
    except Exception as e:
        return f"❌ Error: {e}"

def obtener_estado_monitor(id_evento, nro_vuelta):
    # Traemos inscripciones y arribos para cruzar datos
    ins = supabase.table("inscripciones").select("dorsal, atletas(nombre, apellido, asistente)").eq("id_evento", id_evento).execute()
    arr = supabase.table("vueltas_vivo").select("dorsal").eq("id_evento", id_evento).eq("nro_vuelta", nro_vuelta).execute()
    fuera = supabase.table("vueltas_vivo").select("dorsal").eq("id_evento", id_evento).neq("estado", "ACT").execute()
    
    dorsales_arribados = {a['dorsal'] for a in arr.data}
    dorsales_fuera = {f['dorsal'] for f in fuera.data}
    
    faltantes = []
    for i in ins.data:
        d = i['dorsal']
        if d not in dorsales_arribados and d not in dorsales_fuera:
            faltantes.append(f"Dorsal {d} - {i['atletas']['nombre']} {i['atletas']['apellido']} (Asistente: {i['atletas']['asistente']})")
    
    total_inscriptos = len(ins.data)
    en_circuito = len(faltantes)
    return faltantes, total_inscriptos, en_circuito

# 4. Carga de Evento Activo
res_evento = supabase.table("eventos").select("*").eq("estado", "en_vivo").maybe_single().execute()

if not res_evento.data:
    st.error("No hay evento 'en_vivo' en Supabase.")
    st.stop()

evento = res_evento.data
ID_EVENTO = evento['id_evento']
patio, crono, seg_restantes, alerta_msg = calcular_seguimiento_carrera(evento['hora_cero'])

# --- INTERFAZ DE CONSOLA ---
st.title(f"⏱️ Panel de Control: {evento['nombre']}")
st.subheader(f"📍 {evento['lugar']} | {alerta_msg}")

# SECCIÓN A: MÉTRICAS DE TIEMPO
c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Patio Actual", patio)
with c2:
    # Color inverso (rojo) si faltan menos de 3 minutos para la campana
    st_color = "inverse" if seg_restantes <= 180 else "normal"
    st.metric("Tiempo para Campana", crono, delta_color=st_color)
with c3:
    faltantes_lista, total, en_pista = obtener_estado_monitor(ID_EVENTO, patio)
    st.metric("En Circuito", f"{en_pista} / {total}")

# SECCIÓN B: MONITOR DE SEGURIDAD
if en_pista > 0:
    st.subheader("🚨 Atletas en Circuito (Faltan Arribar)")
    for f in faltantes_lista:
        st.warning(f)

# SECCIÓN C: REGISTRO RÁPIDO (Scanner)
st.divider()
col_in, col_btn = st.columns([3, 1])
with col_in:
    entrada = st.text_input("LECTURA DE HARDWARE (Dorsal):", key="scanner", placeholder="Scan o tipeo...")
with col_btn:
    if st.button("Registrar Arribo", use_container_width=True):
        if entrada:
            res = registrar_suceso(ID_EVENTO, int(entrada), patio, "ACT")
            st.toast(res)
            st.rerun()

# SECCIÓN D: NOVEDADES MANUALES
st.subheader("📝 Novedades del Director")
ins_data = supabase.table("inscripciones").select("dorsal, atletas(nombre, apellido)").eq("id_evento", ID_EVENTO).execute()
opciones = [f"{c['dorsal']} - {c['atletas']['nombre']} {c['atletas']['apellido']}" for c in ins_data.data]
selec = st.selectbox("Seleccionar Atleta:", opciones)
dorsal_id = int(selec.split(" - ")[0])

btn1, btn2, btn3, btn4 = st.columns(4)
with btn1:
    if st.button("❌ RTC", help="Retire To Camp", use_container_width=True):
        st.toast(registrar_suceso(ID_EVENTO, dorsal_id, patio, "DNF (RTC)"))
with btn2:
    if st.button("⚠️ INC", help="Incomplete Lap", use_container_width=True):
        st.toast(registrar_suceso(ID_EVENTO, dorsal_id, patio, "DNF (INC)"))
with btn3:
    if st.button("🚫 DQ", help="Disqualified", use_container_width=True):
        st.toast(registrar_suceso(ID_EVENTO, dorsal_id, patio, "DNF (DQ)"))
with btn4:
    if st.button("🏆 WINNER", type="primary", use_container_width=True):
        st.balloons()
        st.success(registrar_suceso(ID_EVENTO, dorsal_id, patio, "WINNER"))
