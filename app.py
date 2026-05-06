import streamlit as st
import pandas as pd
from datetime import datetime, timezone, timedelta
from supabase import create_client

# 1. Configuración de Conexión y Página
st.set_page_config(layout="wide", page_title="BACKYARD ULTRA.ar EN VIVO - Consola de Control")
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
        return f"✅ Bib {dorsal} registrado en Patio {nro_vuelta}"
    except Exception as e:
        return f"⚠️ Error: El dorsal {dorsal} no es válido o ya fue registrado."

def obtener_estado_monitor(id_evento, nro_vuelta):
    # 1. Traemos inscripciones: asistente está aquí, y anidamos atletas para el nombre
    query = "dorsal, asistente, atletas:dni_atleta(nombre, apellido)"
    ins = supabase.table("inscripciones").select(query).eq("id_evento", id_evento).execute()
    
    # 2. Traemos arribos y DNF del patio actual
    arr = supabase.table("vueltas_vivo").select("dorsal").eq("id_evento", id_evento).eq("nro_vuelta", nro_vuelta).execute()
    fuera = supabase.table("vueltas_vivo").select("dorsal").eq("id_evento", id_evento).neq("estado", "ACT").execute()
    
    dorsales_arribados = {a['dorsal'] for a in arr.data}
    dorsales_fuera = {f['dorsal'] for f in fuera.data}
    
    faltantes = []
    total_inscriptos = len(ins.data)
    
    for i in ins.data:
        d = i['dorsal']
        # Si no llegó y no está fuera, es un faltante
        if d not in dorsales_arribados and d not in dorsales_fuera:
            # Sacamos el nombre del atleta de la relación anidada
            nombre_completo = f"{i['atletas']['nombre']} {i['atletas']['apellido']}"
            asistente = i['asistente'] if i['asistente'] else "Sin asistente"
            faltantes.append(f"Bib {d} - {nombre_completo} | Asistente: {asistente}")
    
    en_circuito = len(faltantes)
    return faltantes, total_inscriptos, en_circuito

# --- AJUSTE PARA MÚLTIPLES EVENTOS EN CONSOLA ---

# 1. Traemos todos los eventos en vivo
res_eventos = supabase.table("eventos").select("*").eq("estado", "en_vivo").execute()
eventos_lista = res_eventos.data

if eventos_lista:
    # 2. Selector para el Director de Carrera
    if len(eventos_lista) > 1:
        nombres_eventos = [e['nombre'] for e in eventos_lista]
        seleccion = st.sidebar.selectbox("🎮 Seleccioná Carrera a Controlar:", nombres_eventos)
        evento = next(e for e in eventos_lista if e['nombre'] == seleccion)
    else:
        evento = eventos_lista[0]

    # 3. Definimos las variables que el resto del código ya usa
    ID_EVENTO = evento['id_evento']
    
    # 4. Cálculo de tiempo (Patio, crono, alertas)
    patio, crono, seg_restantes, alerta_msg = calcular_seguimiento_carrera(evento['hora_cero'])

    # --- INTERFAZ DE CONSOLA ---
    st.title(f"⏱️ Panel de Control: {evento['nombre']}")
    st.subheader(f"📍 {evento['lugar']} | {alerta_msg}")
else:
    st.error("No hay eventos 'en_vivo' para controlar.")
    st.stop()
    
# --- 1. CÁLCULO UNIFICADO ---
# Llamamos a la función UNA SOLA VEZ para toda la página
faltantes_lista, total_starters, en_pista_count = obtener_estado_monitor(ID_EVENTO, patio)

# Calculamos los que REALMENTE están activos (Total - los que ya quedaron fuera)
# Para un Backyard, los 'Activos' son los que salieron a esta vuelta
total_activos = len(faltantes_lista) + (total_starters - en_pista_count) # Lógica simplificada

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
    st.metric("En Circuito", f"{en_pista_count} / {total_activos}")


with st.container(border=True):
    st.subheader("📲 Registro de Arribos")
    # Creamos dos columnas: una ancha para el scan y una angosta para el botón
    # 'vertical_alignment' hace que el botón se alinee al centro del input
    col_input, col_btn = st.columns([3, 1], vertical_alignment="bottom")
    
    with col_input:
        dorsal_scan = st.text_input("Escanear Dorsal o Chip", key="scan_input", placeholder="Ej: 7")
        
    with col_btn:
        if st.button("REGISTRAR ARRIBO", use_container_width=True, type="primary"):
            if dorsal_scan:
                # 1. Llamamos a TU función tal cual la tenés definida
                # Usamos el estado por defecto "ACT" (porque es un arribo normal)
                resultado = registrar_suceso(ID_EVENTO, int(dorsal_scan), patio)
                
                # 2. Lógica de feedback basada en el prefijo que devuelve tu función
                if "✅" in resultado:
                    st.toast(resultado) # Notificación rápida arriba a la derecha
                    st.rerun()          # Refrescamos para que desaparezca de "En Pista"
                else:
                    # Si hubo error (el ⚠️ que devuelve tu except), lo mostramos en rojo
                    st.error(resultado)
            else:
                st.warning("⚠️ Escanée un dorsal primero")

# --- BLOQUE 1: GESTIÓN DE SUCESOS (Tu código actual mejorado) ---
with st.container(border=True):
    st.subheader("📝 Gestión de Sucesos (Manual)")
    
    # Mantenemos tu lógica de carga de atletas
    ins_data = supabase.table("inscripciones").select("dorsal, atletas(nombre, apellido)").eq("id_evento", ID_EVENTO).execute()
    opciones = [f"{c['dorsal']} - {c['atletas']['nombre']} {c['atletas']['apellido']}" for c in ins_data.data]
    selec = st.selectbox("Seleccionar Atleta para novedad:", opciones)
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

# --- BLOQUE 2: MONITOR DE SEGURIDAD (Lo que falta llegar) ---
with st.container(border=True):
    st.subheader("🏃‍♂️ Monitor de Seguridad (En Pista)")
    
    # Aquí usamos la función que arreglamos antes para ver quién falta
    #faltantes, total, en_pista = obtener_estado_monitor(ID_EVENTO, patio)
    
    c_pista, c_total = st.columns(2)
    c_pista.metric("Atletas en Pista", en_pista_count)
    c_total.metric("Total en el Patio", total_activos)
    
    if faltantes_lista:
        for f in faltantes_lista:
            st.warning(f)
    else:
        st.success("✅ ¡Patio Completo!")
